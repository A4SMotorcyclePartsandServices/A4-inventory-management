from db.database import get_db
from datetime import datetime
from utils.formatters import format_date
from services.loyalty_service import log_stamps_for_sale


# ─────────────────────────────────────────────
# CORE LEDGER
# ─────────────────────────────────────────────

def add_transaction(item_id, quantity, transaction_type, user_id=None, user_name=None,
                    reference_id=None, reference_type=None, change_reason=None,
                    unit_price=None, transaction_date=None, external_conn=None, notes=None):
    """
    The Universal Ledger Entry.
    Handles logging and stock updates.

    ENFORCEMENT: BONUS_STOCK transactions require a notes value.
    Enforced here at service level — cannot be bypassed via API.

    NOTE (future branches): when branch_id is added, pass it here.
    Do not hardcode branch assumptions.
    """
    if change_reason == 'BONUS_STOCK':
        if not notes or not str(notes).strip():
            raise ValueError("A reason note is required for over-receive (BONUS_STOCK) transactions.")

    conn = external_conn if external_conn else get_db()

    if transaction_date:
        final_time = transaction_date.replace('T', ' ')
        if len(final_time) == 16:
            final_time += ":00"
    else:
        final_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        row = conn.execute("""
            INSERT INTO items (
                name, category, description, pack_size, 
                vendor_price, cost_per_piece, a4s_selling_price, 
                markup, reorder_level, vendor, mechanic
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
        """, (
            data['name'], data['category'], data['description'], data['pack_size'],
            data['vendor_price'], data['cost_per_piece'], data['selling_price'],
            data['markup'], data['reorder_level'], data['vendor'], data['mechanic']
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
    if unit_price < 0:
        raise ValueError("Invalid unit cost. Must be 0 or higher.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        clean_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
            conn.execute(
                "UPDATE items SET cost_per_piece = %s WHERE id = %s",
                (unit_price, item_id)
            )
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

def record_sale(data, user_id, username):
    """
    Records a full sale: validates payment method, inserts sale row,
    logs all item OUT transactions, inserts services.
    Raises ValueError for business logic errors.
    NOTE (future branches): add branch_id filter to payment method lookup.
    """
    # 1) Validate payment method
    try:
        payment_method_id = int(data.get("payment_method_id"))
    except (TypeError, ValueError):
        raise ValueError("Invalid payment method selected.")

    conn = get_db()
    try:
        pm = conn.execute("""
            SELECT id, category, is_active
            FROM payment_methods WHERE id = %s
        """, (payment_method_id,)).fetchone()

        if not pm or pm["is_active"] != 1:
            raise ValueError("Invalid or inactive payment method selected.")

        payment_category = (pm["category"] or "").strip()
        sale_status = "Unresolved" if payment_category == "Debt" else "Paid"

        # 2) Normalize time
        now_obj = datetime.now()
        raw_date = data.get('transaction_date')
        current_minute = now_obj.strftime("%Y-%m-%d %H:%M")

        if raw_date:
            clean_time = raw_date.replace('T', ' ')
            if clean_time[:16] == current_minute or not raw_date:
                clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")
            elif len(clean_time) == 16:
                clean_time += ":00"
        else:
            clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")

        # 3) Duplicate item guard
        raw_items = data.get("items", []) or []
        seen = set()
        dupes = set()

        for it in raw_items:
            iid = it.get("item_id")
            if iid is None:
                continue
            iid = str(iid).strip()
            if not iid:
                continue
            if iid in seen:
                dupes.add(iid)
            seen.add(iid)

        if dupes:
            placeholders = ",".join(["%s"] * len(dupes))
            items_data = conn.execute(
                f"SELECT id, name FROM items WHERE id IN ({placeholders})",
                tuple(dupes)
            ).fetchall()
            labels = [f"{r['name']} (ID {r['id']})" for r in items_data]
            raise ValueError(f"Duplicate item(s) detected: {', '.join(labels)}. Please adjust Qty Out instead.")

        conn.execute("BEGIN")

        # 4) Insert sale
        vehicle_id = data.get("vehicle_id")
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
                (vehicle_id, data.get("customer_id"))
            ).fetchone()
            if not valid_vehicle:
                raise ValueError("Invalid vehicle selected for this customer.")

        sale_row = conn.execute("""
            INSERT INTO sales (
                sales_number, customer_name, customer_id, vehicle_id, total_amount,
                payment_method_id, reference_no, status,
                notes, user_id, transaction_date, mechanic_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            data.get('sales_number'),
            data.get('customer_name'),
            data.get('customer_id') or None,
            vehicle_id,
            data.get('total_amount'),
            payment_method_id,
            data.get('reference_no'),
            sale_status,
            data.get('notes'),
            user_id,
            clean_time,
            data.get('mechanic_id') or None
        )).fetchone()

        new_sale_id = sale_row["id"]

        # 5a) Stock validation — enforced at service level, cannot be bypassed via API
        # NOTE (future branches): filter stock calc by branch_id when ready.
        for item in raw_items:
            item_id = item['item_id']
            qty_requested = int(item['quantity'])

            stock_row = conn.execute("""
                SELECT COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'IN' THEN quantity
                        WHEN transaction_type = 'OUT' THEN -quantity
                        ELSE 0
                    END
                ), 0) AS current_stock
                FROM inventory_transactions
                WHERE item_id = %s
            """, (item_id,)).fetchone()

            current_stock = int(stock_row['current_stock']) if stock_row else 0

            if qty_requested > current_stock:
                name_row = conn.execute("SELECT name FROM items WHERE id = %s", (item_id,)).fetchone()
                item_name = name_row['name'] if name_row else f"Item ID {item_id}"
                raise ValueError(
                    f"Insufficient stock for '{item_name}'. "
                    f"Requested: {qty_requested}, Available: {current_stock}."
                )

        # 5) Items OUT
        for item in raw_items:
            original_price = float(item.get('original_price', 0))
            final_price = float(item.get('final_price', 0))
            discount_percent_whole = float(item.get('discount_percent', 0))
            discount_percent_decimal = discount_percent_whole / 100
            discount_amount = original_price - final_price

            add_transaction(
                item_id=item['item_id'],
                quantity=item['quantity'],
                transaction_type='OUT',
                user_id=user_id,
                user_name=username,
                reference_id=new_sale_id,
                reference_type='SALE',
                change_reason='CUSTOMER_PURCHASE',
                unit_price=original_price,
                transaction_date=clean_time,
                external_conn=conn
            )

            conn.execute("""
                INSERT INTO sales_items (
                    sale_id, item_id, quantity,
                    original_unit_price, discount_percent, discount_amount, final_unit_price,
                    discounted_by, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                new_sale_id, item['item_id'], item['quantity'],
                original_price, discount_percent_decimal, discount_amount, final_price,
                user_id if discount_percent_whole > 0 else None,
                clean_time
            ))

        # 6) Services
        service_subtotal = 0
        for service in data.get('services', []):
            service_id = service.get('service_id')
            if not service_id:
                raise ValueError("Selected service is missing service_id.")

            raw_price = service.get('price')
            if raw_price in (None, ""):
                raise ValueError("Price is required for each selected service.")

            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                raise ValueError("Invalid service price. Please enter a valid amount.")

            if price < 0:
                raise ValueError("Service price cannot be negative.")

            service_subtotal += price
            conn.execute("""
                INSERT INTO sales_services (sale_id, service_id, price)
                VALUES (%s, %s, %s)
            """, (new_sale_id, service_id, price))

        conn.execute(
            "UPDATE sales SET service_fee = %s WHERE id = %s",
            (service_subtotal, new_sale_id)
        )

        service_ids = [s["service_id"] for s in data.get("services", [])]
        item_ids    = [i["item_id"] for i in raw_items]
        log_stamps_for_sale(new_sale_id, data.get("customer_id"), service_ids, item_ids, clean_time, conn)

        conn.commit()
        return data.get('sales_number'), new_sale_id

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# PURCHASE ORDERS
# ─────────────────────────────────────────────

def create_purchase_order(data, user_id, username):
    """
    Creates a new purchase order and logs ORDER transactions.
    Returns the new po_number and po_id.
    NOTE (future branches): add branch_id when ready.
    """
    conn = get_db()
    now_obj = datetime.now()
    clean_time = now_obj.strftime("%Y-%m-%d %H:%M:%S")
    today_str = now_obj.strftime("%Y%m%d")

    try:
        conn.execute("BEGIN")

        count = conn.execute(
            "SELECT COUNT(*) FROM purchase_orders WHERE po_number ILIKE %s",
            (f"PO-{today_str}%",)
        ).fetchone()[0]

        po_number = f"PO-{today_str}-{str(count + 1).zfill(3)}"

        po_row = conn.execute("""
            INSERT INTO purchase_orders (po_number, vendor_name, notes, status, created_by, created_at)
            VALUES (%s, %s, %s, 'PENDING', %s, %s)
            RETURNING id
        """, (po_number, data.get('vendor_name'), data.get('notes'), user_id, clean_time)).fetchone()

        new_po_id = po_row["id"]
        total_order_amount = 0

        for item in data.get('items', []):
            qty = int(item['qty'])
            cost = float(item['cost'])
            total_order_amount += qty * cost

            conn.execute("""
                INSERT INTO po_items (po_id, item_id, quantity_ordered, unit_cost)
                VALUES (%s, %s, %s, %s)
            """, (new_po_id, item['id'], qty, cost))

            add_transaction(
                item_id=item['id'],
                quantity=qty,
                transaction_type='ORDER',
                user_id=user_id,
                user_name=username,
                reference_id=new_po_id,
                reference_type='PURCHASE_ORDER',
                change_reason='ORDER_PLACEMENT',
                unit_price=cost,
                transaction_date=clean_time,
                external_conn=conn
            )

        conn.execute(
            "UPDATE purchase_orders SET total_amount = %s WHERE id = %s",
            (total_order_amount, new_po_id)
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
            (SELECT COUNT(*) FROM po_items WHERE po_id = po.id) as item_count
        FROM purchase_orders po
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()
    return orders


def get_purchase_order_with_items(po_id):
    """Returns a PO and its items. Used by the API detail endpoint."""
    conn = get_db()
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = %s", (po_id,)).fetchone()
    items = conn.execute("""
        SELECT pi.*, i.name
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
    """, (po_id,)).fetchall()
    conn.close()
    return po, items


def get_purchase_order_export_data(po_id):
    """Returns PO + item rows formatted for CSV export."""
    conn = get_db()
    po = conn.execute("""
        SELECT id, po_number, vendor_name, status, created_at, received_at, total_amount
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
            pi.unit_cost
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
        ORDER BY i.name ASC
    """, (po_id,)).fetchall()

    conn.close()
    return po, items


def get_po_for_receive_page(po_id):
    """Returns PO + items needed for the receive page. Returns None if not found."""
    conn = get_db()
    po = conn.execute("SELECT * FROM purchase_orders WHERE id = %s", (po_id,)).fetchone()
    if not po:
        conn.close()
        return None, None

    items = conn.execute("""
        SELECT pi.*, i.name, i.pack_size
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
    """, (po_id,)).fetchall()
    conn.close()
    return po, items


def receive_purchase_order(po_id, received_items, user_id, username):
    """
    Processes stock reception for a PO.
    Handles cost correction, over-receive splitting, and PO status update.
    Raises ValueError for business logic errors.
    NOTE (future branches): add branch_id when ready.
    """
    conn = get_db()
    clean_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn.execute("BEGIN")
        all_completed = True

        for entry in received_items:
            item_id = entry['item_id']
            qty_in = int(entry['qty_received'])
            item_notes = entry.get('notes', '').strip()

            if qty_in <= 0:
                continue

            po_item = conn.execute("""
                SELECT quantity_ordered, quantity_received, unit_cost
                FROM po_items
                WHERE po_id = %s AND item_id = %s
            """, (po_id, item_id)).fetchone()

            if not po_item:
                raise ValueError(f"Item ID {item_id} not found in this PO.")

            already_received = po_item['quantity_received']
            qty_ordered = po_item['quantity_ordered']
            remaining = qty_ordered - already_received
            unit_cost = po_item['unit_cost']

            # Cost self-correction
            item_row = conn.execute(
                "SELECT cost_per_piece FROM items WHERE id = %s", (item_id,)
            ).fetchone()
            current_master_cost = float(item_row["cost_per_piece"] or 0)

            if float(unit_cost) != current_master_cost:
                conn.execute(
                    "UPDATE items SET cost_per_piece = %s WHERE id = %s",
                    (unit_cost, item_id)
                )
                add_transaction(
                    item_id=item_id, quantity=0, transaction_type='IN',
                    user_id=user_id, user_name=username,
                    reference_id=po_id, reference_type='PURCHASE_ORDER',
                    change_reason='COST_PER_PIECE_UPDATED', unit_price=unit_cost,
                    transaction_date=clean_time, external_conn=conn,
                    notes=f"Cost updated from {current_master_cost:.2f} to {float(unit_cost):.2f} via PO receive"
                )

            is_over_receive = qty_in > remaining

            if is_over_receive and not item_notes:
                raise ValueError(f"A reason note is required for over-receiving item ID {item_id}.")

            if is_over_receive:
                if remaining > 0:
                    add_transaction(
                        item_id=item_id, quantity=remaining, transaction_type='IN',
                        user_id=user_id, user_name=username,
                        reference_id=po_id, reference_type='PURCHASE_ORDER',
                        change_reason='PO_ARRIVAL', unit_price=unit_cost,
                        transaction_date=clean_time, external_conn=conn
                    )
                excess = qty_in - remaining
                add_transaction(
                    item_id=item_id, quantity=excess, transaction_type='IN',
                    user_id=user_id, user_name=username,
                    reference_id=po_id, reference_type='PURCHASE_ORDER',
                    change_reason='BONUS_STOCK', unit_price=unit_cost,
                    transaction_date=clean_time, external_conn=conn,
                    notes=item_notes
                )
            else:
                will_still_have_remaining = (already_received + qty_in) < qty_ordered
                arrival_reason = 'PARTIAL_ARRIVAL' if will_still_have_remaining else 'PO_ARRIVAL'
                add_transaction(
                    item_id=item_id, quantity=qty_in, transaction_type='IN',
                    user_id=user_id, user_name=username,
                    reference_id=po_id, reference_type='PURCHASE_ORDER',
                    change_reason=arrival_reason, unit_price=unit_cost,
                    transaction_date=clean_time, external_conn=conn
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

        new_status = 'COMPLETED' if all_completed else 'PARTIAL'
        conn.execute("""
            UPDATE purchase_orders SET status = %s, received_at = %s
            WHERE id = %s
        """, (new_status, clean_time, po_id))

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_po_details_for_api(po_id):
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
            (pi.quantity_ordered * pi.unit_cost) AS subtotal
        FROM po_items pi
        JOIN items i ON pi.item_id = i.id
        WHERE pi.po_id = %s
    """, (po_id,)).fetchall()
    conn.close()

    return {
        "po_number": po['po_number'],
        "vendor_name": po['vendor_name'],
        "status": po['status'] or "Pending",
        "status_class": get_status_class(po['status']),
        "total_amount": po['total_amount'],
        "mode": 'IN' if po['received_at'] else 'ORDER',
        "created_at": format_date(po['created_at'], show_time=True),
        "received_at": format_date(po['received_at'], show_time=True),
        "items": [
            {
                "name": item['name'],
                "quantity_ordered": item['quantity_ordered'],
                "unit_price": float(item['unit_price']),
                "subtotal": float(item['subtotal'])
            }
            for item in items
        ]
    }


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
    elif status == "PENDING":
        return "bg-warning text-dark"
    elif status == "CANCELLED":
        return "bg-danger"
    else:
        return "bg-secondary"

