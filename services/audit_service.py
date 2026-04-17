from db.database import get_db
from utils.formatters import format_date

PER_PAGE = 50


def _build_where_clause(conditions, *, prefix="WHERE"):
    if not conditions:
        return ""
    return f" {prefix} " + " AND ".join(conditions)


def _build_and_clause(conditions):
    if not conditions:
        return ""
    return " AND " + " AND ".join(conditions)


def get_audit_trail(page=1, start_date=None, end_date=None, movement_type=None, has_discount=False):
    """
    Paginated audit trail with optional filters.

    - movement_type: 'IN', 'OUT', 'ORDER', or None for all
    - start_date / end_date: YYYY-MM-DD strings
    - has_discount: when true, include only SALE movement rows from sales that have discounted items
    - Returns dict with rows, pagination metadata

    NOTE (future branches): add branch_id filter here when ready.
    """
    conn = get_db()
    try:
        current_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        current_page = 1

    inv_conditions = []
    inv_params = []
    sale_conditions = []
    sale_params = []

    if start_date:
        inv_conditions.append("DATE(t.transaction_date) >= %s")
        inv_params.append(start_date)
        sale_conditions.append("DATE(s.transaction_date) >= %s")
        sale_params.append(start_date)

    if end_date:
        inv_conditions.append("DATE(t.transaction_date) <= %s")
        inv_params.append(end_date)
        sale_conditions.append("DATE(s.transaction_date) <= %s")
        sale_params.append(end_date)

    if movement_type:
        inv_conditions.append("t.transaction_type = %s")
        inv_params.append(movement_type)
        if movement_type != "OUT":
            sale_conditions.append("1 = 0")

    if has_discount:
        inv_conditions.append("""
            (
                t.reference_type = 'SALE'
                AND EXISTS (
                    SELECT 1
                    FROM sales_items si
                    WHERE si.sale_id = t.reference_id
                      AND (si.discount_percent > 0 OR si.discount_amount > 0)
                )
            )
        """)
        sale_conditions.append("""
            EXISTS (
                SELECT 1
                FROM sales_items si
                WHERE si.sale_id = s.id
                  AND (si.discount_percent > 0 OR si.discount_amount > 0)
            )
        """)

    inv_where_clause = _build_where_clause(inv_conditions)
    sale_extra_clause = _build_and_clause(sale_conditions)

    base_query = """
        SELECT
            MIN(t.id) AS audit_group_id,
            t.transaction_date,
            t.transaction_type,
            SUM(t.quantity) AS total_qty,
            t.user_name,
            t.change_reason,
            t.reference_type,
            t.reference_id,
            COALESCE(NULLIF(MAX(t.notes), ''), MAX(s.notes)) AS notes,
            s.sales_number,
            po.po_number,
            STRING_AGG(i.name::text, ', ' ORDER BY i.name) AS items_summary
        FROM inventory_transactions t
        JOIN items i ON t.item_id = i.id
        LEFT JOIN sales s
            ON t.reference_id = s.id AND t.reference_type = 'SALE'
        LEFT JOIN purchase_orders po
            ON t.reference_id = po.id AND t.reference_type = 'PURCHASE_ORDER'
    """ + inv_where_clause + """
        GROUP BY
            t.reference_id,
            t.transaction_date,
            t.transaction_type,
            t.change_reason,
            t.user_name,
            t.reference_type,
            s.sales_number,
            po.po_number

        UNION ALL

        SELECT
            NULL AS audit_group_id,
            s.transaction_date,
            'OUT' AS transaction_type,
            0 AS total_qty,
            COALESCE(u.username, 'System') AS user_name,
            'SERVICE_ONLY_SALE' AS change_reason,
            'SALE' AS reference_type,
            s.id AS reference_id,
            s.notes,
            s.sales_number,
            NULL AS po_number,
            COALESCE((
                SELECT STRING_AGG(sv.name::text, ', ' ORDER BY sv.name)
                FROM sales_services ss
                JOIN services sv ON sv.id = ss.service_id
                WHERE ss.sale_id = s.id
            ), 'Service-only sale') AS items_summary
        FROM sales s
        LEFT JOIN users u ON u.id = s.user_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM inventory_transactions t2
            WHERE t2.reference_type = 'SALE'
              AND CAST(t2.reference_id AS TEXT) = CAST(s.id AS TEXT)
        )
    """ + sale_extra_clause

    count_query = """
        SELECT COUNT(*) FROM (
    """ + base_query + """
        ) audit_rows
    """
    total = conn.execute(count_query, inv_params + sale_params).fetchone()[0]
    total_pages = max(1, -(-total // PER_PAGE))
    current_page = min(current_page, total_pages)
    offset = (current_page - 1) * PER_PAGE

    data_query = base_query + """
        ORDER BY transaction_date DESC
        LIMIT %s OFFSET %s
    """

    rows = conn.execute(data_query, inv_params + sale_params + [PER_PAGE, offset]).fetchall()
    conn.close()

    def _build_display_reference(row):
        if row["reference_type"] == "PURCHASE_ORDER":
            return row["po_number"] or row["reference_id"]
        if row["reference_type"] == "SALE":
            if row["sales_number"]:
                return row["sales_number"]
            if str(row.get("change_reason") or "").strip().upper() == "MECHANIC_SUPPLY":
                try:
                    return f"MS-{int(row['reference_id']):06d}"
                except (TypeError, ValueError):
                    return f"MS-{row['reference_id']}"
        return row["reference_id"]

    formatted = [
        {
            **dict(r),
            "display_reference": _build_display_reference(r),
            "transaction_date_raw": r["transaction_date"].isoformat() if r["transaction_date"] else None,
            "transaction_date": format_date(r["transaction_date"], show_time=True),
        }
        for r in rows
    ]

    return {
        "rows": formatted,
        "total": total,
        "page": current_page,
        "per_page": PER_PAGE,
        "total_pages": total_pages,
    }

