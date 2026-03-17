from db.database import get_db
from utils.formatters import format_date

PER_PAGE = 50
VALID_SALE_STATUSES = {"Paid", "Partial", "Unresolved"}

def get_sales_paginated(page=1, start_date=None, end_date=None, search=None, has_discount=False, payment_status=None):
    """
    Paginated sales history for the admin panel.
    Searchable by receipt number or customer name.
    Optional has_discount=True filters to sales containing discounted items.
    
    NOTE (future branches): add branch_id filter here when ready.
    """
    conn = get_db()
    offset = (page - 1) * PER_PAGE

    conditions = []
    params = []

    if start_date:
        conditions.append("DATE(s.transaction_date) >= %s")
        params.append(start_date)
    if end_date:
        conditions.append("DATE(s.transaction_date) <= %s")
        params.append(end_date)
    if search:
        conditions.append("(s.sales_number ILIKE %s OR s.customer_name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if payment_status:
        if payment_status not in VALID_SALE_STATUSES:
            raise ValueError("Invalid payment status")
        conditions.append("s.status = %s")
        params.append(payment_status)
    if has_discount:
        conditions.append("""
            EXISTS (
                SELECT 1
                FROM sales_items si
                WHERE si.sale_id = s.id AND (si.discount_percent > 0 OR si.discount_amount > 0)
            )
        """)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = conn.execute(f"""
        SELECT COUNT(*) FROM sales s {where_clause}
    """, params).fetchone()[0]

    total_pages = max(1, -(-total // PER_PAGE))

    rows = conn.execute(f"""
        SELECT
            s.id,
            s.transaction_date,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            pm.name AS payment_method_name
        FROM sales s
        LEFT JOIN payment_methods pm ON s.payment_method_id = pm.id
        {where_clause}
        ORDER BY s.transaction_date DESC
        LIMIT %s OFFSET %s
    """, params + [PER_PAGE, offset]).fetchall()

    conn.close()

    formatted = [
        {**dict(r), "transaction_date": format_date(r["transaction_date"], show_time=True)}
        for r in rows
    ]

    return {
        "rows":        formatted,
        "total":       total,
        "page":        page,
        "per_page":    PER_PAGE,
        "total_pages": total_pages,
    }

