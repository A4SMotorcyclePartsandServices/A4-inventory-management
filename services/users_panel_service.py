import re
from datetime import datetime

from psycopg2 import errors as pg_errors
from werkzeug.security import generate_password_hash

from db.database import get_db
from services.vendor_service import get_vendors_panel_records
from services.transactions_service import get_sale_refund_context
from utils.formatters import format_date, norm_text
from utils.timezone import now_local_str


def get_users_page_context(active_tab="mechanics-tab", include_audit_data=False):
    conn = get_db()
    try:
        mechanics = conn.execute(
            "SELECT * FROM mechanics ORDER BY name ASC"
        ).fetchall()
        mechanic_quota_topup_overrides = conn.execute(
            """
            SELECT
                o.id,
                o.mechanic_id,
                o.quota_date,
                o.applies_quota_topup,
                o.created_at,
                o.updated_at,
                m.name AS mechanic_name
            FROM mechanic_quota_topup_overrides o
            JOIN mechanics m ON m.id = o.mechanic_id
            ORDER BY o.quota_date DESC, m.name ASC, o.id DESC
            LIMIT 40
            """
        ).fetchall()
        services_list = conn.execute(
            "SELECT * FROM services ORDER BY category ASC, name ASC LIMIT 20"
        ).fetchall()
        categories = conn.execute(
            "SELECT DISTINCT category FROM services WHERE category IS NOT NULL"
        ).fetchall()
        payment_methods = conn.execute(
            "SELECT * FROM payment_methods ORDER BY category ASC, name ASC"
        ).fetchall()
        bundles = conn.execute(
            """
            SELECT
                b.id,
                b.name,
                b.vehicle_category,
                b.is_active,
                b.created_at,
                COALESCE(cv.version_no, 1) AS current_version_no,
                COALESCE(v.variant_count, 0) AS variant_count,
                COALESCE(s.service_count, 0) AS service_count,
                COALESCE(i.item_count, 0) AS item_count
            FROM bundles b
            LEFT JOIN (
                SELECT DISTINCT ON (bundle_id)
                    id,
                    bundle_id,
                    version_no
                FROM bundle_versions
                WHERE is_current = 1
                ORDER BY bundle_id, version_no DESC, id DESC
            ) cv ON cv.bundle_id = b.id
            LEFT JOIN (
                SELECT bv.bundle_id, COUNT(bvv.id) AS variant_count
                FROM bundle_versions bv
                JOIN bundle_version_variants bvv ON bvv.bundle_version_id = bv.id
                WHERE bv.is_current = 1
                GROUP BY bv.bundle_id
            ) v ON v.bundle_id = b.id
            LEFT JOIN (
                SELECT bv.bundle_id, COUNT(bvs.id) AS service_count
                FROM bundle_versions bv
                JOIN bundle_version_services bvs ON bvs.bundle_version_id = bv.id
                WHERE bv.is_current = 1
                GROUP BY bv.bundle_id
            ) s ON s.bundle_id = b.id
            LEFT JOIN (
                SELECT bv.bundle_id, COUNT(bvi.id) AS item_count
                FROM bundle_versions bv
                JOIN bundle_version_items bvi ON bvi.bundle_version_id = bv.id
                WHERE bv.is_current = 1
                GROUP BY bv.bundle_id
            ) i ON i.bundle_id = b.id
            ORDER BY b.created_at DESC, b.id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    formatted_mechanic_quota_topup_overrides = [
        {
            **dict(row),
            "quota_date_display": format_date(row["quota_date"]),
            "updated_at_display": format_date(row["updated_at"], show_time=True),
        }
        for row in mechanic_quota_topup_overrides
    ]
    formatted_bundles = [
        {**dict(bundle), "created_at": format_date(bundle["created_at"], show_time=True)}
        for bundle in bundles
    ]
    vendors = get_vendors_panel_records()

    context = {
        "mechanics": mechanics,
        "mechanic_quota_topup_overrides": formatted_mechanic_quota_topup_overrides,
        "services_list": services_list,
        "categories": categories,
        "payment_methods": payment_methods,
        "bundles": formatted_bundles,
        "vendors": vendors,
        "active_tab": active_tab,
    }

    return context


def create_staff_user(username, password, phone_no, created_by):
    normalized_username = str(username or "").strip()
    normalized_phone_no = str(phone_no or "").strip()

    if not normalized_username or not str(password or "").strip() or not normalized_phone_no:
        raise ValueError("Username, password, and phone number are required.")

    conn = get_db()
    try:
        now = now_local_str()
        conn.execute(
            """
            INSERT INTO users (username, password_hash, phone_no, role, created_at, created_by)
            VALUES (%s, %s, %s, 'staff', %s, %s)
            """,
            (
                normalized_username,
                generate_password_hash(password),
                normalized_phone_no,
                now,
                created_by,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def add_mechanic_record(name, commission, phone):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO mechanics (name, commission_rate, phone, is_active)
            VALUES (%s, %s, %s, 1)
            """,
            (name, commission, phone),
        )
        conn.commit()
    finally:
        conn.close()


def toggle_mechanic_active_status(mechanic_id):
    conn = get_db()
    try:
        mechanic = conn.execute(
            "SELECT is_active, name FROM mechanics WHERE id = %s",
            (mechanic_id,),
        ).fetchone()
        if not mechanic:
            return {"status": "missing"}

        was_active = mechanic["is_active"]
        new_status = 0 if was_active == 1 else 1
        conn.execute(
            "UPDATE mechanics SET is_active = %s WHERE id = %s",
            (new_status, mechanic_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "name": mechanic["name"],
            "was_active": was_active,
            "new_status": new_status,
        }
    finally:
        conn.close()


def save_mechanic_quota_topup_override(mechanic_id, quota_date, applies_quota_topup):
    normalized_date = str(quota_date or "").strip()
    if not mechanic_id or not normalized_date:
        raise ValueError("Mechanic and date are required.")

    try:
        mechanic_id = int(mechanic_id)
    except (TypeError, ValueError):
        raise ValueError("Invalid mechanic selected.")

    try:
        datetime.strptime(normalized_date, "%Y-%m-%d")
    except ValueError:
        raise ValueError("Invalid quota date.")

    applies_flag = 1 if str(applies_quota_topup).strip().lower() in {"1", "true", "yes", "on"} else 0

    conn = get_db()
    try:
        mechanic = conn.execute(
            "SELECT id, name FROM mechanics WHERE id = %s",
            (mechanic_id,),
        ).fetchone()
        if not mechanic:
            raise ValueError("Mechanic not found.")

        conn.execute(
            """
            INSERT INTO mechanic_quota_topup_overrides (
                mechanic_id, quota_date, applies_quota_topup, updated_at
            )
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (mechanic_id, quota_date)
            DO UPDATE SET
                applies_quota_topup = EXCLUDED.applies_quota_topup,
                updated_at = NOW()
            """,
            (mechanic_id, normalized_date, applies_flag),
        )
        conn.commit()
        return {
            "mechanic_name": mechanic["name"],
            "quota_date": normalized_date,
            "applies_quota_topup": applies_flag,
        }
    finally:
        conn.close()


def delete_mechanic_quota_topup_override(override_id):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                o.id,
                o.quota_date,
                m.name AS mechanic_name
            FROM mechanic_quota_topup_overrides o
            JOIN mechanics m ON m.id = o.mechanic_id
            WHERE o.id = %s
            """,
            (override_id,),
        ).fetchone()
        if not row:
            return {"status": "missing"}

        conn.execute(
            "DELETE FROM mechanic_quota_topup_overrides WHERE id = %s",
            (override_id,),
        )
        conn.commit()
        return {
            "status": "ok",
            "mechanic_name": row["mechanic_name"],
            "quota_date": row["quota_date"],
        }
    finally:
        conn.close()


def get_manual_in_details(audit_group_id):
    conn = get_db()
    try:
        anchor = conn.execute(
            """
            SELECT t.id, t.item_id, t.transaction_date, t.user_id, t.user_name, i.name AS item_name
            FROM inventory_transactions t
            JOIN items i ON i.id = t.item_id
            WHERE t.id = %s
              AND t.reference_type = 'MANUAL_ADJUSTMENT'
            """,
            (audit_group_id,),
        ).fetchone()

        if not anchor:
            return {"error": "Manual stock-in record not found."}, 404

        related_rows = conn.execute(
            """
            SELECT
                t.id,
                t.quantity,
                t.change_reason,
                t.unit_price,
                t.notes,
                t.transaction_date,
                t.user_name
            FROM inventory_transactions t
            WHERE t.reference_type = 'MANUAL_ADJUSTMENT'
              AND t.item_id = %s
              AND t.transaction_date = %s
              AND COALESCE(t.user_id, 0) = COALESCE(%s, 0)
            ORDER BY t.id ASC
            """,
            (anchor["item_id"], anchor["transaction_date"], anchor["user_id"]),
        ).fetchall()
    finally:
        conn.close()

    walkin_row = next(
        (row for row in related_rows if row["change_reason"] == "WALKIN_PURCHASE"),
        None,
    )
    cost_row = next(
        (row for row in related_rows if row["change_reason"] == "COST_PER_PIECE_UPDATED"),
        None,
    )

    previous_cost = None
    updated_cost = None
    if cost_row and cost_row["notes"]:
        match = re.search(
            r"Cost updated from ([0-9]+(?:\.[0-9]+)?) to ([0-9]+(?:\.[0-9]+)?)",
            str(cost_row["notes"]),
        )
        if match:
            previous_cost = float(match.group(1))
            updated_cost = float(match.group(2))

    return {
        "item_name": anchor["item_name"],
        "transaction_date": format_date(anchor["transaction_date"], show_time=True),
        "user_name": anchor["user_name"] or "System",
        "walkin_purchase": {
            "quantity": int(walkin_row["quantity"] or 0) if walkin_row else 0,
            "unit_cost": float(walkin_row["unit_price"] or 0) if walkin_row else 0,
            "notes": walkin_row["notes"] if walkin_row else "",
        } if walkin_row else None,
        "cost_update": {
            "unit_cost": float(cost_row["unit_price"] or 0) if cost_row else 0,
            "previous_cost": previous_cost,
            "updated_cost": updated_cost,
            "notes": cost_row["notes"] if cost_row else "",
        } if cost_row else None,
    }, 200


def add_service_record(name, existing_category, new_category):
    normalized_name = (name or "").strip()
    normalized_new_category = (new_category or "").strip()

    if not normalized_name:
        return {"status": "missing_fields"}

    conn = get_db()
    try:
        if existing_category == "__OTHER__" and normalized_new_category:
            match = conn.execute(
                "SELECT category FROM services WHERE LOWER(TRIM(category)) = %s LIMIT 1",
                (normalized_new_category.lower(),),
            ).fetchone()
            category = match["category"] if match else normalized_new_category
        else:
            category = (
                existing_category
                if existing_category and existing_category != "__OTHER__"
                else "Labor"
            )

        existing_service = conn.execute(
            "SELECT name FROM services WHERE LOWER(TRIM(name)) = %s LIMIT 1",
            (normalized_name.lower(),),
        ).fetchone()
        if existing_service:
            return {"status": "duplicate", "name": normalized_name}

        conn.execute(
            "INSERT INTO services (name, category, is_active) VALUES (%s, %s, 1)",
            (normalized_name, category),
        )
        conn.commit()
        return {"status": "ok", "name": normalized_name, "category": category}
    except pg_errors.UniqueViolation:
        conn.rollback()
        return {"status": "duplicate", "name": normalized_name}
    finally:
        conn.close()


def toggle_service_active_status(service_id):
    conn = get_db()
    try:
        service = conn.execute(
            "SELECT is_active, name FROM services WHERE id = %s",
            (service_id,),
        ).fetchone()
        if not service:
            return {"status": "missing"}

        new_status = 0 if service["is_active"] == 1 else 1
        conn.execute(
            "UPDATE services SET is_active = %s WHERE id = %s",
            (new_status, service_id),
        )
        conn.commit()
        return {"status": "ok", "name": service["name"], "new_status": new_status}
    finally:
        conn.close()


def _normalize_bundle_payload(name, vehicle_category, variants, service_ids, items):
    bundle_name = str(name or "").strip()
    normalized_vehicle_category = str(vehicle_category or "").strip()

    if not bundle_name:
        raise ValueError("Bundle name is required.")
    if not normalized_vehicle_category:
        raise ValueError("Vehicle category is required.")

    normalized_service_ids = []
    seen_service_ids = set()
    for raw_service_id in service_ids or []:
        try:
            service_id = int(raw_service_id)
        except (TypeError, ValueError):
            raise ValueError("One or more selected services are invalid.")
        if service_id in seen_service_ids:
            continue
        seen_service_ids.add(service_id)
        normalized_service_ids.append(service_id)

    normalized_items = []
    seen_item_ids = set()
    for index, raw in enumerate(items or []):
        raw = raw or {}
        try:
            item_id = int(raw.get("item_id"))
        except (TypeError, ValueError):
            raise ValueError("One or more selected items are invalid.")

        try:
            quantity = int(raw.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            raise ValueError("One or more bundle item quantities are invalid.")

        if quantity <= 0:
            raise ValueError("Bundle item quantities must be at least 1.")
        if item_id in seen_item_ids:
            raise ValueError("Duplicate bundle item detected. Please adjust quantity instead.")
        seen_item_ids.add(item_id)
        normalized_items.append({
            "item_id": item_id,
            "quantity": quantity,
            "sort_order": index,
        })

    normalized_variants = []
    seen_variant_names = set()
    for index, raw in enumerate(variants or []):
        variant_name = str((raw or {}).get("variant_name") or "").strip()
        if not variant_name:
            continue
        try:
            shop_share = round(float((raw or {}).get("shop_share", 0) or 0), 2)
            mechanic_share = round(float((raw or {}).get("mechanic_share", 0) or 0), 2)
        except (TypeError, ValueError):
            raise ValueError(f"Invalid pricing breakdown for subcategory '{variant_name}'.")
        if shop_share < 0 or mechanic_share < 0:
            raise ValueError(f"Pricing values cannot be negative for subcategory '{variant_name}'.")
        dedupe_key = variant_name.lower()
        if dedupe_key in seen_variant_names:
            raise ValueError(f"Duplicate bundle variant detected: '{variant_name}'.")
        seen_variant_names.add(dedupe_key)
        normalized_variants.append({
            "variant_name": variant_name,
            "shop_share": shop_share,
            "mechanic_share": mechanic_share,
            "sort_order": index,
        })

    if not normalized_variants:
        raise ValueError("At least one bundle variant is required.")

    return {
        "bundle_name": bundle_name,
        "vehicle_category": normalized_vehicle_category,
        "variants": normalized_variants,
        "service_ids": normalized_service_ids,
        "items": normalized_items,
    }


def _validate_bundle_component_refs(conn, service_ids, items):
    if service_ids:
        service_rows = conn.execute(
            "SELECT id FROM services WHERE id = ANY(%s)",
            (service_ids,),
        ).fetchall()
        existing_service_ids = {int(row["id"]) for row in service_rows}
        if len(existing_service_ids) != len(service_ids):
            raise ValueError("One or more selected services no longer exist.")

    if items:
        item_rows = conn.execute(
            "SELECT id, a4s_selling_price FROM items WHERE id = ANY(%s)",
            ([item["item_id"] for item in items],),
        ).fetchall()
        existing_item_ids = {int(row["id"]) for row in item_rows}
        if len(existing_item_ids) != len(items):
            raise ValueError("One or more selected items no longer exist.")
        return {
            int(row["id"]): round(float(row["a4s_selling_price"] or 0), 2)
            for row in item_rows
        }
    return {}


def _apply_bundle_item_value(variants, items, item_price_map):
    computed_item_value = round(
        sum(
            int(item["quantity"] or 0) * round(float(item_price_map.get(int(item["item_id"]), 0) or 0), 2)
            for item in items
        ),
        2,
    )
    for variant in variants:
        variant["item_value_reference"] = computed_item_value
        variant["sale_price"] = round(
            computed_item_value + float(variant["shop_share"] or 0) + float(variant["mechanic_share"] or 0),
            2,
        )
    return computed_item_value


def _create_bundle_version(conn, bundle_id, variants, service_ids, items, change_notes, created_by=None, created_by_username=None):
    current_version = conn.execute(
        """
        SELECT COALESCE(MAX(version_no), 0) AS latest_version
        FROM bundle_versions
        WHERE bundle_id = %s
        """,
        (bundle_id,),
    ).fetchone()
    next_version_no = int(current_version["latest_version"] or 0) + 1

    conn.execute(
        "UPDATE bundle_versions SET is_current = 0 WHERE bundle_id = %s AND is_current = 1",
        (bundle_id,),
    )
    version_row = conn.execute(
        """
        INSERT INTO bundle_versions (
            bundle_id, version_no, is_current, change_notes, created_by, created_by_username
        ) VALUES (%s, %s, 1, %s, %s, %s)
        RETURNING id, version_no
        """,
        (bundle_id, next_version_no, change_notes, created_by, created_by_username),
    ).fetchone()
    bundle_version_id = int(version_row["id"])

    conn.executemany(
        """
        INSERT INTO bundle_version_variants (
            bundle_version_id, subcategory_name,
            item_value_reference, shop_share, mechanic_share,
            sale_price, sort_order
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                bundle_version_id,
                variant["variant_name"],
                variant["item_value_reference"],
                variant["shop_share"],
                variant["mechanic_share"],
                variant["sale_price"],
                variant["sort_order"],
            )
            for variant in variants
        ],
    )

    if service_ids:
        conn.executemany(
            """
            INSERT INTO bundle_version_services (bundle_version_id, service_id, sort_order)
            VALUES (%s, %s, %s)
            """,
            [
                (bundle_version_id, service_id, index)
                for index, service_id in enumerate(service_ids)
            ],
        )

    if items:
        conn.executemany(
            """
            INSERT INTO bundle_version_items (bundle_version_id, item_id, quantity, sort_order)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (bundle_version_id, item["item_id"], item["quantity"], item["sort_order"])
                for item in items
            ],
        )

    return {"bundle_version_id": bundle_version_id, "version_no": int(version_row["version_no"])}


def create_bundle_record(name, vehicle_category, variants, service_ids, items):
    normalized = _normalize_bundle_payload(name, vehicle_category, variants, service_ids, items)
    bundle_name = normalized["bundle_name"]
    normalized_vehicle_category = normalized["vehicle_category"]
    normalized_variants = normalized["variants"]
    normalized_service_ids = normalized["service_ids"]
    normalized_items = normalized["items"]

    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT id
            FROM bundles
            WHERE LOWER(TRIM(name)) = %s
              AND LOWER(TRIM(vehicle_category)) = %s
            LIMIT 1
            """,
            (bundle_name.lower(), normalized_vehicle_category.lower()),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "name": bundle_name, "vehicle_category": normalized_vehicle_category}

        item_price_map = _validate_bundle_component_refs(conn, normalized_service_ids, normalized_items)
        _apply_bundle_item_value(normalized_variants, normalized_items, item_price_map)

        conn.execute("BEGIN")
        bundle_row = conn.execute(
            """
            INSERT INTO bundles (name, vehicle_category, is_active)
            VALUES (%s, %s, 1)
            RETURNING id
            """,
            (bundle_name, normalized_vehicle_category),
        ).fetchone()
        bundle_id = int(bundle_row["id"])
        version_result = _create_bundle_version(
            conn=conn,
            bundle_id=bundle_id,
            variants=normalized_variants,
            service_ids=normalized_service_ids,
            items=normalized_items,
            change_notes="Initial bundle version",
            created_by=None,
            created_by_username="System",
        )

        conn.commit()
        return {
            "status": "ok",
            "bundle_id": bundle_id,
            "bundle_version_id": version_result["bundle_version_id"],
            "version_no": version_result["version_no"],
            "name": bundle_name,
            "vehicle_category": normalized_vehicle_category,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def toggle_bundle_active_status(bundle_id):
    conn = get_db()
    try:
        bundle = conn.execute(
            "SELECT id, name, is_active FROM bundles WHERE id = %s",
            (bundle_id,),
        ).fetchone()
        if not bundle:
            return {"status": "missing"}

        new_status = 0 if bundle["is_active"] == 1 else 1
        conn.execute(
            "UPDATE bundles SET is_active = %s WHERE id = %s",
            (new_status, bundle_id),
        )
        conn.commit()
        return {"status": "ok", "name": bundle["name"], "new_status": new_status}
    finally:
        conn.close()


def get_bundle_edit_payload(bundle_id):
    conn = get_db()
    try:
        bundle = conn.execute(
            """
            SELECT
                b.id,
                b.name,
                b.vehicle_category,
                b.is_active,
                cv.id AS bundle_version_id,
                cv.version_no,
                cv.change_notes,
                cv.created_at,
                cv.created_by_username
            FROM bundles b
            LEFT JOIN bundle_versions cv
              ON cv.bundle_id = b.id
             AND cv.is_current = 1
            WHERE b.id = %s
            LIMIT 1
            """,
            (bundle_id,),
        ).fetchone()
        if not bundle:
            raise ValueError("Bundle not found.")

        bundle_version_id = bundle["bundle_version_id"]
        variants = []
        services = []
        items = []
        if bundle_version_id:
            variants = conn.execute(
                """
                SELECT
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
                    i.a4s_selling_price,
                    bvi.quantity,
                    bvi.sort_order
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
        "is_active": int(bundle["is_active"] or 0),
        "bundle_version_id": int(bundle_version_id) if bundle_version_id else None,
        "version_no": int(bundle["version_no"] or 0),
        "change_notes": bundle["change_notes"] or "",
        "version_created_at": format_date(bundle["created_at"], show_time=True) if bundle["created_at"] else None,
        "version_created_by_username": bundle["created_by_username"] or None,
        "variants": [
            {
                "variant_name": row["subcategory_name"],
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
                "selling_price": float(row["a4s_selling_price"] or 0),
                "quantity": int(row["quantity"] or 0),
            }
            for row in items
        ],
    }


def update_bundle_record(bundle_id, name, vehicle_category, variants, service_ids, items, changed_by=None, changed_by_username=None, change_notes=None):
    normalized = _normalize_bundle_payload(name, vehicle_category, variants, service_ids, items)
    bundle_name = normalized["bundle_name"]
    normalized_vehicle_category = normalized["vehicle_category"]
    normalized_variants = normalized["variants"]
    normalized_service_ids = normalized["service_ids"]
    normalized_items = normalized["items"]
    normalized_change_notes = str(change_notes or "").strip() or "Updated bundle version"

    conn = get_db()
    try:
        bundle = conn.execute(
            "SELECT id FROM bundles WHERE id = %s LIMIT 1",
            (bundle_id,),
        ).fetchone()
        if not bundle:
            return {"status": "missing"}

        existing = conn.execute(
            """
            SELECT id
            FROM bundles
            WHERE LOWER(TRIM(name)) = %s
              AND LOWER(TRIM(vehicle_category)) = %s
              AND id <> %s
            LIMIT 1
            """,
            (bundle_name.lower(), normalized_vehicle_category.lower(), bundle_id),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "name": bundle_name, "vehicle_category": normalized_vehicle_category}

        item_price_map = _validate_bundle_component_refs(conn, normalized_service_ids, normalized_items)
        _apply_bundle_item_value(normalized_variants, normalized_items, item_price_map)

        conn.execute("BEGIN")
        conn.execute(
            """
            UPDATE bundles
            SET name = %s,
                vehicle_category = %s
            WHERE id = %s
            """,
            (bundle_name, normalized_vehicle_category, bundle_id),
        )

        version_result = _create_bundle_version(
            conn=conn,
            bundle_id=bundle_id,
            variants=normalized_variants,
            service_ids=normalized_service_ids,
            items=normalized_items,
            change_notes=normalized_change_notes,
            created_by=changed_by,
            created_by_username=changed_by_username,
        )
        conn.commit()
        return {
            "status": "ok",
            "bundle_id": int(bundle_id),
            "bundle_version_id": version_result["bundle_version_id"],
            "version_no": version_result["version_no"],
            "name": bundle_name,
            "vehicle_category": normalized_vehicle_category,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_payment_method_record(name, category):
    normalized_name = norm_text(name)
    normalized_category = norm_text(category)
    allowed_categories = {"Bank", "Cash", "Debt", "Online"}

    if not normalized_name or not normalized_category:
        return {"status": "missing_fields"}

    if normalized_category not in allowed_categories:
        return {"status": "invalid_category"}

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM payment_methods WHERE LOWER(TRIM(name)) = %s",
            (normalized_name.lower(),),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "name": normalized_name}

        conn.execute(
            """
            INSERT INTO payment_methods (name, category, is_active)
            VALUES (%s, %s, 1)
            """,
            (normalized_name, normalized_category),
        )
        conn.commit()
        return {"status": "ok", "name": normalized_name}
    except pg_errors.UniqueViolation:
        conn.rollback()
        return {"status": "duplicate", "name": normalized_name}
    finally:
        conn.close()


def toggle_payment_method_active_status(pm_id):
    conn = get_db()
    try:
        payment_method = conn.execute(
            "SELECT name, is_active FROM payment_methods WHERE id = %s",
            (pm_id,),
        ).fetchone()
        if not payment_method:
            return {"status": "missing"}

        new_status = 0 if payment_method["is_active"] == 1 else 1
        conn.execute(
            "UPDATE payment_methods SET is_active = %s WHERE id = %s",
            (new_status, pm_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "name": payment_method["name"],
            "new_status": new_status,
        }
    finally:
        conn.close()


def get_item_details_payload(item_id):
    conn = get_db()
    try:
        item = conn.execute(
            """
            SELECT i.name, i.category, i.description, i.pack_size,
                   vendor_price, cost_per_piece, a4s_selling_price,
                   markup, reorder_level,
                   COALESCE(v.vendor_name, i.vendor) AS vendor,
                   i.vendor_id
            FROM items i
            LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE i.id = %s
            """,
            (item_id,),
        ).fetchone()
    finally:
        conn.close()

    if not item:
        return None

    return dict(item)


__all__ = [
    "create_bundle_record",
    "get_bundle_edit_payload",
    "add_mechanic_record",
    "add_payment_method_record",
    "add_service_record",
    "create_staff_user",
    "delete_mechanic_quota_topup_override",
    "get_item_details_payload",
    "get_users_page_context",
    "get_manual_in_details",
    "get_sale_refund_context",
    "save_mechanic_quota_topup_override",
    "toggle_bundle_active_status",
    "toggle_mechanic_active_status",
    "toggle_payment_method_active_status",
    "toggle_service_active_status",
    "update_bundle_record",
]
