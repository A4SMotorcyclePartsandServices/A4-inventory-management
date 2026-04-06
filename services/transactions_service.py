from db.database import get_db
from datetime import datetime, timedelta
import json
import re
from psycopg2 import errors as pg_errors
from utils.formatters import format_date
from utils.timezone import now_local, now_local_naive, now_local_str, today_local
from services.loyalty_service import log_stamps_for_sale
from services.approval_service import (
    approve_request,
    cancel_request,
    create_approval_request,
    get_approval_request_by_entity,
    get_approval_request_with_history,
    request_revisions,
    resubmit_request,
)
from services.notification_service import (
    archive_notifications,
    create_notification,
    create_notifications_for_users,
    list_active_user_ids,
)
from services.payables_service import ensure_payable_for_po_receipt


def _build_where_clause(conditions):
    if not conditions:
        return ""
    return " WHERE " + " AND ".join(conditions)


# ─────────────────────────────────────────────
# CORE LEDGER
# ─────────────────────────────────────────────

def add_transaction(item_id, quantity, transaction_type, user_id=None, user_name=None,
                    reference_id=None, reference_type=None, change_reason=None,
                    unit_price=None, transaction_date=None, external_conn=None, notes=None):
    """
    The Universal Ledger Entry.
    Handles logging and stock updates.

    NOTE (future branches): when branch_id is added, pass it here.
    Do not hardcode branch assumptions.
    """
    conn = external_conn if external_conn else get_db()

    if transaction_date:
        final_time = transaction_date.replace('T', ' ')
        if len(final_time) == 16:
            final_time += ":00"
    else:
        final_time = now_local_str()

    conn.execute("""
        INSERT INTO inventory_transactions 
        (item_id, quantity, transaction_type, transaction_date, user_id, user_name, 
        reference_id, reference_type, change_reason, unit_price, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        item_id, quantity, transaction_type, final_time, user_id, user_name,
        reference_id, reference_type, change_reason, unit_price, notes
    ))

    if not external_conn:
        conn.commit()
        conn.close()


# ─────────────────────────────────────────────
# ITEMS
# ─────────────────────────────────────────────

def add_item_to_db(data, user_id=None, username=None):
    """Saves a brand new product to the items table and logs an audit entry."""
    conn = get_db()
    try:
        conn.execute("BEGIN")
        vendor_id = data.get("vendor_id")
        if vendor_id in ("", None):
            raise ValueError("Vendor is required.")

        try:
            vendor_id = int(vendor_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid vendor selected.")

        vendor_exists = conn.execute(
            "SELECT id FROM vendors WHERE id = %s AND is_active = 1",
            (vendor_id,),
        ).fetchone()
        if not vendor_exists:
            raise ValueError("Selected vendor was not found or is inactive.")

        row = conn.execute("""
            INSERT INTO items (
                name, category, description, pack_size, 
                vendor_price, cost_per_piece, a4s_selling_price, 
                markup, reorder_level, vendor, vendor_id, mechanic
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
        """, (
            data['name'], data['category'], data['description'], data['pack_size'],
            data['vendor_price'], data['cost_per_piece'], data['selling_price'],
            data['markup'], data.get('reorder_level', 0), None, vendor_id, data['mechanic']
        )).fetchone()

        new_id = row["id"]

        add_transaction(
            item_id=new_id,
            quantity=0,
            transaction_type='IN',
            user_id=user_id,
            user_name=username,
            reference_id=new_id,
            reference_type='ITEM_CATALOG',
            change_reason='ITEM_CREATED',
            unit_price=data['selling_price'],
            external_conn=conn
        )

        conn.commit()
    except pg_errors.UniqueViolation as exc:
        conn.rollback()
        raise ValueError("Another item already uses that name.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return new_id


def normalize_item_category(existing_cat, new_cat):
    """
    Resolves the final category string from the two form fields.
    - If existing selected: use it as-is (already canonical from DB).
    - If new typed: check DB for a case-variant match and use that instead.
    - Returns None if nothing selected (route should guard against this).
    """
    if existing_cat == "__OTHER__" and new_cat:
        conn = get_db()
        match = conn.execute(
            "SELECT category FROM items WHERE LOWER(TRIM(category)) = %s LIMIT 1",
            (new_cat.lower(),)
        ).fetchone()
        conn.close()
        return match['category'] if match else new_cat
    elif existing_cat and existing_cat != "__OTHER__":
        return existing_cat
    return None


def _calculate_markup_decimal(cost_per_piece, selling_price):
    cost = round(float(cost_per_piece or 0), 4)
    selling = round(float(selling_price or 0), 4)
    if cost <= 0 or selling <= 0:
        return 0.0
    return round((selling - cost) / cost, 4)


def _update_item_cost_and_markup(conn, item_id, new_cost):
    item_row = conn.execute(
        "SELECT cost_per_piece, a4s_selling_price FROM items WHERE id = %s",
        (item_id,),
    ).fetchone()
    current_master_cost = float(item_row["cost_per_piece"] or 0) if item_row else 0.0
    selling_price = float(item_row["a4s_selling_price"] or 0) if item_row else 0.0
    recalculated_markup = _calculate_markup_decimal(new_cost, selling_price)

    conn.execute(
        "UPDATE items SET cost_per_piece = %s, markup = %s WHERE id = %s",
        (new_cost, recalculated_markup, item_id),
    )
    return current_master_cost, recalculated_markup


def _serialize_item_for_edit_row(row):
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": (row["name"] or "").strip(),
        "category": (row["category"] or "").strip(),
        "description": row["description"] or "",
        "pack_size": row["pack_size"] or "",
        "vendor_price": round(float(row["vendor_price"] or 0), 2),
        "cost_per_piece": round(float(row["cost_per_piece"] or 0), 2),
        "a4s_selling_price": round(float(row["a4s_selling_price"] or 0), 2),
        "markup": round(float(row["markup"] or 0), 4),
        "reorder_level": int(row["reorder_level"] or 0),
        "vendor_id": int(row["vendor_id"]) if row["vendor_id"] is not None else None,
        "vendor_name": row["vendor_name"] or "",
        "mechanic": row["mechanic"] or "",
        "updated_at": row["updated_at"],
    }


def _get_item_for_edit(conn, item_id):
    row = conn.execute(
        """
        SELECT
            i.id,
            i.name,
            i.category,
            i.description,
            i.pack_size,
            i.vendor_price,
            i.cost_per_piece,
            i.a4s_selling_price,
            i.markup,
            i.reorder_level,
            i.vendor_id,
            i.mechanic,
            i.updated_at,
            COALESCE(v.vendor_name, i.vendor, '') AS vendor_name
        FROM items i
        LEFT JOIN vendors v ON v.id = i.vendor_id
        WHERE i.id = %s
        """,
        (item_id,),
    ).fetchone()
    return _serialize_item_for_edit_row(row)


def get_item_edit_context(item_id, history_limit=20):
    conn = get_db()
    try:
        item = _get_item_for_edit(conn, item_id)
        if not item:
            return None

        history_rows = conn.execute(
            """
            SELECT
                h.id,
                h.changed_at,
                h.changed_by,
                h.changed_by_username,
                h.change_reason,
                h.before_payload,
                h.after_payload
            FROM item_edit_history h
            WHERE h.item_id = %s
            ORDER BY h.changed_at DESC, h.id DESC
            LIMIT %s
            """,
            (item_id, history_limit),
        ).fetchall()
    finally:
        conn.close()

    history = []
    for row in history_rows:
        before_payload = row["before_payload"] or {}
        after_payload = row["after_payload"] or {}
        if isinstance(before_payload, str):
            before_payload = json.loads(before_payload or "{}")
        if isinstance(after_payload, str):
            after_payload = json.loads(after_payload or "{}")
        changed_fields = []
        for field_name in (
            "name",
            "category",
            "description",
            "pack_size",
            "vendor_price",
            "cost_per_piece",
            "a4s_selling_price",
            "markup",
            "reorder_level",
            "vendor_id",
            "vendor_name",
            "mechanic",
        ):
            if before_payload.get(field_name) != after_payload.get(field_name):
                changed_fields.append(field_name)

        history.append({
            "id": int(row["id"]),
            "changed_at": format_date(row["changed_at"], show_time=True),
            "changed_by_username": row["changed_by_username"] or "System",
            "change_reason": row["change_reason"] or "",
            "before_payload": before_payload,
            "after_payload": after_payload,
            "changed_fields": changed_fields,
        })

    return {
        "item": item,
        "history": history,
    }


def update_item_record(item_id, data, user_id=None, username=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        current_item = _get_item_for_edit(conn, item_id)
        if not current_item:
            raise ValueError("Item not found.")

        existing_cat = str((data or {}).get("existing_category") or "").strip()
        new_cat = str((data or {}).get("new_category") or "").strip()
        category = normalize_item_category(existing_cat, new_cat)

        name = str((data or {}).get("name") or "").strip()
        if not name or not category:
            raise ValueError("Item name and category are required.")

        try:
            vendor_price = round(float((data or {}).get("vendor_price") or 0), 2)
            cost_per_piece = round(float((data or {}).get("cost_per_piece") or 0), 2)
            selling_price = round(float((data or {}).get("a4s_selling_price") or 0), 2)
        except (TypeError, ValueError):
            raise ValueError("Vendor price, cost per piece, and selling price must be valid numbers.")

        if vendor_price < 0 or cost_per_piece < 0 or selling_price < 0:
            raise ValueError("Pricing values cannot be negative.")

        reorder_level_raw = (data or {}).get("reorder_level")
        if reorder_level_raw in (None, ""):
            reorder_level = int(current_item.get("reorder_level") or 0)
        else:
            try:
                reorder_level = int(reorder_level_raw)
            except (TypeError, ValueError):
                raise ValueError("Reorder level must be a whole number.")
            if reorder_level < 0:
                raise ValueError("Reorder level cannot be negative.")

        vendor_id_raw = (data or {}).get("vendor_id")
        if vendor_id_raw in ("", None):
            raise ValueError("Vendor is required.")
        try:
            vendor_id = int(vendor_id_raw)
        except (TypeError, ValueError):
            raise ValueError("Invalid vendor selected.")

        vendor_row = conn.execute(
            """
            SELECT id, vendor_name
            FROM vendors
            WHERE id = %s AND is_active = 1
            """,
            (vendor_id,),
        ).fetchone()
        if not vendor_row:
            raise ValueError("Selected vendor was not found or is inactive.")

        duplicate_name = conn.execute(
            """
            SELECT id
            FROM items
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))
              AND id <> %s
            LIMIT 1
            """,
            (name, item_id),
        ).fetchone()
        if duplicate_name:
            raise ValueError("Another item already uses that name.")

        updated_item = {
            "id": int(item_id),
            "name": name,
            "category": category,
            "description": str((data or {}).get("description") or "").strip(),
            "pack_size": str((data or {}).get("pack_size") or "").strip(),
            "vendor_price": vendor_price,
            "cost_per_piece": cost_per_piece,
            "a4s_selling_price": selling_price,
            "markup": _calculate_markup_decimal(cost_per_piece, selling_price),
            "reorder_level": reorder_level,
            "vendor_id": vendor_id,
            "vendor_name": vendor_row["vendor_name"] or "",
            "mechanic": str((data or {}).get("mechanic") or "").strip(),
            "updated_at": current_item.get("updated_at"),
        }

        tracked_fields = (
            "name",
            "category",
            "description",
            "pack_size",
            "vendor_price",
            "cost_per_piece",
            "a4s_selling_price",
            "markup",
            "reorder_level",
            "vendor_id",
            "vendor_name",
            "mechanic",
        )
        changed_fields = [
            field_name
            for field_name in tracked_fields
            if current_item.get(field_name) != updated_item.get(field_name)
        ]
        if not changed_fields:
            raise ValueError("No changes detected.")

        change_reason = str((data or {}).get("change_reason") or "").strip()
        if not change_reason:
            raise ValueError("Reason for edit is required.")

        conn.execute(
            """
            UPDATE items
            SET
                name = %s,
                category = %s,
                description = %s,
                pack_size = %s,
                vendor_price = %s,
                cost_per_piece = %s,
                a4s_selling_price = %s,
                markup = %s,
                reorder_level = %s,
                vendor_id = %s,
                vendor = NULL,
                mechanic = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                updated_item["name"],
                updated_item["category"],
                updated_item["description"] or None,
                updated_item["pack_size"] or None,
                updated_item["vendor_price"],
                updated_item["cost_per_piece"],
                updated_item["a4s_selling_price"],
                updated_item["markup"],
                updated_item["reorder_level"],
                updated_item["vendor_id"],
                updated_item["mechanic"] or None,
                item_id,
            ),
        )

        conn.execute(
            """
            INSERT INTO item_edit_history (
                item_id,
                changed_by,
                changed_by_username,
                change_reason,
                before_payload,
                after_payload
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                item_id,
                user_id,
                username,
                change_reason,
                json.dumps({field: current_item.get(field) for field in tracked_fields}),
                json.dumps({field: updated_item.get(field) for field in tracked_fields}),
            ),
        )

        conn.commit()
        return _get_item_for_edit(conn, item_id)
    except pg_errors.UniqueViolation as exc:
        conn.rollback()
        raise ValueError("Another item already uses that name.") from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# TRANSACTION OUT PAGE DATA
# ─────────────────────────────────────────────

def get_transaction_out_context():
    """
    Fetches everything the transaction OUT page needs to render.
    NOTE (future branches): add branch_id filter to all queries here.
    """
    conn = get_db()

    payment_methods = conn.execute("""
        SELECT id, name, category
        FROM payment_methods
        WHERE is_active = 1
        ORDER BY category ASC, name ASC
    """).fetchall()

    cash_pm = conn.execute("""
        SELECT id FROM payment_methods
        WHERE category = 'Cash' AND is_active = 1
        ORDER BY id ASC LIMIT 1
    """).fetchone()

    debt_pm = conn.execute("""
        SELECT id FROM payment_methods
        WHERE category = 'Debt' AND is_active = 1
        ORDER BY id ASC LIMIT 1
    """).fetchone()

    others_pm = conn.execute("""
        SELECT id FROM payment_methods
        WHERE category = 'Others' AND is_active = 1
        ORDER BY id ASC LIMIT 1
    """).fetchone()

    mechanics = conn.execute("""
        SELECT id, name FROM mechanics
        WHERE is_active = 1
    """).fetchall()

    conn.close()

    return {
        "payment_methods": payment_methods,
        "mechanics": mechanics,
        "cash_pm_id": cash_pm["id"] if cash_pm else None,
        "debt_pm_id": debt_pm["id"] if debt_pm else None,
        "others_pm_id": others_pm["id"] if others_pm else None,
    }


def get_active_bundles_for_sale():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                b.id,
                b.name,
                b.vehicle_category,
                cv.id AS bundle_version_id,
                cv.version_no
            FROM bundles b
            JOIN bundle_versions cv
              ON cv.bundle_id = b.id
             AND cv.is_current = 1
            WHERE b.is_active = 1
            ORDER BY b.name ASC, b.vehicle_category ASC
            """
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": row["name"],
                "vehicle_category": row["vehicle_category"],
                "bundle_version_id": int(row["bundle_version_id"]),
                "version_no": int(row["version_no"] or 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_bundle_sale_config(bundle_id):
    conn = get_db()
    try:
        bundle = conn.execute(
            """
            SELECT
                b.id,
                b.name,
                b.vehicle_category,
                cv.id AS bundle_version_id,
                cv.version_no
            FROM bundles b
            JOIN bundle_versions cv
              ON cv.bundle_id = b.id
             AND cv.is_current = 1
            WHERE b.id = %s
              AND b.is_active = 1
            LIMIT 1
            """,
            (bundle_id,),
        ).fetchone()
        if not bundle:
            raise ValueError("Bundle not found or inactive.")

        bundle_version_id = int(bundle["bundle_version_id"])
        variants = conn.execute(
            """
            SELECT
                id,
                subcategory_name,
                item_value_reference,
                shop_share,
                mechanic_share,
                sale_price,
                sort_order
            FROM bundle_version_variants
            WHERE bundle_version_id = %s
            ORDER BY sort_order ASC, id ASC
            """,
            (bundle_version_id,),
        ).fetchall()
        services = conn.execute(
            """
            SELECT
                bvs.service_id,
                sv.name,
                sv.category,
                bvs.sort_order
            FROM bundle_version_services bvs
            JOIN services sv ON sv.id = bvs.service_id
            WHERE bvs.bundle_version_id = %s
            ORDER BY bvs.sort_order ASC, bvs.id ASC
            """,
            (bundle_version_id,),
        ).fetchall()
        items = conn.execute(
            """
            SELECT
                bvi.item_id,
                i.name,
                i.category,
                bvi.quantity,
                bvi.sort_order,
                COALESCE((
                    SELECT SUM(
                        CASE
                            WHEN it.transaction_type = 'IN' THEN it.quantity
                            WHEN it.transaction_type = 'OUT' THEN -it.quantity
                            ELSE 0
                        END
                    )
                    FROM inventory_transactions it
                    WHERE it.item_id = i.id
                ), 0) AS current_stock
            FROM bundle_version_items bvi
            JOIN items i ON i.id = bvi.item_id
            WHERE bvi.bundle_version_id = %s
            ORDER BY bvi.sort_order ASC, bvi.id ASC
            """,
            (bundle_version_id,),
        ).fetchall()
    finally:
        conn.close()

    return {
        "bundle_id": int(bundle["id"]),
        "name": bundle["name"],
        "vehicle_category": bundle["vehicle_category"],
        "bundle_version_id": bundle_version_id,
        "version_no": int(bundle["version_no"] or 0),
        "variants": [
            {
                "variant_id": int(row["id"]),
                "subcategory_name": row["subcategory_name"],
                "item_value_reference": float(row["item_value_reference"] or 0),
                "shop_share": float(row["shop_share"] or 0),
                "mechanic_share": float(row["mechanic_share"] or 0),
                "sale_price": float(row["sale_price"] or 0),
            }
            for row in variants
        ],
        "services": [
            {
                "service_id": int(row["service_id"]),
                "name": row["name"],
                "category": row["category"],
            }
            for row in services
        ],
        "items": [
            {
                "item_id": int(row["item_id"]),
                "name": row["name"],
                "category": row["category"],
                "quantity": int(row["quantity"] or 0),
                "current_stock": int(row["current_stock"] or 0),
            }
            for row in items
        ],
    }


# ─────────────────────────────────────────────
# MANUAL STOCK IN
# ─────────────────────────────────────────────

def process_manual_stock_in(item_id, qty_int, unit_price, notes, user_id, username):
    """
    Records a manual stock IN with cost self-correction.
    Raises ValueError for invalid inputs.
    NOTE (future branches): pass branch_id when ready.
    """
    if qty_int <= 0:
        raise ValueError("Invalid quantity. Must be at least 1.")
    if unit_price <= 0:
        raise ValueError("Invalid unit cost. Must be greater than 0.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        clean_time = now_local_str()

        # 1) Log the manual IN
        add_transaction(
            item_id=item_id,
            quantity=qty_int,
            transaction_type='IN',
            user_id=user_id,
            user_name=username,
            reference_id=None,
            reference_type='MANUAL_ADJUSTMENT',
            change_reason='WALKIN_PURCHASE',
            unit_price=unit_price,
            notes=notes,
            transaction_date=clean_time,
            external_conn=conn
        )

        # 2) Cost self-correction + audit
        item_row = conn.execute(
            "SELECT cost_per_piece FROM items WHERE id = %s", (item_id,)
        ).fetchone()
        current_master_cost = float(item_row["cost_per_piece"] or 0) if item_row else 0.0

        if unit_price != current_master_cost:
            current_master_cost, _ = _update_item_cost_and_markup(conn, item_id, unit_price)
            add_transaction(
                item_id=item_id,
                quantity=0,
                transaction_type='IN',
                user_id=user_id,
                user_name=username,
                reference_id=None,
                reference_type='MANUAL_ADJUSTMENT',
                change_reason='COST_PER_PIECE_UPDATED',
                unit_price=unit_price,
                notes=f"Cost updated from {current_master_cost:.2f} to {unit_price:.2f}. Reason: {notes}",
                transaction_date=clean_time,
                external_conn=conn
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# RECORD SALE
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# PURCHASE ORDERS
# ─────────────────────────────────────────────

# Single source of truth for all OUT sale saves, including quick sales,
# regular sales, mechanic supply, services, and bundle-aware sales.
def record_sale(data, user_id, username):
    """
    Records a full sale that may contain standalone items, services,
    and at most one bundle.
    """
    try:
        payment_method_id = int(data.get("payment_method_id"))
    except (TypeError, ValueError):
        raise ValueError("Invalid payment method selected.")

    requested_transaction_class = str(data.get("transaction_class") or "").strip().upper()
    raw_items = data.get("items", []) or []
    raw_services = data.get("services", []) or []
    raw_bundles = data.get("bundles", []) or []
    raw_customer_name = str(data.get("customer_name") or "").strip()
    raw_sales_number = str(data.get("sales_number") or "").strip()
    raw_mechanic_id = data.get("mechanic_id")

    if not requested_transaction_class:
        requested_transaction_class = "QUICK_SALE" if bool(data.get("quick_sale")) else "NEW_SALE"

    if (
        requested_transaction_class == "NEW_SALE"
        and raw_mechanic_id
        and not raw_customer_name
        and not raw_sales_number
        and not raw_services
        and not raw_bundles
    ):
        requested_transaction_class = "MECHANIC_SUPPLY"

    valid_transaction_classes = {"QUICK_SALE", "NEW_SALE", "MECHANIC_SUPPLY"}
    if requested_transaction_class not in valid_transaction_classes:
        raise ValueError("Invalid transaction class selected.")

    quick_sale = requested_transaction_class == "QUICK_SALE"
    mechanic_supply = requested_transaction_class == "MECHANIC_SUPPLY"

    conn = get_db()
    try:
        pm = conn.execute(
            """
            SELECT id, category, is_active
            FROM payment_methods WHERE id = %s
            """,
            (payment_method_id,),
        ).fetchone()
        if not pm or pm["is_active"] != 1:
            raise ValueError("Invalid or inactive payment method selected.")

        payment_category = (pm["category"] or "").strip()
        if mechanic_supply and payment_category != "Cash":
            raise ValueError("Mechanic Supply must use a cash payment method.")
        sale_status = "Unresolved" if payment_category == "Debt" else "Paid"

        now_obj = now_local()
        raw_date = data.get("transaction_date")
        current_minute = now_obj.strftime("%Y-%m-%d %H:%M")
        if raw_date:
            clean_time = raw_date.replace("T", " ")
            if clean_time[:16] == current_minute:
                clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")
            elif len(clean_time) == 16:
                clean_time += ":00"
        else:
            clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")

        seen_item_ids = set()
        duplicate_item_ids = set()
        for item in raw_items:
            raw_item_id = item.get("item_id")
            if raw_item_id in (None, ""):
                continue
            item_id_str = str(raw_item_id).strip()
            if not item_id_str:
                continue
            if item_id_str in seen_item_ids:
                duplicate_item_ids.add(item_id_str)
            seen_item_ids.add(item_id_str)

        if duplicate_item_ids:
            placeholders = ",".join(["%s"] * len(duplicate_item_ids))
            items_data = conn.execute(
                f"SELECT id, name FROM items WHERE id IN ({placeholders})",
                tuple(duplicate_item_ids),
            ).fetchall()
            labels = [f"{row['name']} (ID {row['id']})" for row in items_data]
            raise ValueError(f"Duplicate item(s) detected: {', '.join(labels)}. Please adjust Qty Out instead.")

        normalized_items = []
        if raw_items:
            item_ids = []
            for item in raw_items:
                try:
                    item_ids.append(int(item.get("item_id")))
                except (TypeError, ValueError):
                    raise ValueError("One or more selected items are invalid.")

            placeholders = ",".join(["%s"] * len(item_ids))
            item_rows = conn.execute(
                f"""
                SELECT id, name, a4s_selling_price, cost_per_piece
                FROM items
                WHERE id IN ({placeholders})
                """,
                tuple(item_ids),
            ).fetchall()
            item_catalog = {int(row["id"]): dict(row) for row in item_rows}
            missing_item_ids = [item_id for item_id in item_ids if item_id not in item_catalog]
            if missing_item_ids:
                raise ValueError("One or more selected items no longer exist.")

            for item in raw_items:
                item_id = int(item.get("item_id"))
                item_row = item_catalog[item_id]
                try:
                    quantity = int(item.get("quantity", 0) or 0)
                except (TypeError, ValueError):
                    raise ValueError("One or more item quantities are invalid.")
                if quantity <= 0:
                    raise ValueError("Item quantities must be at least 1.")

                cost_per_piece_snapshot = round(float(item_row.get("cost_per_piece") or 0), 2)
                master_price = round(
                    cost_per_piece_snapshot if mechanic_supply else float(item_row.get("a4s_selling_price") or 0),
                    2,
                )
                try:
                    discount_amount = round(float(item.get("discount_amount", 0) or 0), 2)
                except (TypeError, ValueError):
                    raise ValueError("One or more item discounts are invalid.")
                max_discount_amount = 0.0 if mechanic_supply else round(master_price * 0.5, 2)
                if discount_amount < 0 or discount_amount > max_discount_amount:
                    raise ValueError("Item discount cannot exceed 50% of the selling price.")

                discount_percent_whole = 0.0
                if not mechanic_supply and master_price > 0:
                    discount_percent_whole = round((discount_amount / master_price) * 100, 2)

                if mechanic_supply and discount_amount != 0:
                    raise ValueError("Mechanic Supply item discounts are not allowed.")
                submitted_original_price = round(float(item.get("original_price", 0) or 0), 2)
                submitted_final_price = round(float(item.get("final_price", 0) or 0), 2)
                expected_final_price = round(
                    master_price if mechanic_supply else (master_price - discount_amount),
                    2,
                )

                if abs(submitted_original_price - master_price) > 0.01:
                    raise ValueError(f"Price mismatch detected for '{item_row['name']}'. Please refresh and try again.")
                if abs(submitted_final_price - expected_final_price) > 0.01:
                    raise ValueError(f"Discounted price mismatch detected for '{item_row['name']}'. Please refresh and try again.")

                normalized_items.append(
                    {
                        "item_id": item_id,
                        "name": item_row["name"],
                        "quantity": quantity,
                        "original_price": master_price,
                        "cost_per_piece_snapshot": cost_per_piece_snapshot,
                        "discount_percent_whole": 0.0 if mechanic_supply else discount_percent_whole,
                        "discount_percent_decimal": 0.0 if mechanic_supply else (discount_percent_whole / 100),
                        "final_price": expected_final_price,
                        "discount_amount": 0.0 if mechanic_supply else discount_amount,
                    }
                )

        normalized_services = []
        if raw_services:
            service_ids = []
            for service in raw_services:
                try:
                    service_ids.append(int(service.get("service_id")))
                except (TypeError, ValueError):
                    raise ValueError("One or more selected services are invalid.")

            placeholders = ",".join(["%s"] * len(service_ids))
            service_rows = conn.execute(
                f"""
                SELECT id, name
                FROM services
                WHERE id IN ({placeholders})
                """,
                tuple(service_ids),
            ).fetchall()
            service_catalog = {int(row["id"]): dict(row) for row in service_rows}
            missing_service_ids = [service_id for service_id in service_ids if service_id not in service_catalog]
            if missing_service_ids:
                raise ValueError("One or more selected services no longer exist.")

            for service in raw_services:
                service_id = int(service.get("service_id"))
                raw_price = service.get("price")
                if raw_price in (None, ""):
                    raise ValueError("Price is required for each selected service.")
                try:
                    price = round(float(raw_price), 2)
                except (TypeError, ValueError):
                    raise ValueError("Invalid service price. Please enter a valid amount.")
                if price < 0:
                    raise ValueError("Service price cannot be negative.")

                normalized_services.append(
                    {
                        "service_id": service_id,
                        "name": service_catalog[service_id]["name"],
                        "price": price,
                    }
                )

        normalized_bundle = None
        if raw_bundles:
            if len(raw_bundles) > 1:
                raise ValueError("Only one bundle is allowed per sale.")

            submitted_bundle = raw_bundles[0] or {}
            try:
                bundle_id = int(submitted_bundle.get("bundle_id"))
                bundle_version_id = int(submitted_bundle.get("bundle_version_id"))
                bundle_variant_id = int(submitted_bundle.get("bundle_variant_id"))
            except (TypeError, ValueError):
                raise ValueError("Selected bundle is invalid. Please reselect the bundle.")

            bundle_row = conn.execute(
                """
                SELECT
                    b.id AS bundle_id,
                    b.name AS bundle_name,
                    b.vehicle_category,
                    bv.id AS bundle_version_id,
                    bv.version_no,
                    bvv.id AS bundle_variant_id,
                    bvv.subcategory_name,
                    bvv.item_value_reference,
                    bvv.shop_share,
                    bvv.mechanic_share,
                    bvv.sale_price
                FROM bundles b
                JOIN bundle_versions bv ON bv.bundle_id = b.id
                JOIN bundle_version_variants bvv ON bvv.bundle_version_id = bv.id
                WHERE b.id = %s
                  AND bv.id = %s
                  AND bvv.id = %s
                LIMIT 1
                """,
                (bundle_id, bundle_version_id, bundle_variant_id),
            ).fetchone()
            if not bundle_row:
                raise ValueError("Selected bundle configuration is no longer valid. Please reselect the bundle.")

            bundle_service_rows = conn.execute(
                """
                SELECT
                    bvs.service_id,
                    sv.name,
                    bvs.sort_order
                FROM bundle_version_services bvs
                JOIN services sv ON sv.id = bvs.service_id
                WHERE bvs.bundle_version_id = %s
                ORDER BY bvs.sort_order ASC, bvs.id ASC
                """,
                (bundle_version_id,),
            ).fetchall()
            bundle_item_rows = conn.execute(
                """
                SELECT
                    bvi.item_id,
                    i.name,
                    bvi.quantity,
                    bvi.sort_order,
                    i.a4s_selling_price,
                    i.cost_per_piece
                FROM bundle_version_items bvi
                JOIN items i ON i.id = bvi.item_id
                WHERE bvi.bundle_version_id = %s
                ORDER BY bvi.sort_order ASC, bvi.id ASC
                """,
                (bundle_version_id,),
            ).fetchall()

            bundle_item_flags = {}
            for submitted_item in submitted_bundle.get("items", []) or []:
                try:
                    item_id = int(submitted_item.get("item_id"))
                except (TypeError, ValueError):
                    raise ValueError("One or more bundle items are invalid.")
                if item_id in bundle_item_flags:
                    raise ValueError("Duplicate bundle items detected. Please refresh and try again.")
                bundle_item_flags[item_id] = bool(submitted_item.get("is_included", True))

            normalized_bundle = {
                "bundle_id": int(bundle_row["bundle_id"]),
                "bundle_name": bundle_row["bundle_name"],
                "vehicle_category": bundle_row["vehicle_category"],
                "bundle_version_id": int(bundle_row["bundle_version_id"]),
                "bundle_version_no": int(bundle_row["version_no"] or 0),
                "bundle_variant_id": int(bundle_row["bundle_variant_id"]),
                "subcategory_name": bundle_row["subcategory_name"],
                "item_value_reference": round(float(bundle_row["item_value_reference"] or 0), 2),
                "shop_share": round(float(bundle_row["shop_share"] or 0), 2),
                "mechanic_share": round(float(bundle_row["mechanic_share"] or 0), 2),
                "sale_price": round(float(bundle_row["sale_price"] or 0), 2),
                "services": [
                    {
                        "service_id": int(row["service_id"]),
                        "name": row["name"],
                        "sort_order": int(row["sort_order"] or 0),
                    }
                    for row in bundle_service_rows
                ],
                "items": [
                    {
                        "item_id": int(row["item_id"]),
                        "name": row["name"],
                        "quantity": int(row["quantity"] or 0),
                        "sort_order": int(row["sort_order"] or 0),
                        "is_included": bundle_item_flags.get(int(row["item_id"]), True),
                        "original_price": round(float(row["a4s_selling_price"] or 0), 2),
                        "cost_per_piece_snapshot": round(float(row["cost_per_piece"] or 0), 2),
                    }
                    for row in bundle_item_rows
                ],
            }

        if not (normalized_items or normalized_services or normalized_bundle):
            raise ValueError("Please add at least one item, service, or bundle before submitting.")

        if normalized_bundle and not data.get("mechanic_id"):
            raise ValueError("Bundle sales require a selected mechanic before submitting.")

        if quick_sale:
            if raw_bundles:
                raise ValueError("Quick Sale does not support bundle entries.")
            customer_name = str(data.get("customer_name") or "").strip()
            if not customer_name:
                raise ValueError("Quick Sale requires a customer name.")
            if not (raw_items or raw_services):
                raise ValueError("Quick Sale requires at least one item or service.")

            if data.get("mechanic_id") and not raw_services:
                raise ValueError("Assigned mechanic requires at least one service entry.")

            computed_quick_total = 0.0
            for item in normalized_items:
                master_price = float(item["original_price"] or 0)
                final_price = float(item["final_price"] or 0)
                quantity = int(item["quantity"] or 0)
                if master_price > 500:
                    raise ValueError(f"'{item['name']}' is not eligible for Quick Sale because its catalog price is above 500 pesos.")
                if final_price > 500:
                    raise ValueError("Quick Sale only allows items priced up to 500 pesos.")
                computed_quick_total += quantity * final_price

            for service in normalized_services:
                computed_quick_total += float(service["price"] or 0)

            if round(computed_quick_total, 2) > 500:
                raise ValueError("Quick Sale total due cannot exceed 500 pesos.")
        elif mechanic_supply:
            if raw_services:
                raise ValueError("Mechanic Supply only supports item stock-outs.")
            if raw_bundles:
                raise ValueError("Mechanic Supply does not support bundle entries.")
            if not data.get("mechanic_id"):
                raise ValueError("Mechanic Supply requires an assigned mechanic.")
            if not raw_items:
                raise ValueError("Mechanic Supply requires at least one item.")

        conn.execute("BEGIN")
        sale_total_amount = 0.0

        vehicle_id = None if mechanic_supply else data.get("vehicle_id")
        if vehicle_id in ("", None):
            vehicle_id = None
        else:
            try:
                vehicle_id = int(vehicle_id)
            except (TypeError, ValueError):
                vehicle_id = None

        if vehicle_id is not None and data.get("customer_id"):
            valid_vehicle = conn.execute(
                "SELECT id FROM vehicles WHERE id = %s AND customer_id = %s AND is_active = 1",
                (vehicle_id, data.get("customer_id")),
            ).fetchone()
            if not valid_vehicle:
                raise ValueError("Invalid vehicle selected for this customer.")

        stock_requirements = {}
        stock_name_lookup = {}
        for item in normalized_items:
            stock_requirements[item["item_id"]] = stock_requirements.get(item["item_id"], 0) + int(item["quantity"])
            stock_name_lookup[item["item_id"]] = item["name"]
        if normalized_bundle:
            for item in normalized_bundle["items"]:
                if not item["is_included"]:
                    continue
                stock_requirements[item["item_id"]] = stock_requirements.get(item["item_id"], 0) + int(item["quantity"])
                stock_name_lookup[item["item_id"]] = item["name"]

        for item_id, requested_quantity in stock_requirements.items():
            stock_row = conn.execute(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'IN' THEN quantity
                        WHEN transaction_type = 'OUT' THEN -quantity
                        ELSE 0
                    END
                ), 0) AS current_stock
                FROM inventory_transactions
                WHERE item_id = %s
                """,
                (item_id,),
            ).fetchone()
            current_stock = int(stock_row["current_stock"] or 0) if stock_row else 0
            if requested_quantity > current_stock:
                item_name = stock_name_lookup.get(item_id, f"Item ID {item_id}")
                raise ValueError(f"Insufficient stock for '{item_name}'. Requested: {requested_quantity}, Available: {current_stock}.")

        submitted_sales_number = str(data.get("sales_number") or "").strip() or None

        sale_row = conn.execute(
            """
            INSERT INTO sales (
                sales_number, customer_name, customer_id, vehicle_id, total_amount,
                payment_method_id, reference_no, status,
                notes, user_id, transaction_date, mechanic_id, transaction_class
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                submitted_sales_number,
                None if mechanic_supply else data.get("customer_name"),
                None if mechanic_supply else (data.get("customer_id") or None),
                vehicle_id,
                sale_total_amount,
                payment_method_id,
                data.get("reference_no"),
                sale_status,
                data.get("notes"),
                user_id,
                clean_time,
                data.get("mechanic_id") or None,
                requested_transaction_class,
            ),
        ).fetchone()
        new_sale_id = sale_row["id"]

        item_total_amount = 0.0
        for item in normalized_items:
            quantity = int(item["quantity"])
            original_price = float(item["original_price"])
            final_price = float(item["final_price"])
            item_total_amount += quantity * final_price

            add_transaction(
                item_id=item["item_id"],
                quantity=quantity,
                transaction_type="OUT",
                user_id=user_id,
                user_name=username,
                reference_id=new_sale_id,
                reference_type="SALE",
                change_reason="MECHANIC_SUPPLY" if mechanic_supply else "CUSTOMER_PURCHASE",
                unit_price=original_price,
                transaction_date=clean_time,
                external_conn=conn,
            )

            conn.execute(
                """
                INSERT INTO sales_items (
                    sale_id, item_id, quantity,
                    original_unit_price, discount_percent, discount_amount, final_unit_price,
                    cost_per_piece_snapshot, discounted_by, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    new_sale_id,
                    item["item_id"],
                    quantity,
                    original_price,
                    float(item["discount_percent_decimal"]),
                    float(item["discount_amount"]),
                    final_price,
                    float(item["cost_per_piece_snapshot"]),
                    None if mechanic_supply else (user_id if float(item["discount_percent_whole"]) > 0 else None),
                    clean_time,
                ),
            )

        service_subtotal = 0.0
        for service in normalized_services:
            service_subtotal += float(service["price"])
            conn.execute(
                """
                INSERT INTO sales_services (sale_id, service_id, price)
                VALUES (%s, %s, %s)
                """,
                (new_sale_id, service["service_id"], service["price"]),
            )

        bundle_total_amount = 0.0
        bundle_service_ids = []
        bundle_included_item_ids = []
        if normalized_bundle:
            bundle_total_amount = float(normalized_bundle["sale_price"])
            sales_bundle_row = conn.execute(
                """
                INSERT INTO sales_bundles (
                    sale_id, bundle_id, bundle_version_id, bundle_variant_id,
                    bundle_name_snapshot, vehicle_category_snapshot, bundle_version_no_snapshot,
                    subcategory_name_snapshot, item_value_reference_snapshot, shop_share_snapshot,
                    mechanic_share_snapshot, bundle_price_snapshot, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    new_sale_id,
                    normalized_bundle["bundle_id"],
                    normalized_bundle["bundle_version_id"],
                    normalized_bundle["bundle_variant_id"],
                    normalized_bundle["bundle_name"],
                    normalized_bundle["vehicle_category"],
                    normalized_bundle["bundle_version_no"],
                    normalized_bundle["subcategory_name"],
                    normalized_bundle["item_value_reference"],
                    normalized_bundle["shop_share"],
                    normalized_bundle["mechanic_share"],
                    normalized_bundle["sale_price"],
                    clean_time,
                ),
            ).fetchone()
            sales_bundle_id = int(sales_bundle_row["id"])

            for service in normalized_bundle["services"]:
                bundle_service_ids.append(service["service_id"])
                conn.execute(
                    """
                    INSERT INTO sales_bundle_services (
                        sales_bundle_id, service_id, service_name_snapshot, sort_order
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (sales_bundle_id, service["service_id"], service["name"], service["sort_order"]),
                )

            for item in normalized_bundle["items"]:
                conn.execute(
                    """
                    INSERT INTO sales_bundle_items (
                        sales_bundle_id, item_id, item_name_snapshot, quantity,
                        cost_per_piece_snapshot, selling_price_snapshot, line_total_snapshot, is_included, sort_order
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        sales_bundle_id,
                        item["item_id"],
                        item["name"],
                        item["quantity"],
                        float(item.get("cost_per_piece_snapshot") or 0),
                        float(item["original_price"]),
                        round(float(item["original_price"]) * int(item["quantity"]), 2) if item["is_included"] else 0.0,
                        1 if item["is_included"] else 0,
                        item["sort_order"],
                    ),
                )

                if not item["is_included"]:
                    continue

                bundle_included_item_ids.append(item["item_id"])
                add_transaction(
                    item_id=item["item_id"],
                    quantity=int(item["quantity"]),
                    transaction_type="OUT",
                    user_id=user_id,
                    user_name=username,
                    reference_id=new_sale_id,
                    reference_type="SALE",
                    change_reason="BUNDLE_PURCHASE",
                    unit_price=float(item["original_price"]),
                    transaction_date=clean_time,
                    external_conn=conn,
                    notes=f"Bundle: {normalized_bundle['bundle_name']} - {normalized_bundle['subcategory_name']}",
                )

        sale_total_amount = round(item_total_amount + service_subtotal + bundle_total_amount, 2)
        conn.execute(
            "UPDATE sales SET total_amount = %s, service_fee = %s WHERE id = %s",
            (sale_total_amount, service_subtotal, new_sale_id),
        )

        service_ids = [service["service_id"] for service in normalized_services] + bundle_service_ids
        item_ids = [item["item_id"] for item in normalized_items] + bundle_included_item_ids
        if not mechanic_supply:
            log_stamps_for_sale(new_sale_id, data.get("customer_id"), service_ids, item_ids, clean_time, conn)

        conn.commit()
        return submitted_sales_number, new_sale_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


REFUND_WINDOW_DAYS = 7


def _normalize_db_timestamp(raw_value=None):
    now_obj = now_local()
    if raw_value:
        clean_time = str(raw_value).replace('T', ' ')
        if len(clean_time) == 16:
            clean_time += ":00"
        return clean_time
    return now_obj.strftime("%Y-%m-%d %H:%M:%S")


def _build_refund_number(conn, sales_number, sale_id, reference_time=None):
    or_number = str(sales_number or sale_id).strip()
    stamp_source = reference_time or now_local_naive()
    stamp = stamp_source.strftime("%m%d")
    base_number = f"RF-{or_number}-{stamp}"

    existing_count_row = conn.execute(
        """
        SELECT COUNT(*) AS existing_count
        FROM sale_refunds
        WHERE refund_number = %s
           OR refund_number LIKE %s
        """,
        (base_number, f"{base_number}-%"),
    ).fetchone()

    existing_count = int(existing_count_row["existing_count"] or 0)
    if existing_count <= 0:
        return base_number
    return f"{base_number}-{existing_count + 1}"


def _derive_refund_state(total_refunded, remaining_qty):
    refunded_amount = round(float(total_refunded or 0), 2)
    remaining_quantity = int(remaining_qty or 0)
    if refunded_amount <= 0:
        return "Not Refunded"
    if remaining_quantity <= 0:
        return "Fully Refunded"
    return "Partially Refunded"


def _get_cash_payment_method_id(conn):
    row = conn.execute(
        """
        SELECT id
        FROM payment_methods
        WHERE category = %s
        ORDER BY id ASC
        LIMIT 1
        """,
        ("Cash",),
    ).fetchone()
    if not row:
        raise ValueError("No cash payment method is configured for exchanges.")
    return int(row["id"])


def _build_exchange_number(conn, sales_number, sale_id):
    or_number = str(sales_number or sale_id).strip()
    base_number = f"SW-{or_number}"

    existing_count_row = conn.execute(
        """
        SELECT COUNT(*) AS existing_count
        FROM sale_exchanges
        WHERE exchange_number = %s
           OR exchange_number LIKE %s
        """,
        (base_number, f"{base_number}-%"),
    ).fetchone()

    existing_count = int(existing_count_row["existing_count"] or 0)
    if existing_count <= 0:
        return base_number
    return f"{base_number}-{existing_count + 1}"


def _build_exchange_replacement_sales_number(conn, sales_number, sale_id, reference_time=None):
    or_number = str(sales_number or sale_id).strip()
    stamp_source = reference_time or now_local_naive()
    stamp = stamp_source.strftime("%m%d")
    base_number = f"SW-{or_number}-{stamp}"

    existing_count_row = conn.execute(
        """
        SELECT COUNT(*) AS existing_count
        FROM sales
        WHERE sales_number = %s
           OR sales_number LIKE %s
        """,
        (base_number, f"{base_number}-%"),
    ).fetchone()

    existing_count = int(existing_count_row["existing_count"] or 0)
    if existing_count <= 0:
        return base_number
    return f"{base_number}-{existing_count + 1}"


def _determine_exchange_type(net_adjustment_amount):
    amount = round(float(net_adjustment_amount or 0), 2)
    if amount > 0:
        return "CUSTOMER_TOPUP"
    if amount < 0:
        return "SHOP_CASH_OUT"
    return "EVEN"


def _insert_sale_refund(conn, sale_id, refund_number, refund_lines, reason, notes, user_id, username, refund_time):
    refund_amount = round(sum(line["line_total"] for line in refund_lines), 2)
    refund_row = conn.execute(
        """
        INSERT INTO sale_refunds (
            sale_id,
            refund_number,
            refund_amount,
            reason,
            notes,
            refunded_by,
            refunded_by_username,
            refund_date
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            sale_id,
            refund_number,
            refund_amount,
            reason,
            notes,
            user_id,
            username,
            refund_time,
        ),
    ).fetchone()

    refund_id = int(refund_row["id"])
    audit_notes = f"{refund_number}: {reason}" + (f" | {notes}" if notes else "")

    for line in refund_lines:
        conn.execute(
            """
            INSERT INTO sale_refund_items (
                refund_id,
                sale_item_id,
                item_id,
                quantity,
                unit_price,
                line_total
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                refund_id,
                line["sale_item_id"],
                line["item_id"],
                line["quantity"],
                line["final_unit_price"],
                line["line_total"],
            ),
        )

        add_transaction(
            item_id=line["item_id"],
            quantity=line["quantity"],
            transaction_type='IN',
            user_id=user_id,
            user_name=username,
            reference_id=sale_id,
            reference_type='SALE',
            change_reason='CUSTOMER_REFUND',
            unit_price=line["final_unit_price"],
            transaction_date=refund_time,
            external_conn=conn,
            notes=audit_notes,
        )

    return {
        "refund_id": refund_id,
        "refund_number": refund_number,
        "refund_amount": refund_amount,
    }


def _create_exchange_replacement_sale(
    conn,
    original_sale,
    exchange_number,
    replacement_item_id,
    replacement_quantity,
    refund_time,
    user_id,
    username,
    reason,
    notes,
):
    replacement_item_row = conn.execute(
        """
        SELECT id, name, a4s_selling_price, cost_per_piece
        FROM items
        WHERE id = %s
        FOR UPDATE
        """,
        (replacement_item_id,),
    ).fetchone()
    if not replacement_item_row:
        raise ValueError("Replacement item not found.")

    current_stock_row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN transaction_type = 'IN' THEN quantity
                WHEN transaction_type = 'OUT' THEN -quantity
                ELSE 0
            END
        ), 0) AS current_stock
        FROM inventory_transactions
        WHERE item_id = %s
        """,
        (replacement_item_id,),
    ).fetchone()
    current_stock = int(current_stock_row["current_stock"] or 0)
    if replacement_quantity > current_stock:
        raise ValueError(
            f"Insufficient stock for replacement item '{replacement_item_row['name']}'. "
            f"Requested: {replacement_quantity}, Available: {current_stock}."
        )

    unit_price = round(float(replacement_item_row["a4s_selling_price"] or 0), 2)
    replacement_amount = round(replacement_quantity * unit_price, 2)
    cash_payment_method_id = _get_cash_payment_method_id(conn)
    original_sale_label = original_sale["sales_number"] or f"#{original_sale['id']}"
    replacement_sales_number = _build_exchange_replacement_sales_number(
        conn,
        original_sale["sales_number"],
        original_sale["id"],
        reference_time=datetime.strptime(refund_time, "%Y-%m-%d %H:%M:%S"),
    )
    exchange_notes = f"Exchange replacement linked to {original_sale_label} via {exchange_number}. Reason: {reason}"
    if notes:
        exchange_notes += f" | {notes}"

    replacement_sale_row = conn.execute(
        """
        INSERT INTO sales (
            sales_number,
            customer_name,
            customer_id,
            vehicle_id,
            total_amount,
            payment_method_id,
            reference_no,
            status,
            notes,
            user_id,
            transaction_date,
            mechanic_id,
            paid_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'Paid', %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            replacement_sales_number,
            original_sale["customer_name"],
            original_sale["customer_id"],
            original_sale["vehicle_id"],
            replacement_amount,
            cash_payment_method_id,
            original_sale["sales_number"],
            exchange_notes,
            user_id,
            refund_time,
            None,
            refund_time,
        ),
    ).fetchone()
    replacement_sale_id = int(replacement_sale_row["id"])
    replacement_cost_snapshot = round(float(replacement_item_row["cost_per_piece"] or 0), 2)

    conn.execute(
        """
        INSERT INTO sales_items (
            sale_id, item_id, quantity,
            original_unit_price, discount_percent, discount_amount, final_unit_price,
            cost_per_piece_snapshot, discounted_by, created_at
        ) VALUES (%s, %s, %s, %s, 0, 0, %s, %s, NULL, %s)
        """,
        (
            replacement_sale_id,
            replacement_item_id,
            replacement_quantity,
            unit_price,
            unit_price,
            replacement_cost_snapshot,
            refund_time,
        ),
    )

    add_transaction(
        item_id=replacement_item_id,
        quantity=replacement_quantity,
        transaction_type='OUT',
        user_id=user_id,
        user_name=username,
        reference_id=replacement_sale_id,
        reference_type='SALE',
        change_reason='CUSTOMER_EXCHANGE_REPLACEMENT',
        unit_price=unit_price,
        transaction_date=refund_time,
        external_conn=conn,
        notes=exchange_notes,
    )

    return {
        "replacement_sale_id": replacement_sale_id,
        "replacement_sales_number": replacement_sales_number,
        "replacement_item_name": replacement_item_row["name"],
        "replacement_quantity": replacement_quantity,
        "replacement_unit_price": unit_price,
        "replacement_amount": replacement_amount,
    }


def _sale_refund_cutoff(sale_row):
    transaction_date = sale_row["transaction_date"]
    if not transaction_date:
        return None
    return transaction_date.date() + timedelta(days=REFUND_WINDOW_DAYS)


def get_sale_refund_context(sale_id):
    conn = get_db()
    try:
        sale = conn.execute(
            """
            SELECT
                s.id,
                s.sales_number,
                COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
                s.customer_name,
                s.customer_id,
                s.vehicle_id,
                s.total_amount,
                s.status,
                s.notes,
                s.transaction_date,
                s.mechanic_id,
                m.name AS mechanic_name,
                pm.name AS payment_method_name,
                pm.category AS payment_method_category,
                se.exchange_number,
                se.original_sale_id,
                os.sales_number AS original_sales_number
            FROM sales s
            LEFT JOIN mechanics m ON m.id = s.mechanic_id
            LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
            LEFT JOIN sale_exchanges se ON se.replacement_sale_id = s.id
            LEFT JOIN sales os ON os.id = se.original_sale_id
            WHERE s.id = %s
            """,
            (sale_id,),
        ).fetchone()

        if not sale:
            raise ValueError("Sale not found.")

        item_rows = conn.execute(
            """
            SELECT
                si.id AS sale_item_id,
                si.item_id,
                i.name,
                i.description,
                si.quantity AS sold_quantity,
                si.original_unit_price,
                si.discount_amount,
                si.final_unit_price,
                COALESCE((
                    SELECT SUM(sri.quantity)
                    FROM sale_refund_items sri
                    JOIN sale_refunds sr ON sr.id = sri.refund_id
                    WHERE sri.sale_item_id = si.id
                ), 0) AS refunded_quantity
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id = %s
            ORDER BY i.name ASC, si.id ASC
            """,
            (sale_id,),
        ).fetchall()

        service_rows = conn.execute(
            """
            SELECT sv.name, ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id = %s
            ORDER BY sv.name ASC, ss.id ASC
            """,
            (sale_id,),
        ).fetchall()

        bundle_rows = conn.execute(
            """
            SELECT
                sb.id,
                sb.bundle_id,
                sb.bundle_version_id,
                sb.bundle_variant_id,
                sb.bundle_name_snapshot,
                sb.vehicle_category_snapshot,
                sb.bundle_version_no_snapshot,
                sb.subcategory_name_snapshot,
                sb.item_value_reference_snapshot,
                sb.shop_share_snapshot,
                sb.mechanic_share_snapshot,
                sb.bundle_price_snapshot
            FROM sales_bundles sb
            WHERE sb.sale_id = %s
            ORDER BY sb.id ASC
            """,
            (sale_id,),
        ).fetchall()

        bundle_service_rows = conn.execute(
            """
            SELECT
                sbs.sales_bundle_id,
                sbs.service_id,
                sbs.service_name_snapshot,
                sbs.sort_order
            FROM sales_bundle_services sbs
            JOIN sales_bundles sb ON sb.id = sbs.sales_bundle_id
            WHERE sb.sale_id = %s
            ORDER BY sbs.sales_bundle_id ASC, sbs.sort_order ASC, sbs.id ASC
            """,
            (sale_id,),
        ).fetchall()

        bundle_item_rows = conn.execute(
            """
            SELECT
                sbi.sales_bundle_id,
                sbi.item_id,
                sbi.item_name_snapshot,
                i.description,
                sbi.quantity,
                sbi.selling_price_snapshot,
                sbi.line_total_snapshot,
                sbi.is_included,
                sbi.sort_order
            FROM sales_bundle_items sbi
            JOIN sales_bundles sb ON sb.id = sbi.sales_bundle_id
            LEFT JOIN items i ON i.id = sbi.item_id
            WHERE sb.sale_id = %s
            ORDER BY sbi.sales_bundle_id ASC, sbi.sort_order ASC, sbi.id ASC
            """,
            (sale_id,),
        ).fetchall()

        refund_rows = conn.execute(
            """
            SELECT
                sr.id,
                sr.refund_number,
                sr.refund_amount,
                sr.reason,
                sr.notes,
                sr.refund_date,
                sr.refunded_by_username,
                se.exchange_number,
                se.exchange_type,
                se.replacement_amount,
                se.net_adjustment_amount,
                rs.sales_number AS replacement_sales_number
            FROM sale_refunds sr
            LEFT JOIN sale_exchanges se ON se.refund_id = sr.id
            LEFT JOIN sales rs ON rs.id = se.replacement_sale_id
            WHERE sr.sale_id = %s
            ORDER BY sr.refund_date DESC, sr.id DESC
            """,
            (sale_id,),
        ).fetchall()
    finally:
        conn.close()

    sale_data = dict(sale)
    cutoff_date = _sale_refund_cutoff(sale)
    today = today_local()

    refundable_items = []
    for row in item_rows:
        refunded_qty = int(row["refunded_quantity"] or 0)
        refundable_items.append({
            "sale_item_id": int(row["sale_item_id"]),
            "item_id": int(row["item_id"]),
            "name": row["name"],
            "description": row["description"] or "",
            "sold_quantity": int(row["sold_quantity"] or 0),
            "refunded_quantity": refunded_qty,
            "refundable_quantity": max(0, int(row["sold_quantity"] or 0) - refunded_qty),
            "original_price": round(float(row["original_unit_price"] or 0), 2),
            "discount_amount": round(float(row["discount_amount"] or 0), 2),
            "final_unit_price": round(float(row["final_unit_price"] or 0), 2),
        })

    refund_history = [
        {
            **dict(row),
            "refund_amount": round(float(row["refund_amount"] or 0), 2),
            "replacement_amount": round(float(row["replacement_amount"] or 0), 2),
            "net_adjustment_amount": round(float(row["net_adjustment_amount"] or 0), 2),
            "refund_date_display": format_date(row["refund_date"], show_time=True),
        }
        for row in refund_rows
    ]
    total_refunded = round(sum(row["refund_amount"] for row in refund_history), 2)

    services_by_bundle = {}
    for row in bundle_service_rows:
        services_by_bundle.setdefault(int(row["sales_bundle_id"]), []).append({
            "service_id": int(row["service_id"]) if row["service_id"] is not None else None,
            "name": row["service_name_snapshot"],
        })

    items_by_bundle = {}
    for row in bundle_item_rows:
        items_by_bundle.setdefault(int(row["sales_bundle_id"]), []).append({
            "item_id": int(row["item_id"]) if row["item_id"] is not None else None,
            "name": row["item_name_snapshot"],
            "description": row["description"] or "",
            "quantity": int(row["quantity"] or 0),
            "selling_price_snapshot": round(float(row["selling_price_snapshot"] or 0), 2),
            "line_total_snapshot": round(float(row["line_total_snapshot"] or 0), 2),
            "is_included": int(row["is_included"] or 0) == 1,
        })

    bundle_details = [
        {
            "sales_bundle_id": int(row["id"]),
            "bundle_id": int(row["bundle_id"]) if row["bundle_id"] is not None else None,
            "bundle_version_id": int(row["bundle_version_id"]) if row["bundle_version_id"] is not None else None,
            "bundle_variant_id": int(row["bundle_variant_id"]) if row["bundle_variant_id"] is not None else None,
            "bundle_name": row["bundle_name_snapshot"],
            "vehicle_category": row["vehicle_category_snapshot"],
            "version_no": int(row["bundle_version_no_snapshot"] or 0),
            "subcategory_name": row["subcategory_name_snapshot"],
            "item_value_reference": round(float(row["item_value_reference_snapshot"] or 0), 2),
            "shop_share": round(float(row["shop_share_snapshot"] or 0), 2),
            "mechanic_share": round(float(row["mechanic_share_snapshot"] or 0), 2),
            "bundle_price": round(float(row["bundle_price_snapshot"] or 0), 2),
            "services": services_by_bundle.get(int(row["id"]), []),
            "items": items_by_bundle.get(int(row["id"]), []),
        }
        for row in bundle_rows
    ]

    can_refund = True
    refund_block_reason = ""
    if sale_data["status"] != "Paid":
        can_refund = False
        refund_block_reason = "Only fully paid sales can be refunded."
    elif not refundable_items:
        can_refund = False
        refund_block_reason = "This sale has no item lines to refund."
    elif max((item["refundable_quantity"] for item in refundable_items), default=0) <= 0:
        can_refund = False
        refund_block_reason = "All refundable item quantities have already been returned."
    elif cutoff_date and today > cutoff_date:
        can_refund = False
        refund_block_reason = f"Refund window expired on {cutoff_date.isoformat()}."

    sale_data.update({
        "transaction_date_display": format_date(sale_data["transaction_date"], show_time=True),
        "refund_cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
        "refund_cutoff_display": format_date(cutoff_date.isoformat()) if cutoff_date else None,
        "total_amount": round(float(sale_data["total_amount"] or 0), 2),
        "total_refunded": total_refunded,
        "net_amount": round(float(sale_data["total_amount"] or 0) - total_refunded, 2),
        "refund_state": _derive_refund_state(
            total_refunded,
            sum(item["refundable_quantity"] for item in refundable_items),
        ),
        "can_refund": can_refund,
        "refund_block_reason": refund_block_reason,
        "items": refundable_items,
        "services": [
            {
                **dict(row),
                "price": round(float(row["price"] or 0), 2),
            }
            for row in service_rows
        ],
        "bundles": bundle_details,
        "refund_history": refund_history,
    })
    return sale_data


def search_sales_for_refund(query=None, days=None, has_refundable=False, limit=50):
    conn = get_db()
    try:
        conditions = []
        params = []

        search_text = str(query or "").strip()
        if search_text:
            like = f"%{search_text}%"
            digit_only_search = "".join(ch for ch in search_text if ch.isdigit())
            normalized_date_search = search_text.replace("-", "/").replace(".", "/")
            conditions.append(
                """
                (
                    s.sales_number ILIKE %s
                    OR s.customer_name ILIKE %s
                    OR EXISTS (
                        SELECT 1
                        FROM sales_items si_search
                        JOIN items i_search ON i_search.id = si_search.item_id
                        WHERE si_search.sale_id = s.id
                          AND i_search.name ILIKE %s
                    )
                    OR to_char(s.transaction_date, 'YYYY-MM-DD') ILIKE %s
                    OR to_char(s.transaction_date, 'MM/DD/YYYY') ILIKE %s
                    OR to_char(s.transaction_date, 'Mon DD, YYYY') ILIKE %s
                    OR to_char(s.transaction_date, 'Month DD, YYYY') ILIKE %s
                    OR (
                        %s <> ''
                        AND regexp_replace(to_char(s.transaction_date, 'MMDDYYYY'), '[^0-9]', '', 'g') LIKE %s
                    )
                )
                """
            )
            params.extend([
                like,
                like,
                like,
                f"%{normalized_date_search}%",
                f"%{normalized_date_search}%",
                like,
                like,
                digit_only_search,
                f"%{digit_only_search}%",
            ])

        if days:
            try:
                days_int = int(days)
            except (TypeError, ValueError):
                days_int = 0
            if days_int > 0:
                conditions.append("DATE(s.transaction_date) >= CURRENT_DATE - (%s * INTERVAL '1 day')")
                params.append(days_int)

        where_clause = _build_where_clause(conditions)
        query_sql = """
            SELECT
                s.id,
                s.sales_number,
                s.customer_name,
                s.total_amount,
                s.status,
                s.transaction_date,
                pm.name AS payment_method_name,
                pm.category AS payment_method_category,
                COALESCE(refunds.total_refunded, 0) AS refunded_amount,
                COALESCE(items.total_remaining_qty, 0) AS remaining_qty,
                se.exchange_number,
                se.original_sale_id,
                os.sales_number AS original_sales_number
            FROM sales s
            LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
            LEFT JOIN sale_exchanges se ON se.replacement_sale_id = s.id
            LEFT JOIN sales os ON os.id = se.original_sale_id
            LEFT JOIN (
                SELECT
                    sr.sale_id,
                    SUM(sr.refund_amount) AS total_refunded
                FROM sale_refunds sr
                GROUP BY sr.sale_id
            ) refunds ON refunds.sale_id = s.id
            LEFT JOIN (
                SELECT
                    si.sale_id,
                    SUM(
                        GREATEST(
                            si.quantity - COALESCE(refunded.refunded_quantity, 0),
                            0
                        )
                    ) AS total_remaining_qty
                FROM sales_items si
                LEFT JOIN (
                    SELECT
                        sri.sale_item_id,
                        SUM(sri.quantity) AS refunded_quantity
                    FROM sale_refund_items sri
                    GROUP BY sri.sale_item_id
                ) refunded ON refunded.sale_item_id = si.id
                GROUP BY si.sale_id
            ) items ON items.sale_id = s.id
        """ + where_clause + """
            ORDER BY s.transaction_date DESC, s.id DESC
            LIMIT %s
        """

        rows = conn.execute(query_sql, params + [max(1, min(int(limit or 50), 100))]).fetchall()
    finally:
        conn.close()

    today = today_local()
    results = []
    for row in rows:
        cutoff_date = _sale_refund_cutoff(row)
        can_refund = (
            row["status"] == "Paid"
            and int(row["remaining_qty"] or 0) > 0
            and (cutoff_date is None or today <= cutoff_date)
        )
        result = {
            "id": int(row["id"]),
            "sales_number": row["sales_number"] or f"#{row['id']}",
            "customer_name": row["customer_name"] or "Walk-in",
            "transaction_date": format_date(row["transaction_date"], show_time=True),
            "payment_method_name": row["payment_method_name"] or "—",
            "payment_method_category": row["payment_method_category"] or "",
            "status": row["status"],
            "total_amount": round(float(row["total_amount"] or 0), 2),
            "refunded_amount": round(float(row["refunded_amount"] or 0), 2),
            "net_amount": round(float(row["total_amount"] or 0) - float(row["refunded_amount"] or 0), 2),
            "remaining_qty": int(row["remaining_qty"] or 0),
            "refund_cutoff_date": cutoff_date.isoformat() if cutoff_date else None,
            "refund_cutoff_display": format_date(cutoff_date.isoformat()) if cutoff_date else None,
            "has_refundable_items": int(row["remaining_qty"] or 0) > 0,
            "can_refund": can_refund,
            "refund_state": _derive_refund_state(row["refunded_amount"], row["remaining_qty"]),
            "is_exchange_replacement": bool(row["exchange_number"]),
            "exchange_number": row["exchange_number"] or "",
            "original_sales_number": row["original_sales_number"] or "",
        }
        if has_refundable and not result["can_refund"]:
            continue
        results.append(result)

    return results


def record_sale_refund(sale_id, data, user_id, username):
    if not user_id:
        raise ValueError("User session not found.")

    reason = str((data or {}).get("reason") or "").strip()
    notes = str((data or {}).get("notes") or "").strip() or None
    raw_items = (data or {}).get("items") or []
    exchange_data = (data or {}).get("exchange") or {}
    exchange_enabled = str(exchange_data.get("mode") or "").strip().lower() == "swap" or bool(exchange_data.get("enabled"))
    refund_time = _normalize_db_timestamp((data or {}).get("refund_date"))

    if not reason:
        raise ValueError("Refund reason is required.")

    conn = get_db()
    try:
        conn.execute("BEGIN")

        sale = conn.execute(
            """
            SELECT id, sales_number, customer_name, customer_id, vehicle_id, status, transaction_date
            FROM sales
            WHERE id = %s
            """,
            (sale_id,),
        ).fetchone()
        if not sale:
            raise ValueError("Sale not found.")
        if sale["status"] != "Paid":
            raise ValueError("Only fully paid sales can be refunded.")

        cutoff_date = _sale_refund_cutoff(sale)
        refund_date_obj = datetime.strptime(refund_time, "%Y-%m-%d %H:%M:%S").date()
        if cutoff_date and refund_date_obj > cutoff_date:
            raise ValueError(f"Refund window expired on {cutoff_date.isoformat()}.")

        sale_item_rows = conn.execute(
            """
            SELECT
                si.id AS sale_item_id,
                si.item_id,
                i.name,
                si.quantity AS sold_quantity,
                si.final_unit_price,
                COALESCE((
                    SELECT SUM(sri.quantity)
                    FROM sale_refund_items sri
                    JOIN sale_refunds sr ON sr.id = sri.refund_id
                    WHERE sri.sale_item_id = si.id
                ), 0) AS refunded_quantity
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id = %s
            FOR UPDATE
            """,
            (sale_id,),
        ).fetchall()

        if not sale_item_rows:
            raise ValueError("This sale has no item lines to refund.")

        sale_item_map = {
            int(row["sale_item_id"]): {
                "sale_item_id": int(row["sale_item_id"]),
                "item_id": int(row["item_id"]),
                "name": row["name"],
                "remaining_quantity": max(
                    0,
                    int(row["sold_quantity"] or 0) - int(row["refunded_quantity"] or 0),
                ),
                "final_unit_price": round(float(row["final_unit_price"] or 0), 2),
            }
            for row in sale_item_rows
        }

        refund_lines = []
        for raw_item in raw_items:
            try:
                sale_item_id = int(raw_item.get("sale_item_id"))
                quantity = int(raw_item.get("quantity"))
            except (TypeError, ValueError):
                raise ValueError("Refund quantities must be whole numbers.")

            if quantity <= 0:
                continue

            sale_item = sale_item_map.get(sale_item_id)
            if not sale_item:
                raise ValueError("One or more refund items are invalid.")
            if quantity > sale_item["remaining_quantity"]:
                raise ValueError(
                    f"Refund quantity for '{sale_item['name']}' exceeds the remaining refundable quantity."
                )

            refund_lines.append({
                **sale_item,
                "quantity": quantity,
                "line_total": round(quantity * sale_item["final_unit_price"], 2),
            })

        if not refund_lines:
            raise ValueError("Select at least one item quantity to refund.")

        refund_number = _build_refund_number(
            conn,
            sale["sales_number"],
            sale_id,
            reference_time=datetime.strptime(refund_time, "%Y-%m-%d %H:%M:%S"),
        )
        refund_result = _insert_sale_refund(
            conn=conn,
            sale_id=sale_id,
            refund_number=refund_number,
            refund_lines=refund_lines,
            reason=reason,
            notes=notes,
            user_id=user_id,
            username=username,
            refund_time=refund_time,
        )

        result = {
            **refund_result,
            "sale_id": int(sale["id"]),
            "sales_number": sale["sales_number"] or f"#{sale['id']}",
            "customer_name": sale["customer_name"] or "Walk-in",
        }

        if exchange_enabled:
            try:
                replacement_item_id = int(exchange_data.get("replacement_item_id"))
                replacement_quantity = int(exchange_data.get("replacement_quantity"))
            except (TypeError, ValueError):
                raise ValueError("Select a valid replacement item and quantity for the swap.")
            if replacement_quantity <= 0:
                raise ValueError("Replacement quantity must be at least 1.")

            exchange_number = _build_exchange_number(conn, sale["sales_number"], sale_id)
            replacement_result = _create_exchange_replacement_sale(
                conn=conn,
                original_sale=sale,
                exchange_number=exchange_number,
                replacement_item_id=replacement_item_id,
                replacement_quantity=replacement_quantity,
                refund_time=refund_time,
                user_id=user_id,
                username=username,
                reason=reason,
                notes=notes,
            )

            net_adjustment_amount = round(
                float(replacement_result["replacement_amount"]) - float(refund_result["refund_amount"]),
                2,
            )
            exchange_type = _determine_exchange_type(net_adjustment_amount)

            exchange_row = conn.execute(
                """
                INSERT INTO sale_exchanges (
                    exchange_number,
                    original_sale_id,
                    refund_id,
                    replacement_sale_id,
                    exchange_type,
                    refunded_amount,
                    replacement_amount,
                    net_adjustment_amount,
                    reason,
                    notes,
                    exchanged_by,
                    exchanged_by_username,
                    exchanged_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    exchange_number,
                    sale_id,
                    refund_result["refund_id"],
                    replacement_result["replacement_sale_id"],
                    exchange_type,
                    refund_result["refund_amount"],
                    replacement_result["replacement_amount"],
                    net_adjustment_amount,
                    reason,
                    notes,
                    user_id,
                    username,
                    refund_time,
                ),
            ).fetchone()

            result.update({
                "mode": "swap",
                "exchange_id": int(exchange_row["id"]),
                "exchange_number": exchange_number,
                "exchange_type": exchange_type,
                "replacement_sale_id": replacement_result["replacement_sale_id"],
                "replacement_sales_number": replacement_result["replacement_sales_number"],
                "replacement_item_name": replacement_result["replacement_item_name"],
                "replacement_quantity": replacement_result["replacement_quantity"],
                "replacement_amount": replacement_result["replacement_amount"],
                "net_adjustment_amount": net_adjustment_amount,
            })
        else:
            result["mode"] = "refund"

        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


PO_APPROVAL_TYPE = "PURCHASE_ORDER"
PO_ENTITY_TYPE = "purchase_order"
PO_EDITABLE_APPROVAL_STATUSES = {"REVISIONS_NEEDED", "APPROVED"}
PO_RECEIVABLE_STATUSES = {"PENDING", "PARTIAL"}
PO_ADMIN_PENDING_NOTIFICATION_TYPES = {
    "PO_SUBMITTED_FOR_APPROVAL",
    "PO_RESUBMITTED_FOR_APPROVAL",
}


def _po_notification_url(po_id, audience="general"):
    if audience == "admin":
        return f"/transaction/order/{int(po_id)}/review"
    return f"/transaction/orders/list?po_id={int(po_id)}&open_po=1"


def _archive_po_admin_notifications(conn, po_id):
    archive_notifications(
        entity_type=PO_ENTITY_TYPE,
        entity_id=po_id,
        notification_types=PO_ADMIN_PENDING_NOTIFICATION_TYPES,
        external_conn=conn,
    )


def _archive_po_requester_notifications(conn, po_id, requester_id):
    archive_notifications(
        recipient_user_id=requester_id,
        entity_type=PO_ENTITY_TYPE,
        entity_id=po_id,
        external_conn=conn,
    )


def _notify_po_admins_pending(conn, po_row, actor_user_id, notification_type):
    admin_ids = [
        admin_id for admin_id in list_active_user_ids(role="admin", external_conn=conn)
        if int(admin_id) != int(actor_user_id)
    ]
    if not admin_ids:
        return

    po_id = int(po_row["id"])
    po_number = po_row["po_number"]
    vendor_name = po_row["vendor_name"] or "Unknown vendor"
    verb = "submitted" if notification_type == "PO_SUBMITTED_FOR_APPROVAL" else "resubmitted"

    _archive_po_admin_notifications(conn, po_id)
    create_notifications_for_users(
        admin_ids,
        notification_type,
        "Purchase order needs approval",
        f"{po_number} for {vendor_name} was {verb} and is waiting for approval.",
        category="approval",
        entity_type=PO_ENTITY_TYPE,
        entity_id=po_id,
        action_url=_po_notification_url(po_id, audience="admin"),
        created_by=actor_user_id,
        metadata={
            "po_number": po_number,
            "vendor_name": vendor_name,
        },
        external_conn=conn,
    )


def _notify_po_requester(conn, po_row, requester_id, actor_user_id, notification_type, title, message):
    if int(requester_id) == int(actor_user_id):
        return

    po_id = int(po_row["id"])
    po_number = po_row["po_number"]
    vendor_name = po_row["vendor_name"] or "Unknown vendor"

    _archive_po_requester_notifications(conn, po_id, requester_id)
    create_notification(
        requester_id,
        notification_type,
        title,
        message,
        category="approval",
        entity_type=PO_ENTITY_TYPE,
        entity_id=po_id,
        action_url=_po_notification_url(po_id, audience="requester"),
        created_by=actor_user_id,
        metadata={
            "po_number": po_number,
            "vendor_name": vendor_name,
        },
        external_conn=conn,
    )


def _coerce_positive_int(value, field_name):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number.")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be at least 1.")
    return parsed


def _coerce_nonnegative_float(value, field_name):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid amount.")
    if parsed < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return parsed


def _normalize_po_purchase_mode(value):
    mode = str(value or "PIECE").strip().upper()
    if mode not in {"PIECE", "BOX"}:
        raise ValueError("Purchase mode must be either piece-based or box-based.")
    return mode


def _normalize_po_payload(data):
    payload = data or {}
    raw_vendor_id = payload.get("vendor_id")
    notes = str(payload.get("notes") or "").strip() or None
    raw_items = payload.get("items") or []

    try:
        vendor_id = int(raw_vendor_id)
    except (TypeError, ValueError):
        vendor_id = 0

    if vendor_id <= 0:
        raise ValueError("Please select a vendor / supplier from the list.")
    if not raw_items:
        raise ValueError("Add at least one item to the purchase order.")

    normalized_items = []
    seen_item_ids = set()
    for item in raw_items:
        try:
            item_id = int(item.get("id") or item.get("item_id"))
        except (TypeError, ValueError):
            raise ValueError("Every purchase order item must have a valid item ID.")

        if item_id in seen_item_ids:
            raise ValueError("Duplicate items are not allowed in the same purchase order.")
        seen_item_ids.add(item_id)

        purchase_mode = _normalize_po_purchase_mode(item.get("purchase_mode"))
        box_cost_confirmed = item.get("box_cost_confirmed")
        if purchase_mode == "BOX" and box_cost_confirmed is not True:
            raise ValueError(
                "Box-based items must use the cost of one box only. Please review the box cost entry and try again."
            )

        normalized_items.append(
            {
                "item_id": item_id,
                "name": str(item.get("name") or "").strip() or None,
                "qty": _coerce_positive_int(item.get("qty"), "Quantity"),
                "cost": _coerce_nonnegative_float(item.get("cost"), "Unit cost"),
                "purchase_mode": purchase_mode,
            }
        )

    return {
        "vendor_id": vendor_id,
        "notes": notes,
        "items": normalized_items,
    }


def _get_active_vendor_by_id(conn, vendor_id):
    return conn.execute(
        """
        SELECT id, vendor_name, address, contact_person, contact_no, email
        FROM vendors
        WHERE id = %s AND is_active = 1
        """,
        (vendor_id,),
    ).fetchone()


def _vendor_snapshot_from_row(vendor_row):
    return {
        "vendor_id": int(vendor_row["id"]),
        "vendor_name": str(vendor_row["vendor_name"] or "").strip(),
        "vendor_address": str(vendor_row["address"] or "").strip() or None,
        "vendor_contact_person": str(vendor_row["contact_person"] or "").strip() or None,
        "vendor_contact_no": str(vendor_row["contact_no"] or "").strip() or None,
        "vendor_email": str(vendor_row["email"] or "").strip() or None,
    }


def _build_po_approval_metadata(po_row, items):
    return {
        "po_number": po_row["po_number"],
        "vendor_id": po_row["vendor_id"],
        "vendor_name": po_row["vendor_name"] or "",
        "total_amount": float(po_row["total_amount"] or 0),
        "item_count": len(items),
        "status": po_row["status"],
        "items": [
            {
                "item_id": int(item["item_id"]),
                "qty": int(item["quantity_ordered"]),
                "cost": float(item["unit_cost"]),
                "purchase_mode": _normalize_po_purchase_mode(item.get("purchase_mode")),
            }
            for item in items
        ],
    }


def _get_po_row(conn, po_id):
    return conn.execute(
        """
        SELECT po.*, u.username AS created_by_username
        FROM purchase_orders po
        LEFT JOIN users u ON u.id = po.created_by
        WHERE po.id = %s
        """,
        (po_id,),
    ).fetchone()


def _get_po_items(conn, po_id):
    return conn.execute(
        """
        SELECT
            pi.*,
            i.name,
            i.pack_size,
            i.cost_per_piece,
            COALESCE((
                SELECT SUM(
                    CASE
                        WHEN t.transaction_type = 'IN' THEN t.quantity
                        WHEN t.transaction_type = 'OUT' THEN -t.quantity
                        ELSE 0
                    END
                )
                FROM inventory_transactions t
                WHERE t.item_id = pi.item_id
            ), 0) AS current_stock,
            COALESCE((
                SELECT SUM(other_pi.quantity_ordered - other_pi.quantity_received)
                FROM po_items other_pi
                JOIN purchase_orders other_po ON other_po.id = other_pi.po_id
                WHERE other_pi.item_id = pi.item_id
                  AND COALESCE(other_pi.purchase_mode, 'PIECE') = 'PIECE'
                  AND other_po.status IN ('PENDING', 'PARTIAL')
                  AND other_pi.quantity_ordered > other_pi.quantity_received
            ), 0) AS pending_stock
            ,
            COALESCE((
                SELECT SUM(other_pi.quantity_ordered - other_pi.quantity_received)
                FROM po_items other_pi
                JOIN purchase_orders other_po ON other_po.id = other_pi.po_id
                WHERE other_pi.item_id = pi.item_id
                  AND COALESCE(other_pi.purchase_mode, 'PIECE') = 'BOX'
                  AND other_po.status IN ('PENDING', 'PARTIAL')
                  AND other_pi.quantity_ordered > other_pi.quantity_received
            ), 0) AS pending_box_quantity
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
        ORDER BY i.name ASC, pi.id ASC
        """,
        (po_id,),
    ).fetchall()


def _get_po_approval(conn, po_id):
    return get_approval_request_by_entity(
        PO_APPROVAL_TYPE,
        PO_ENTITY_TYPE,
        po_id,
        external_conn=conn,
    )


def _total_received_quantity(items):
    return sum(int(item["quantity_received"] or 0) for item in items)


def _normalize_po_revision_items(current_items, revision_items):
    item_lookup = {}
    for item in current_items:
        item_lookup[int(item["item_id"])] = item

    normalized = []
    seen_item_ids = set()
    for raw_item in revision_items or []:
        note = str(raw_item.get("revision_note") or "").strip()
        if not note:
            continue

        try:
            item_id = int(raw_item.get("item_id"))
        except (TypeError, ValueError):
            raise ValueError("Each item revision must reference a valid purchase order item.")

        if item_id not in item_lookup:
            raise ValueError("One or more revised items do not belong to this purchase order.")
        if item_id in seen_item_ids:
            raise ValueError("Duplicate item revisions are not allowed.")
        seen_item_ids.add(item_id)

        po_item = item_lookup[item_id]
        normalized.append(
            {
                "item_id": item_id,
                "item_name": po_item["name"],
                "quantity_ordered": int(po_item["quantity_ordered"] or 0),
                "quantity_received": int(po_item["quantity_received"] or 0),
                "revision_note": note,
            }
        )

    return normalized


def _replace_po_items_and_order_transactions(conn, po_id, items, user_id, username, clean_time):
    conn.execute(
        """
        DELETE FROM inventory_transactions
        WHERE reference_type = 'PURCHASE_ORDER'
          AND reference_id = %s
          AND transaction_type = 'ORDER'
        """,
        (po_id,),
    )
    conn.execute("DELETE FROM po_items WHERE po_id = %s", (po_id,))

    total_order_amount = 0.0
    for item in items:
        qty = item["qty"]
        cost = item["cost"]
        total_order_amount += qty * cost

        conn.execute(
            """
            INSERT INTO po_items (po_id, item_id, quantity_ordered, unit_cost, purchase_mode)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (po_id, item["item_id"], qty, cost, item["purchase_mode"]),
        )

        add_transaction(
            item_id=item["item_id"],
            quantity=qty,
            transaction_type='ORDER',
            user_id=user_id,
            user_name=username,
            reference_id=po_id,
            reference_type='PURCHASE_ORDER',
            change_reason='ORDER_PLACEMENT',
            unit_price=cost,
            transaction_date=clean_time,
            external_conn=conn
        )

    return total_order_amount


def _fmt_change_value(value, value_type=None):
    if value is None:
        return None
    if value_type == "money":
        return f"{float(value):.2f}"
    return str(value)


def _build_po_change_entries(previous_po, previous_items, normalized_payload):
    change_entries = []

    previous_vendor = str(previous_po.get("vendor_name") or "").strip()
    next_vendor = str(normalized_payload.get("vendor_name") or "").strip()
    if previous_vendor != next_vendor:
        change_entries.append(
            {
                "change_scope": "HEADER",
                "field_name": "vendor_name",
                "before_value": _fmt_change_value(previous_vendor or None),
                "after_value": _fmt_change_value(next_vendor or None),
                "change_label": "Vendor updated",
            }
        )

    previous_notes = str(previous_po.get("notes") or "").strip()
    next_notes = str(normalized_payload.get("notes") or "").strip()
    if previous_notes != next_notes:
        change_entries.append(
            {
                "change_scope": "HEADER",
                "field_name": "notes",
                "before_value": _fmt_change_value(previous_notes or None),
                "after_value": _fmt_change_value(next_notes or None),
                "change_label": "PO notes updated",
            }
        )

    previous_by_item = {int(item["item_id"]): item for item in previous_items}
    next_by_item = {int(item["item_id"]): item for item in normalized_payload["items"]}

    all_item_ids = sorted(set(previous_by_item) | set(next_by_item))
    for item_id in all_item_ids:
        previous_item = previous_by_item.get(item_id)
        next_item = next_by_item.get(item_id)

        if previous_item and not next_item:
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": previous_item["name"],
                    "field_name": "item_status",
                    "before_value": "present",
                    "after_value": "removed",
                    "change_label": "Item removed",
                }
            )
            continue

        if next_item and not previous_item:
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": next_item.get("name") or f"Item #{item_id}",
                    "field_name": "item_status",
                    "before_value": "missing",
                    "after_value": "added",
                    "change_label": "Item added",
                }
            )
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": next_item.get("name") or f"Item #{item_id}",
                    "field_name": "quantity_ordered",
                    "before_value": None,
                    "after_value": _fmt_change_value(next_item["qty"]),
                    "change_label": "Ordered quantity set",
                }
            )
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": next_item.get("name") or f"Item #{item_id}",
                    "field_name": "unit_cost",
                    "before_value": None,
                    "after_value": _fmt_change_value(next_item["cost"], value_type="money"),
                    "change_label": "Unit cost set",
                }
            )
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": next_item.get("name") or f"Item #{item_id}",
                    "field_name": "purchase_mode",
                    "before_value": None,
                    "after_value": _fmt_change_value(next_item["purchase_mode"]),
                    "change_label": "Purchase mode set",
                }
            )
            continue

        previous_qty = int(previous_item["quantity_ordered"] or 0)
        next_qty = int(next_item["qty"] or 0)
        if previous_qty != next_qty:
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": previous_item["name"],
                    "field_name": "quantity_ordered",
                    "before_value": _fmt_change_value(previous_qty),
                    "after_value": _fmt_change_value(next_qty),
                    "change_label": "Ordered quantity updated",
                }
            )

        previous_cost = float(previous_item["unit_cost"] or 0)
        next_cost = float(next_item["cost"] or 0)
        if previous_cost != next_cost:
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": previous_item["name"],
                    "field_name": "unit_cost",
                    "before_value": _fmt_change_value(previous_cost, value_type="money"),
                    "after_value": _fmt_change_value(next_cost, value_type="money"),
                    "change_label": "Unit cost updated",
                }
            )

        previous_mode = _normalize_po_purchase_mode(previous_item.get("purchase_mode"))
        next_mode = _normalize_po_purchase_mode(next_item.get("purchase_mode"))
        if previous_mode != next_mode:
            change_entries.append(
                {
                    "change_scope": "ITEM",
                    "item_id": item_id,
                    "item_name": previous_item["name"],
                    "field_name": "purchase_mode",
                    "before_value": _fmt_change_value(previous_mode),
                    "after_value": _fmt_change_value(next_mode),
                    "change_label": "Purchase mode updated",
                }
            )

    return change_entries


def _serialize_po_permissions(po_row, approval_data, total_received, current_user_id, current_role):
    approval_status = (approval_data or {}).get("status")
    is_creator = int(po_row["created_by"] or 0) == int(current_user_id or 0)
    is_admin = str(current_role or "").strip().lower() == "admin"
    po_status = (po_row["status"] or "").upper()
    is_review_only = po_status in {"PARTIAL", "COMPLETED", "CANCELLED"} or total_received > 0

    can_edit = (
        is_creator
        and po_status not in {"PARTIAL", "COMPLETED", "CANCELLED"}
        and total_received == 0
        and approval_status in PO_EDITABLE_APPROVAL_STATUSES
    )
    can_receive = po_status in PO_RECEIVABLE_STATUSES

    if is_admin:
        can_cancel = po_status not in {"PARTIAL", "COMPLETED", "CANCELLED"} and total_received == 0
    else:
        can_cancel = (
            is_creator
            and po_status not in {"PARTIAL", "COMPLETED", "CANCELLED"}
            and total_received == 0
            and approval_status not in {"APPROVED", "CANCELLED"}
        )

    return {
        "can_edit": can_edit,
        "can_cancel": can_cancel,
        "can_receive": can_receive,
        "can_admin_approve": is_admin and not is_review_only and approval_status in {"PENDING", "REVISIONS_NEEDED"},
        "can_admin_request_revisions": is_admin and not is_review_only and approval_status in {"PENDING", "APPROVED"},
        "can_admin_cancel": is_admin and can_cancel,
        "is_creator": is_creator,
    }


def create_purchase_order(data, user_id, username, user_role):
    """
    Creates a new purchase order and logs ORDER transactions.
    Returns the new po_number and po_id.
    NOTE (future branches): add branch_id when ready.
    """
    conn = get_db()
    now_obj = now_local()
    clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")
    month_str = now_obj.strftime("%Y%m")
    normalized = _normalize_po_payload(data)

    try:
        conn.execute("BEGIN")

        count = conn.execute(
            "SELECT COUNT(*) FROM purchase_orders WHERE po_number ILIKE %s",
            (f"PO-{month_str}%",)
        ).fetchone()[0]
        vendor_row = _get_active_vendor_by_id(conn, normalized["vendor_id"])
        if not vendor_row:
            raise ValueError("Selected vendor was not found or is inactive.")
        normalized.update(_vendor_snapshot_from_row(vendor_row))

        po_number = f"PO-{month_str}-{str(count + 1).zfill(3)}"
        initial_status = 'PENDING' if str(user_role or '').strip().lower() == 'admin' else 'FOR_APPROVAL'

        po_row = conn.execute("""
            INSERT INTO purchase_orders (
                po_number,
                vendor_id,
                vendor_name,
                vendor_address,
                vendor_contact_person,
                vendor_contact_no,
                vendor_email,
                notes,
                status,
                created_by,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, po_number, vendor_id, vendor_name, notes, status, total_amount, created_by
        """, (
            po_number,
            normalized["vendor_id"],
            normalized["vendor_name"],
            normalized["vendor_address"],
            normalized["vendor_contact_person"],
            normalized["vendor_contact_no"],
            normalized["vendor_email"],
            normalized["notes"],
            initial_status,
            user_id,
            clean_time,
        )).fetchone()

        new_po_id = po_row["id"]
        total_order_amount = _replace_po_items_and_order_transactions(
            conn=conn,
            po_id=new_po_id,
            items=normalized["items"],
            user_id=user_id,
            username=username,
            clean_time=clean_time,
        )

        conn.execute(
            "UPDATE purchase_orders SET total_amount = %s WHERE id = %s",
            (total_order_amount, new_po_id)
        )
        po_row = conn.execute(
            """
            SELECT id, po_number, vendor_id, vendor_name, notes, status, total_amount, created_by
            FROM purchase_orders
            WHERE id = %s
            """,
            (new_po_id,),
        ).fetchone()
        po_items = _get_po_items(conn, new_po_id)
        create_approval_request(
            approval_type=PO_APPROVAL_TYPE,
            entity_type=PO_ENTITY_TYPE,
            entity_id=new_po_id,
            requested_by=user_id,
            requester_role=user_role,
            metadata=_build_po_approval_metadata(po_row, po_items),
            external_conn=conn,
        )

        if str(user_role or "").strip().lower() != "admin":
            _notify_po_admins_pending(
                conn,
                po_row=po_row,
                actor_user_id=user_id,
                notification_type="PO_SUBMITTED_FOR_APPROVAL",
            )

        conn.commit()
        return po_number, new_po_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_purchase_orders():
    """Returns all purchase orders for the overview page."""
    conn = get_db()
    orders = conn.execute("""
        SELECT po.*,
            ar.id AS approval_request_id,
            ar.status AS approval_status,
            ar.decision_notes AS approval_decision_notes,
            ar.current_revision_no,
            (SELECT COUNT(*) FROM po_items WHERE po_id = po.id) as item_count
        FROM purchase_orders po
        LEFT JOIN approval_requests ar
            ON ar.approval_type = %s
           AND ar.entity_type = %s
           AND ar.entity_id = po.id
        ORDER BY created_at DESC
    """, (PO_APPROVAL_TYPE, PO_ENTITY_TYPE)).fetchall()
    conn.close()
    return orders


def search_purchase_orders(query, limit=20):
    """Searches purchase orders by PO number across all states."""
    cleaned_query = str(query or "").strip()
    if not cleaned_query:
        return []

    normalized_query = re.sub(r"[^A-Za-z0-9]", "", cleaned_query).lower()
    if not normalized_query:
        return []

    safe_limit = max(1, min(int(limit or 20), 20))
    raw_query = cleaned_query.lower()
    raw_prefix = f"{raw_query}%"
    raw_contains = f"%{raw_query}%"
    normalized_prefix = f"{normalized_query}%"
    normalized_contains = f"%{normalized_query}%"

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                po.id,
                po.po_number,
                po.vendor_name,
                po.status,
                po.total_amount,
                po.created_at,
                ar.status AS approval_status,
                (
                    SELECT COUNT(*)
                    FROM po_items pi
                    WHERE pi.po_id = po.id
                ) AS item_count,
                CASE
                    WHEN LOWER(po.po_number) = %s THEN 0
                    WHEN REPLACE(REPLACE(LOWER(po.po_number), '-', ''), ' ', '') = %s THEN 0
                    WHEN LOWER(po.po_number) LIKE %s THEN 1
                    WHEN REPLACE(REPLACE(LOWER(po.po_number), '-', ''), ' ', '') LIKE %s THEN 1
                    WHEN LOWER(po.po_number) LIKE %s THEN 2
                    WHEN REPLACE(REPLACE(LOWER(po.po_number), '-', ''), ' ', '') LIKE %s THEN 2
                    ELSE 3
                END AS match_rank
            FROM purchase_orders po
            LEFT JOIN approval_requests ar
                ON ar.approval_type = %s
               AND ar.entity_type = %s
               AND ar.entity_id = po.id
            WHERE LOWER(po.po_number) LIKE %s
               OR REPLACE(REPLACE(LOWER(po.po_number), '-', ''), ' ', '') LIKE %s
            ORDER BY match_rank ASC, po.created_at DESC, po.id DESC
            LIMIT %s
            """,
            (
                raw_query,
                normalized_query,
                raw_prefix,
                normalized_prefix,
                raw_contains,
                normalized_contains,
                PO_APPROVAL_TYPE,
                PO_ENTITY_TYPE,
                raw_contains,
                normalized_contains,
                safe_limit,
            ),
        ).fetchall()

        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_active_purchase_orders():
    """Returns non-archived purchase orders for the overview page."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT po.*,
                ar.id AS approval_request_id,
                ar.status AS approval_status,
                ar.decision_notes AS approval_decision_notes,
                ar.current_revision_no,
                (SELECT COUNT(*) FROM po_items WHERE po_id = po.id) as item_count
            FROM purchase_orders po
            LEFT JOIN approval_requests ar
                ON ar.approval_type = %s
               AND ar.entity_type = %s
               AND ar.entity_id = po.id
            WHERE po.status NOT IN ('COMPLETED', 'CANCELLED')
            ORDER BY created_at DESC
            """,
            (PO_APPROVAL_TYPE, PO_ENTITY_TYPE),
        ).fetchall()
        return rows
    finally:
        conn.close()


def get_purchase_order_archive_month_summaries(status):
    """Returns archive month summaries for completed/cancelled purchase orders."""
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in {"COMPLETED", "CANCELLED"}:
        raise ValueError("Unsupported archive status.")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                TO_CHAR(DATE_TRUNC('month', created_at), 'YYYY-MM') AS month_key,
                TO_CHAR(DATE_TRUNC('month', created_at), 'FMMonth YYYY') AS month_label,
                COUNT(*) AS order_count
            FROM purchase_orders
            WHERE status = %s
            GROUP BY DATE_TRUNC('month', created_at)
            ORDER BY DATE_TRUNC('month', created_at) DESC
            """,
            (normalized_status,),
        ).fetchall()
        return [
            {
                "key": row["month_key"] or "unknown",
                "label": str(row["month_label"] or "Unknown Date").strip() or "Unknown Date",
                "order_count": int(row["order_count"] or 0),
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_purchase_orders_by_archive_month(status, month_key):
    """Returns purchase orders for a single archive month."""
    normalized_status = str(status or "").strip().upper()
    normalized_month_key = str(month_key or "").strip()
    if normalized_status not in {"COMPLETED", "CANCELLED"}:
        raise ValueError("Unsupported archive status.")
    if not re.fullmatch(r"\d{4}-\d{2}", normalized_month_key):
        raise ValueError("Invalid archive month.")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                po.id,
                po.po_number,
                po.vendor_name,
                po.status,
                po.total_amount,
                po.created_at,
                ar.status AS approval_status,
                (SELECT COUNT(*) FROM po_items WHERE po_id = po.id) AS item_count
            FROM purchase_orders po
            LEFT JOIN approval_requests ar
                ON ar.approval_type = %s
               AND ar.entity_type = %s
               AND ar.entity_id = po.id
            WHERE po.status = %s
              AND TO_CHAR(DATE_TRUNC('month', po.created_at), 'YYYY-MM') = %s
            ORDER BY po.created_at DESC, po.id DESC
            """,
            (PO_APPROVAL_TYPE, PO_ENTITY_TYPE, normalized_status, normalized_month_key),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_purchase_order_with_items(po_id):
    """Returns a PO and its items. Used by the API detail endpoint."""
    conn = get_db()
    po = _get_po_row(conn, po_id)
    items = _get_po_items(conn, po_id)
    conn.close()
    return po, items


def get_purchase_order_export_data(po_id):
    """Returns PO + item rows formatted for CSV export."""
    conn = get_db()
    po = conn.execute("""
        SELECT id, po_number, vendor_name, vendor_address, vendor_contact_person, vendor_contact_no, status, created_at, received_at, total_amount
        FROM purchase_orders
        WHERE id = %s
    """, (po_id,)).fetchone()

    if not po:
        conn.close()
        return None, []

    items = conn.execute("""
        SELECT
            i.name,
            pi.quantity_ordered,
            pi.quantity_received,
            pi.unit_cost,
            COALESCE(pi.purchase_mode, 'PIECE') AS purchase_mode
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
        ORDER BY i.name ASC
    """, (po_id,)).fetchall()

    receipt_history = _get_po_receipt_history(po_id, external_conn=conn)
    conn.close()
    approval = get_approval_request_by_entity(PO_APPROVAL_TYPE, PO_ENTITY_TYPE, po_id)
    po_data = dict(po)
    po_data["approval_status"] = approval["status"] if approval else None
    po_data["display_status"] = get_po_display_status(po_data.get("status"), po_data.get("approval_status"))
    po_data["receipt_history"] = receipt_history
    return po_data, items


def get_po_for_receive_page(po_id):
    """Returns PO + items needed for the receive page. Returns None if not found."""
    conn = get_db()
    po = _get_po_row(conn, po_id)
    if not po:
        conn.close()
        return None, None

    items = _get_po_items(conn, po_id)
    conn.close()
    return po, items


def _get_po_receipt_history(po_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        receipt_rows = conn.execute(
            """
            SELECT id, po_id, received_at, received_by, received_by_username, notes
            FROM po_receipts
            WHERE po_id = %s
            ORDER BY received_at ASC, id ASC
            """,
            (po_id,),
        ).fetchall()

        if not receipt_rows:
            return []

        item_rows = conn.execute(
            """
            SELECT
                pri.receipt_id,
                pri.po_id,
                pri.item_id,
                pri.quantity_received,
                pri.unit_cost,
                pri.line_total,
                pri.purchase_mode,
                pri.stock_quantity_received,
                pri.effective_piece_cost,
                pri.notes,
                i.name AS item_name
            FROM po_receipt_items pri
            JOIN items i ON i.id = pri.item_id
            WHERE pri.po_id = %s
            ORDER BY pri.receipt_id ASC, i.name ASC, pri.id ASC
            """,
            (po_id,),
        ).fetchall()

        items_by_receipt = {}
        for row in item_rows:
            items_by_receipt.setdefault(row["receipt_id"], []).append({
                "item_id": row["item_id"],
                "item_name": row["item_name"] or "Unknown Item",
                "quantity_received": int(row["quantity_received"] or 0),
                "unit_cost": float(row["unit_cost"] or 0),
                "line_total": float(row["line_total"] or 0),
                "purchase_mode": _normalize_po_purchase_mode(row.get("purchase_mode")),
                "stock_quantity_received": int(row["stock_quantity_received"] or 0),
                "effective_piece_cost": float(row["effective_piece_cost"] or 0),
                "notes": row["notes"] or "",
            })

        history = []
        for row in receipt_rows:
            receipt_items = items_by_receipt.get(row["id"], [])
            history.append({
                "id": row["id"],
                "po_id": row["po_id"],
                "received_at": row["received_at"],
                "received_by": row["received_by"],
                "received_by_username": row["received_by_username"] or "System",
                "notes": row["notes"] or "",
                "total_amount": round(sum(item["line_total"] for item in receipt_items), 2),
                "items": receipt_items,
            })
        return history
    finally:
        if not external_conn:
            conn.close()


def get_purchase_order_details(po_id, current_user_id=None, current_role=None):
    conn = get_db()
    try:
        po = _get_po_row(conn, po_id)
        if not po:
            return None

        items = _get_po_items(conn, po_id)
        receipt_history = _get_po_receipt_history(po_id, external_conn=conn)
        approval_stub = _get_po_approval(conn, po_id)
        approval = (
            get_approval_request_with_history(approval_stub["id"], external_conn=conn)
            if approval_stub else None
        )
        total_received = _total_received_quantity(items)
        permissions = _serialize_po_permissions(
            po_row=po,
            approval_data=approval,
            total_received=total_received,
            current_user_id=current_user_id,
            current_role=current_role,
        )

        po_data = dict(po)
        po_data["created_at"] = format_date(po_data.get("created_at"), show_time=True)
        po_data["received_at"] = format_date(po_data.get("received_at"), show_time=True)
        po_data["status_class"] = get_status_class(po_data.get("status"))

        return {
            "po": po_data,
            "items": [
                {
                    **dict(item),
                    "quantity_ordered": int(item["quantity_ordered"] or 0),
                    "quantity_received": int(item["quantity_received"] or 0),
                    "unit_cost": float(item["unit_cost"] or 0),
                    "cost_per_piece": float(item["cost_per_piece"] or 0),
                    "current_stock": int(item["current_stock"] or 0),
                    "pending_stock": int(item["pending_stock"] or 0),
                    "purchase_mode": _normalize_po_purchase_mode(item.get("purchase_mode")),
                }
                for item in items
            ],
            "receipt_history": [
                {
                    **receipt,
                    "received_at": format_date(receipt.get("received_at"), show_time=True),
                }
                for receipt in receipt_history
            ],
            "approval": approval,
            "permissions": permissions,
        }
    finally:
        conn.close()


def get_purchase_order_review_context(po_id, current_user_id=None, current_role=None):
    details = get_purchase_order_details(
        po_id,
        current_user_id=current_user_id,
        current_role=current_role,
    )
    if not details:
        return None

    review_timeline = []
    for action in details["approval"].get("actions", []) if details.get("approval") else []:
        grouped_item_changes = {}
        header_changes = []

        for entry in action.get("change_entries", []) or []:
            if entry.get("change_scope") == "HEADER":
                header_changes.append(entry)
            else:
                item_key = str(entry.get("item_id") or entry.get("item_name") or "unknown")
                bucket = grouped_item_changes.setdefault(
                    item_key,
                    {
                        "item_name": entry.get("item_name") or "Unknown Item",
                        "entries": [],
                    },
                )
                bucket["entries"].append(entry)

        review_timeline.append(
            {
                **action,
                "header_changes": header_changes,
                "item_change_groups": list(grouped_item_changes.values()),
                "has_no_change_resubmission": (
                    str(action.get("action_type") or "").upper() in {"RESUBMITTED", "EDITED_AFTER_APPROVAL"}
                    and not (action.get("change_entries") or [])
                ),
            }
        )

    review_notes = details["po"].get("notes") or "-"
    for action in review_timeline:
        action_type = str(action.get("action_type") or "").upper()
        if "CANCEL" in action_type:
            review_notes = action.get("notes") or "-"
            break

    return {
        "po": details["po"],
        "items": details["items"],
        "receipt_history": details.get("receipt_history", []),
        "approval": details["approval"],
        "permissions": details["permissions"],
        "review_timeline": review_timeline,
        "review_notes": review_notes,
    }


def update_purchase_order(po_id, data, user_id, username, user_role):
    normalized = _normalize_po_payload(data)
    conn = get_db()
    clean_time = now_local_str()

    try:
        conn.execute("BEGIN")
        po = _get_po_row(conn, po_id)
        if not po:
            raise ValueError("Purchase order not found.")

        approval = _get_po_approval(conn, po_id)
        if not approval:
            raise ValueError("Approval request not found for this purchase order.")
        if int(po["created_by"] or 0) != int(user_id):
            raise ValueError("Only the creator can edit this purchase order.")

        current_items = _get_po_items(conn, po_id)
        if _total_received_quantity(current_items) > 0 or (po["status"] or "").upper() in {"PARTIAL", "COMPLETED", "CANCELLED"}:
            raise ValueError("This purchase order can no longer be edited.")
        if approval["status"] not in PO_EDITABLE_APPROVAL_STATUSES:
            raise ValueError("This purchase order is not currently editable.")

        vendor_row = _get_active_vendor_by_id(conn, normalized["vendor_id"])
        if not vendor_row:
            raise ValueError("Selected vendor was not found or is inactive.")
        normalized.update(_vendor_snapshot_from_row(vendor_row))

        change_entries = _build_po_change_entries(po, current_items, normalized)

        total_order_amount = _replace_po_items_and_order_transactions(
            conn=conn,
            po_id=po_id,
            items=normalized["items"],
            user_id=user_id,
            username=username,
            clean_time=clean_time,
        )

        conn.execute(
            """
            UPDATE purchase_orders
            SET vendor_id = %s,
                vendor_name = %s,
                vendor_address = %s,
                vendor_contact_person = %s,
                vendor_contact_no = %s,
                vendor_email = %s,
                notes = %s,
                total_amount = %s,
                status = %s
            WHERE id = %s
            """,
            (
                normalized["vendor_id"],
                normalized["vendor_name"],
                normalized["vendor_address"],
                normalized["vendor_contact_person"],
                normalized["vendor_contact_no"],
                normalized["vendor_email"],
                normalized["notes"],
                total_order_amount,
                "FOR_APPROVAL",
                po_id,
            ),
        )

        refreshed_po = conn.execute(
            """
            SELECT id, po_number, vendor_id, vendor_name, notes, status, total_amount, created_by
            FROM purchase_orders
            WHERE id = %s
            """,
            (po_id,),
        ).fetchone()
        refreshed_items = _get_po_items(conn, po_id)
        approval = resubmit_request(
            approval_request_id=approval["id"],
            requester_id=user_id,
            metadata=_build_po_approval_metadata(refreshed_po, refreshed_items),
            notes="Purchase order updated and resubmitted.",
            change_entries=change_entries,
            external_conn=conn,
        )
        _archive_po_requester_notifications(conn, po_id, user_id)

        if str(user_role or "").strip().lower() == "admin":
            approve_request(
                approval_request_id=approval["id"],
                admin_user_id=user_id,
                notes="Auto-approved after admin edit.",
                external_conn=conn,
            )
            conn.execute(
                "UPDATE purchase_orders SET status = %s WHERE id = %s",
                ("PENDING", po_id),
            )
            _archive_po_admin_notifications(conn, po_id)
        else:
            _notify_po_admins_pending(
                conn,
                po_row=refreshed_po,
                actor_user_id=user_id,
                notification_type="PO_RESUBMITTED_FOR_APPROVAL",
            )

        conn.commit()
        return get_purchase_order_details(po_id, current_user_id=user_id, current_role=user_role)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_purchase_order(po_id, user_id, user_role, notes=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        po = _get_po_row(conn, po_id)
        if not po:
            raise ValueError("Purchase order not found.")

        approval = _get_po_approval(conn, po_id)
        if not approval:
            raise ValueError("Approval request not found for this purchase order.")

        po_items = _get_po_items(conn, po_id)
        total_received = _total_received_quantity(po_items)
        po_status = (po["status"] or "").upper()
        role = str(user_role or "").strip().lower()

        if po_status in {"PARTIAL", "COMPLETED", "CANCELLED"} or total_received > 0:
            raise ValueError("Only unreceived purchase orders can be cancelled.")

        if role != "admin":
            if int(po["created_by"] or 0) != int(user_id):
                raise ValueError("Only the creator can cancel this purchase order.")
            if approval["status"] in {"APPROVED", "CANCELLED"}:
                raise ValueError("Approved or cancelled purchase orders cannot be cancelled by staff.")

        cancel_request(
            approval_request_id=approval["id"],
            actor_id=user_id,
            actor_role=role,
            notes=notes,
            external_conn=conn,
        )
        conn.execute(
            "UPDATE purchase_orders SET status = %s WHERE id = %s",
            ("CANCELLED", po_id),
        )

        _archive_po_admin_notifications(conn, po_id)
        if role == "admin":
            _notify_po_requester(
                conn,
                po_row=po,
                requester_id=po["created_by"],
                actor_user_id=user_id,
                notification_type="PO_CANCELLED",
                title="Purchase order cancelled",
                message=f"{po['po_number']} was cancelled by an admin.",
            )
        else:
            _archive_po_requester_notifications(conn, po_id, user_id)

        conn.commit()
        return get_purchase_order_details(po_id, current_user_id=user_id, current_role=user_role)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_purchase_order(po_id, admin_user_id, notes=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        approval = _get_po_approval(conn, po_id)
        if not approval:
            raise ValueError("Approval request not found for this purchase order.")
        if not _get_po_row(conn, po_id):
            raise ValueError("Purchase order not found.")
        if _total_received_quantity(_get_po_items(conn, po_id)) > 0:
            raise ValueError("This purchase order already has received quantities and cannot be re-approved.")

        approve_request(
            approval_request_id=approval["id"],
            admin_user_id=admin_user_id,
            notes=notes,
            external_conn=conn,
        )
        conn.execute(
            "UPDATE purchase_orders SET status = %s WHERE id = %s",
            ("PENDING", po_id),
        )

        po = _get_po_row(conn, po_id)
        _archive_po_admin_notifications(conn, po_id)
        _notify_po_requester(
            conn,
            po_row=po,
            requester_id=po["created_by"],
            actor_user_id=admin_user_id,
            notification_type="PO_APPROVED",
            title="Purchase order approved",
            message=f"{po['po_number']} was approved and is ready for receiving.",
        )

        conn.commit()
        return get_purchase_order_details(po_id, current_user_id=admin_user_id, current_role="admin")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def request_po_revisions(po_id, admin_user_id, notes, revision_items=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        approval = _get_po_approval(conn, po_id)
        if not approval:
            raise ValueError("Approval request not found for this purchase order.")
        if not _get_po_row(conn, po_id):
            raise ValueError("Purchase order not found.")
        po_items = _get_po_items(conn, po_id)
        normalized_revision_items = _normalize_po_revision_items(po_items, revision_items)

        request_revisions(
            approval_request_id=approval["id"],
            admin_user_id=admin_user_id,
            notes=notes,
            revision_items=normalized_revision_items,
            external_conn=conn,
        )
        conn.execute(
            "UPDATE purchase_orders SET status = %s WHERE id = %s",
            ("FOR_APPROVAL", po_id),
        )

        po = _get_po_row(conn, po_id)
        _archive_po_admin_notifications(conn, po_id)
        _notify_po_requester(
            conn,
            po_row=po,
            requester_id=po["created_by"],
            actor_user_id=admin_user_id,
            notification_type="PO_REVISIONS_REQUESTED",
            title="Purchase order needs revision",
            message=f"{po['po_number']} was returned for revisions.",
        )

        conn.commit()
        return get_purchase_order_details(po_id, current_user_id=admin_user_id, current_role="admin")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def receive_purchase_order(po_id, received_items, user_id, username):
    """
    Processes stock reception for a PO.
    Handles cost correction and PO status update.
    Raises ValueError for business logic errors.
    NOTE (future branches): add branch_id when ready.
    """
    conn = get_db()
    clean_time = now_local_str()

    try:
        conn.execute("BEGIN")
        po = _get_po_row(conn, po_id)
        if not po:
            raise ValueError("Purchase order not found.")
        if (po["status"] or "").upper() not in PO_RECEIVABLE_STATUSES:
            raise ValueError("This purchase order is not approved for receiving.")
        all_completed = True
        received_any = False
        receipt_entries = []

        for entry in received_items:
            item_id = entry['item_id']
            qty_in = int(entry['qty_received'])

            if qty_in <= 0:
                continue

            po_item = conn.execute("""
                SELECT quantity_ordered, quantity_received, unit_cost, purchase_mode
                FROM po_items
                WHERE po_id = %s AND item_id = %s
            """, (po_id, item_id)).fetchone()

            if not po_item:
                raise ValueError(f"Item ID {item_id} not found in this PO.")

            already_received = po_item['quantity_received']
            qty_ordered = po_item['quantity_ordered']
            remaining = qty_ordered - already_received
            unit_cost = po_item['unit_cost']
            purchase_mode = _normalize_po_purchase_mode(po_item.get("purchase_mode"))

            if remaining <= 0:
                raise ValueError(f"Item ID {item_id} is already fully received.")
            if qty_in > remaining:
                raise ValueError(f"Cannot receive more than the remaining PO quantity for item ID {item_id}.")

            stock_qty_in = qty_in
            if float(unit_cost or 0) <= 0:
                raise ValueError(f"Unit cost must be greater than 0 before receiving item ID {item_id}.")

            effective_piece_cost = float(unit_cost or 0)
            receive_note_suffix = ""

            if purchase_mode == "BOX":
                total_counted_pieces = int(entry.get("stock_quantity_received") or 0)
                if total_counted_pieces <= 0:
                    raise ValueError("Box-based receipts require the total counted pieces received today.")
                stock_qty_in = total_counted_pieces
                line_total = round(float(unit_cost or 0) * qty_in, 2)
                effective_piece_cost = round(line_total / total_counted_pieces, 2)
                receive_note_suffix = (
                    f"Box receipt: {qty_in} box(es), {total_counted_pieces} total counted piece(s), "
                    f"box cost {float(unit_cost or 0):.2f}, effective piece cost {effective_piece_cost:.2f}."
                )
            else:
                line_total = round(float(unit_cost or 0) * qty_in, 2)

            # Cost self-correction
            item_row = conn.execute(
                "SELECT cost_per_piece FROM items WHERE id = %s", (item_id,)
            ).fetchone()
            current_master_cost = float(item_row["cost_per_piece"] or 0)

            if effective_piece_cost != current_master_cost:
                current_master_cost, _ = _update_item_cost_and_markup(conn, item_id, effective_piece_cost)
                add_transaction(
                    item_id=item_id, quantity=0, transaction_type='IN',
                    user_id=user_id, user_name=username,
                    reference_id=po_id, reference_type='PURCHASE_ORDER',
                    change_reason='COST_PER_PIECE_UPDATED', unit_price=effective_piece_cost,
                    transaction_date=clean_time, external_conn=conn,
                    notes=(
                        f"Cost updated from {current_master_cost:.2f} to {effective_piece_cost:.2f} via PO receive. "
                        f"{receive_note_suffix}".strip()
                    )
                )

            received_any = True
            will_still_have_remaining = (already_received + qty_in) < qty_ordered
            arrival_reason = 'PARTIAL_ARRIVAL' if will_still_have_remaining else 'PO_ARRIVAL'
            add_transaction(
                item_id=item_id, quantity=stock_qty_in, transaction_type='IN',
                user_id=user_id, user_name=username,
                reference_id=po_id, reference_type='PURCHASE_ORDER',
                change_reason=arrival_reason, unit_price=effective_piece_cost,
                transaction_date=clean_time, external_conn=conn,
                notes=receive_note_suffix or None,
            )

            conn.execute("""
                UPDATE po_items
                SET quantity_received = quantity_received + %s
                WHERE po_id = %s AND item_id = %s
            """, (qty_in, po_id, item_id))

            updated = conn.execute("""
                SELECT quantity_ordered, quantity_received
                FROM po_items WHERE po_id = %s AND item_id = %s
            """, (po_id, item_id)).fetchone()

            if updated['quantity_received'] < updated['quantity_ordered']:
                all_completed = False

            receipt_entries.append({
                "po_id": po_id,
                "item_id": item_id,
                "quantity_received": qty_in,
                "unit_cost": float(unit_cost or 0),
                "line_total": line_total,
                "purchase_mode": purchase_mode,
                "stock_quantity_received": stock_qty_in,
                "effective_piece_cost": effective_piece_cost,
                "notes": receive_note_suffix.strip(),
            })

        if not received_any:
            raise ValueError("Enter at least one received quantity before confirming delivery.")

        receipt_row = conn.execute(
            """
            INSERT INTO po_receipts (po_id, received_at, received_by, received_by_username, notes)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (po_id, clean_time, user_id, username, None),
        ).fetchone()
        receipt_id = receipt_row["id"]

        for receipt_entry in receipt_entries:
            conn.execute(
                """
                INSERT INTO po_receipt_items (
                    receipt_id, po_id, item_id, quantity_received, unit_cost, line_total,
                    purchase_mode, stock_quantity_received, effective_piece_cost, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    receipt_id,
                    receipt_entry["po_id"],
                    receipt_entry["item_id"],
                    receipt_entry["quantity_received"],
                    receipt_entry["unit_cost"],
                    receipt_entry["line_total"],
                    receipt_entry["purchase_mode"],
                    receipt_entry["stock_quantity_received"],
                    receipt_entry["effective_piece_cost"],
                    receipt_entry["notes"] or None,
                ),
            )

        new_status = 'COMPLETED' if all_completed else 'PARTIAL'
        conn.execute("""
            UPDATE purchase_orders SET status = %s, received_at = %s
            WHERE id = %s
        """, (new_status, clean_time, po_id))

        ensure_payable_for_po_receipt(
            receipt_id,
            created_by=user_id,
            created_by_username=username,
            external_conn=conn,
        )

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_po_details_for_api(po_id, snapshot_at=None, change_reason=None, transaction_type=None):
    """
    Returns a formatted dict for the PO detail API response.
    Returns None if not found.
    """
    conn = get_db()
    po = conn.execute("""
        SELECT po_number, vendor_name, status, total_amount, created_at, received_at
        FROM purchase_orders WHERE id = %s
    """, (po_id,)).fetchone()

    if not po:
        conn.close()
        return None

    items = conn.execute("""
        SELECT i.name, pi.quantity_ordered,
            pi.unit_cost AS unit_price,
            COALESCE(pi.purchase_mode, 'PIECE') AS purchase_mode,
            (pi.quantity_ordered * pi.unit_cost) AS subtotal
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
    """, (po_id,)).fetchall()
    receipt_history = _get_po_receipt_history(po_id, external_conn=conn)
    conn.close()

    approval = get_approval_request_by_entity(PO_APPROVAL_TYPE, PO_ENTITY_TYPE, po_id)

    def _format_snapshot_status(reason, po_status):
        reason_normalized = str(reason or "").strip().upper()
        if reason_normalized == "PARTIAL_ARRIVAL":
            return "Partial Delivery", "bg-warning text-dark"
        if reason_normalized == "PO_ARRIVAL":
            return "Completed Delivery", "bg-success"
        if reason_normalized == "COST_PER_PIECE_UPDATED":
            return "Cost Updated", "bg-warning text-dark"
        if reason_normalized == "ORDER_PLACEMENT":
            return "Order Placement", "bg-primary"
        return po_status or "Pending", get_status_class(po_status)

    def _parse_cost_update_note(note_text):
        if not note_text:
            return None, None
        match = re.search(
            r"Cost updated from ([0-9]+(?:\.[0-9]+)?) to ([0-9]+(?:\.[0-9]+)?)",
            str(note_text),
        )
        if not match:
            return None, None
        return float(match.group(1)), float(match.group(2))

    def _build_po_movement_details(reason, movement_type, movement_rows, matched_receipt=None):
        reason_normalized = str(reason or "").strip().upper()
        movement_normalized = str(movement_type or "").strip().upper()
        if not reason_normalized:
            return None

        base = {
            "reason": reason_normalized,
            "context_note": "",
            "entries": [],
        }

        if movement_normalized == "ORDER" and reason_normalized == "ORDER_PLACEMENT":
            return {
                **base,
                "title": "Order Placement",
                "accent": "info",
                "summary": "This audit row captured the original PO quantities and unit costs at placement time.",
                "entries": [
                    {
                        "item_name": row["name"] or "Unknown Item",
                        "quantity": int(row["quantity"] or 0),
                        "unit_cost": float(row["unit_price"] or 0),
                        "subtotal": round(int(row["quantity"] or 0) * float(row["unit_price"] or 0), 2),
                        "notes": row["notes"] or "",
                    }
                    for row in movement_rows
                    if str(row["change_reason"] or "").strip().upper() == "ORDER_PLACEMENT"
                ],
            }

        if reason_normalized in {"PO_ARRIVAL", "PARTIAL_ARRIVAL"}:
            entries = [
                {
                    "item_name": row["name"] or "Unknown Item",
                    "quantity": int(row["quantity"] or 0),
                    "unit_cost": float(row["unit_price"] or 0),
                    "subtotal": round(int(row["quantity"] or 0) * float(row["unit_price"] or 0), 2),
                    "notes": row["notes"] or "",
                }
                for row in movement_rows
                if str(row["change_reason"] or "").strip().upper() == reason_normalized and int(row["quantity"] or 0) > 0
            ]
            if not entries and matched_receipt:
                entries = [
                    {
                        "item_name": item.get("item_name") or "Unknown Item",
                        "quantity": int(item.get("stock_quantity_received") or 0),
                        "unit_cost": float(item.get("effective_piece_cost") or 0),
                        "subtotal": float(item.get("line_total") or 0),
                        "notes": item.get("notes") or "",
                    }
                    for item in matched_receipt.get("items", [])
                    if int(item.get("stock_quantity_received") or 0) > 0
                ]
            return {
                **base,
                "title": "Receipt Movement",
                "accent": "success" if reason_normalized == "PO_ARRIVAL" else "warning",
                "summary": "This audit row represents the quantity received for this PO receipt event.",
                "entries": entries,
            }

        if reason_normalized == "COST_PER_PIECE_UPDATED":
            entries = []
            for row in movement_rows:
                if str(row["change_reason"] or "").strip().upper() != "COST_PER_PIECE_UPDATED":
                    continue
                previous_cost, updated_cost = _parse_cost_update_note(row["notes"])
                entries.append({
                    "item_name": row["name"] or "Unknown Item",
                    "quantity": int(row["quantity"] or 0),
                    "unit_cost": float(row["unit_price"] or 0),
                    "previous_cost": previous_cost,
                    "updated_cost": updated_cost,
                    "notes": row["notes"] or "",
                })
            return {
                **base,
                "title": "Cost Per Piece Update",
                "accent": "warning",
                "summary": "This audit row updated the item master cost during the same PO receipt event.",
                "context_note": "The receipt batch below shows the related PO delivery snapshot recorded at the same timestamp.",
                "entries": entries,
            }

        return None

    if snapshot_at and transaction_type:
        conn = get_db()
        try:
            snapshot_items = []
            total_amount = 0.0
            received_at_value = "-"
            snapshot_reason = str(change_reason or "").strip().upper()
            movement_type = str(transaction_type or "").strip().upper()
            matched_receipt = None
            movement_rows = conn.execute(
                """
                SELECT i.name, t.item_id, t.quantity, t.unit_price, t.notes, t.change_reason
                FROM inventory_transactions t
                JOIN items i ON i.id = t.item_id
                WHERE t.reference_type = 'PURCHASE_ORDER'
                  AND t.reference_id = %s
                  AND t.transaction_type = %s
                  AND t.transaction_date = %s
                ORDER BY t.id ASC
                """,
                (po_id, movement_type, snapshot_at),
            ).fetchall()

            if movement_type == "ORDER":
                for row in movement_rows:
                    qty = int(row["quantity"] or 0)
                    unit_price = float(row["unit_price"] or 0)
                    subtotal = qty * unit_price
                    total_amount += subtotal
                    snapshot_items.append({
                        "name": row["name"],
                        "quantity_ordered": qty,
                        "unit_price": unit_price,
                        "subtotal": subtotal,
                    })
            elif movement_type == "IN":
                matched_receipt = next(
                    (receipt for receipt in receipt_history if receipt.get("received_at") and str(receipt.get("received_at").isoformat()) == str(snapshot_at)),
                    None,
                )
                if matched_receipt:
                    received_at_value = format_date(matched_receipt.get("received_at"), show_time=True)
                    for item in matched_receipt.get("items", []):
                        if snapshot_reason and snapshot_reason == "PARTIAL_ARRIVAL" and int(item.get("quantity_received") or 0) <= 0:
                            continue
                        total_amount += float(item.get("line_total") or 0)
                        snapshot_items.append({
                            "name": item.get("item_name") or "Unknown Item",
                            "quantity_ordered": int(item.get("stock_quantity_received") or 0),
                            "unit_price": float(item.get("effective_piece_cost") or 0),
                            "purchase_mode": item.get("purchase_mode") or "PIECE",
                            "subtotal": float(item.get("line_total") or 0),
                        })
                else:
                    received_at_value = format_date(snapshot_at, show_time=True)
                    for row in movement_rows:
                        if snapshot_reason and str(row["change_reason"] or "").strip().upper() != snapshot_reason:
                            continue
                        qty = int(row["quantity"] or 0)
                        unit_price = float(row["unit_price"] or 0)
                        subtotal = qty * unit_price
                        total_amount += subtotal
                        snapshot_items.append({
                            "name": row["name"],
                            "quantity_ordered": qty,
                            "unit_price": unit_price,
                            "subtotal": subtotal,
                        })

            status_text, status_class = _format_snapshot_status(change_reason, po['status'])
            movement_details = _build_po_movement_details(
                snapshot_reason,
                movement_type,
                movement_rows,
                matched_receipt=matched_receipt,
            )
            return {
                "modal_title": "Purchase Order Movement",
                "po_number": po['po_number'],
                "vendor_name": po['vendor_name'],
                "status": status_text,
                "status_class": status_class,
                "total_amount": total_amount,
                "mode": movement_type,
                "created_at": format_date(po['created_at'], show_time=True),
                "received_at": received_at_value,
                "approval_status": approval["status"] if approval else None,
                "receipt_history": [
                    {
                        **receipt,
                        "received_at": format_date(receipt.get("received_at"), show_time=True),
                    }
                    for receipt in receipt_history
                ],
                "movement_details": movement_details,
                "items": snapshot_items,
            }
        finally:
            conn.close()

    return {
        "modal_title": "Purchase Order Details",
        "po_number": po['po_number'],
        "vendor_name": po['vendor_name'],
        "status": po['status'] or "Pending",
        "status_class": get_status_class(po['status']),
        "total_amount": po['total_amount'],
        "mode": 'IN' if po['received_at'] else 'ORDER',
        "created_at": format_date(po['created_at'], show_time=True),
        "received_at": format_date(po['received_at'], show_time=True),
        "approval_status": approval["status"] if approval else None,
        "receipt_history": [
            {
                **receipt,
                "received_at": format_date(receipt.get("received_at"), show_time=True),
            }
            for receipt in receipt_history
        ],
        "items": [
            {
                "name": item['name'],
                "quantity_ordered": item['quantity_ordered'],
                "unit_price": float(item['unit_price']),
                "purchase_mode": item['purchase_mode'],
                "subtotal": float(item['subtotal'])
            }
            for item in items
        ]
    }


def get_po_display_status(po_status, approval_status=None):
    po_status_normalized = str(po_status or "PENDING").strip().upper()
    approval_status_normalized = str(approval_status or "").strip().upper()

    if po_status_normalized == "FOR_APPROVAL":
        if approval_status_normalized == "REVISIONS_NEEDED":
            return "FOR_REVISIONS"
        return "FOR_APPROVAL"
    if po_status_normalized == "PENDING":
        return "READY_TO_RECEIVE"
    return po_status_normalized


# ─────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────

def get_status_class(status):
    """Returns Bootstrap badge class for a PO status string."""
    status = (status or "Pending").upper()
    if status == "COMPLETED":
        return "bg-success"
    elif status == "PARTIAL":
        return "bg-info text-dark"
    elif status == "FOR_APPROVAL":
        return "bg-secondary"
    elif status == "PENDING":
        return "bg-warning text-dark"
    elif status == "CANCELLED":
        return "bg-danger"
    else:
        return "bg-secondary"
