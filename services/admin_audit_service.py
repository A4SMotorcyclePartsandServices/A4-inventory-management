import json

from db.database import get_db
from services.audit_service import get_audit_trail
from services.cash_service import get_cash_category_admin_records
from services.password_reset_service import list_password_reset_requests
from services.payables_service import get_payables_audit_log
from services.sales_admin_service import get_sales_paginated
from services.stocktake_access_service import list_stocktake_access_requests
from utils.formatters import format_date


def _to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_audit_dashboard_context(active_tab="users-tab"):
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
    finally:
        conn.close()

    formatted_users = [
        {**dict(user), "created_at": format_date(user["created_at"], show_time=True)}
        for user in users
    ]

    return {
        "users": formatted_users,
        "password_reset_requests": list_password_reset_requests(),
        "stocktake_access_requests": list_stocktake_access_requests(),
        "cash_category_records": get_cash_category_admin_records(),
        "active_tab": active_tab,
    }


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


def get_audit_sales_page(page, start_date, end_date, search, payment_status, has_discount):
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


def _normalize_json_payload(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
    return dict(value)


def get_item_edit_trail_page(page, start_date, end_date, search):
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)

    per_page = 20
    offset = (page - 1) * per_page

    conditions = []
    params = []

    if start_date:
        conditions.append("DATE(h.changed_at) >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("DATE(h.changed_at) <= %s")
        params.append(end_date)
    if search:
        like = f"%{search.strip()}%"
        conditions.append(
            """
            (
                i.name ILIKE %s ESCAPE '\\'
                OR COALESCE(h.changed_by_username, '') ILIKE %s ESCAPE '\\'
                OR COALESCE(h.change_reason, '') ILIKE %s ESCAPE '\\'
            )
            """
        )
        params.extend([like, like, like])

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_db()
    try:
        total_row = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM item_edit_history h
            JOIN items i ON i.id = h.item_id
            {where_clause}
            """,
            params,
        ).fetchone()
        total = int(total_row[0] or 0)
        total_pages = max(1, -(-total // per_page))
        if total and page > total_pages:
            page = total_pages
            offset = (page - 1) * per_page

        rows = conn.execute(
            f"""
            SELECT
                h.id,
                h.item_id,
                i.name AS item_name,
                h.changed_at,
                h.changed_by,
                h.changed_by_username,
                h.change_reason,
                h.before_payload,
                h.after_payload
            FROM item_edit_history h
            JOIN items i ON i.id = h.item_id
            {where_clause}
            ORDER BY h.changed_at DESC, h.id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        ).fetchall()
    finally:
        conn.close()

    formatted_rows = []
    for row in rows:
        before_payload = _normalize_json_payload(row["before_payload"])
        after_payload = _normalize_json_payload(row["after_payload"])
        changed_fields = []
        change_preview = []
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
            "vendor_name",
            "mechanic",
        ):
            before_value = before_payload.get(field_name)
            after_value = after_payload.get(field_name)
            if before_value == after_value:
                continue
            changed_fields.append(field_name)
            if len(change_preview) < 3:
                label = field_name.replace("_", " ").title()
                before_text = "-" if before_value in (None, "") else str(before_value)
                after_text = "-" if after_value in (None, "") else str(after_value)
                change_preview.append(f"{label}: {before_text} -> {after_text}")

        formatted_rows.append({
            "id": int(row["id"]),
            "item_id": int(row["item_id"]),
            "item_name": row["item_name"] or "-",
            "changed_at": format_date(row["changed_at"], show_time=True),
            "changed_by_username": row["changed_by_username"] or "System",
            "change_reason": row["change_reason"] or "",
            "changed_fields": changed_fields,
            "change_preview": change_preview,
        })

    return {
        "rows": formatted_rows,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


__all__ = [
    "_to_bool",
    "get_audit_dashboard_context",
    "get_audit_sales_page",
    "get_audit_trail_page",
    "get_item_edit_trail_page",
    "get_payables_audit_page",
    "toggle_user_active_status",
]
