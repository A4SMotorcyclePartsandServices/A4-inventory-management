import re
from datetime import datetime

from werkzeug.security import generate_password_hash

from db.database import get_db
from services.audit_service import get_audit_trail
from services.payables_service import get_payables_audit_log
from services.password_reset_service import list_password_reset_requests
from services.sales_admin_service import get_sales_paginated
from services.transactions_service import get_sale_refund_context
from utils.formatters import format_date, norm_text


def _to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_manage_users_context(active_tab="users-tab"):
    conn = get_db()
    try:
        users = conn.execute(
            """
            SELECT u.id, u.username, u.phone_no, u.role, u.created_at, u.is_active,
                   creator.username AS creator_name
            FROM users u
            LEFT JOIN users creator ON u.created_by = creator.id
            ORDER BY u.created_at DESC
            """
        ).fetchall()
        mechanics = conn.execute(
            "SELECT * FROM mechanics ORDER BY name ASC"
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
    finally:
        conn.close()

    formatted_users = [
        {**dict(user), "created_at": format_date(user["created_at"], show_time=True)}
        for user in users
    ]

    return {
        "users": formatted_users,
        "mechanics": mechanics,
        "password_reset_requests": list_password_reset_requests(),
        "services_list": services_list,
        "categories": categories,
        "payment_methods": payment_methods,
        "active_tab": active_tab,
    }


def create_staff_user(username, password, phone_no, created_by):
    normalized_username = str(username or "").strip()
    normalized_phone_no = str(phone_no or "").strip()

    if not normalized_username or not str(password or "").strip() or not normalized_phone_no:
        raise ValueError("Username, password, and phone number are required.")

    conn = get_db()
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


def toggle_user_active_status(user_id):
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT role, is_active, username FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if not user:
            return {"status": "missing"}

        if user["role"] == "admin":
            return {"status": "forbidden_admin"}

        was_active = user["is_active"]
        new_status = 0 if was_active == 1 else 1
        conn.execute(
            "UPDATE users SET is_active = %s WHERE id = %s",
            (new_status, user_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "username": user["username"],
            "was_active": was_active,
            "new_status": new_status,
        }
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


def get_audit_trail_page(page, start_date, end_date, movement_type, has_discount):
    valid_types = {"IN", "OUT", "ORDER", None}
    if movement_type not in valid_types:
        raise ValueError("Invalid movement type")

    return get_audit_trail(
        page=page,
        start_date=start_date,
        end_date=end_date,
        movement_type=movement_type,
        has_discount=has_discount,
    )


def get_admin_sales_page(page, start_date, end_date, search, payment_status, has_discount):
    valid_statuses = {"Paid", "Partial", "Unresolved", None}
    if payment_status not in valid_statuses:
        raise ValueError("Invalid payment status")

    return get_sales_paginated(
        page=page,
        start_date=start_date,
        end_date=end_date,
        search=search,
        has_discount=has_discount,
        payment_status=payment_status,
    )


def get_payables_audit_page(
    page,
    start_date,
    end_date,
    event_type,
    source_type,
    payee_search,
    cheque_no_search,
):
    return get_payables_audit_log(
        page=page,
        start_date=start_date,
        end_date=end_date,
        event_type=event_type,
        source_type=source_type,
        payee_search=payee_search,
        cheque_no_search=cheque_no_search,
    )


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
    "_to_bool",
    "add_mechanic_record",
    "add_payment_method_record",
    "add_service_record",
    "create_staff_user",
    "get_admin_sales_page",
    "get_audit_trail_page",
    "get_item_details_payload",
    "get_manage_users_context",
    "get_manual_in_details",
    "get_payables_audit_page",
    "get_sale_refund_context",
    "toggle_mechanic_active_status",
    "toggle_payment_method_active_status",
    "toggle_service_active_status",
    "toggle_user_active_status",
]
