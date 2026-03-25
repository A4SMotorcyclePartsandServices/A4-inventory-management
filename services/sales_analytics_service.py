from datetime import datetime, timedelta

from db.database import get_db


def _num(value):
    return float(value or 0)


def _parse_iso_date(value):
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _daterange(start_date, end_date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def get_sales_analytics_snapshot(start_date, end_date):
    conn = get_db()

    summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_transactions,
            COUNT(*) FILTER (WHERE s.status = 'Paid') AS paid_transactions,
            COUNT(*) FILTER (WHERE s.status = 'Partial') AS partial_transactions,
            COUNT(*) FILTER (WHERE s.status = 'Unresolved') AS unresolved_transactions,
            COALESCE(SUM(CASE WHEN s.status = 'Paid' THEN s.total_amount ELSE 0 END), 0) AS gross_sales,
            COALESCE(SUM(CASE WHEN s.status = 'Partial' THEN s.total_amount ELSE 0 END), 0) AS partial_sales_amount,
            COALESCE(SUM(CASE WHEN s.status = 'Unresolved' THEN s.total_amount ELSE 0 END), 0) AS unresolved_sales_amount
        FROM sales s
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
        """,
        (start_date, end_date),
    ).fetchone()

    debt_row = conn.execute(
        """
        SELECT COALESCE(SUM(dp.amount_paid), 0) AS total_debt_collected
        FROM debt_payments dp
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
        """,
        (start_date, end_date),
    ).fetchone()

    refund_row = conn.execute(
        """
        SELECT COALESCE(SUM(sr.refund_amount), 0) AS total_refunds
        FROM sale_refunds sr
        WHERE DATE(sr.refund_date) BETWEEN %s AND %s
        """,
        (start_date, end_date),
    ).fetchone()

    product_service_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(items_total), 0) AS product_revenue,
            COALESCE(SUM(item_cost_total), 0) AS product_cost,
            COALESCE(SUM(item_profit_total), 0) AS product_profit,
            COALESCE(SUM(services_total), 0) AS service_revenue
        FROM (
            SELECT
                s.id,
                COALESCE(si.items_total, 0) AS items_total,
                COALESCE(si.item_cost_total, 0) AS item_cost_total,
                COALESCE(si.item_profit_total, 0) AS item_profit_total,
                COALESCE(ss.services_total, 0) AS services_total
            FROM sales s
            LEFT JOIN (
                SELECT
                    sale_id,
                    SUM(quantity * final_unit_price) AS items_total,
                    SUM(quantity * cost_per_piece_snapshot) AS item_cost_total,
                    SUM(quantity * (final_unit_price - cost_per_piece_snapshot)) AS item_profit_total
                FROM sales_items
                GROUP BY sale_id
            ) si ON si.sale_id = s.id
            LEFT JOIN (
                SELECT
                    sale_id,
                    SUM(price) AS services_total
                FROM sales_services
                GROUP BY sale_id
            ) ss ON ss.sale_id = s.id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND s.status = 'Paid'
        ) scoped_sales
        """,
        (start_date, end_date),
    ).fetchone()

    status_rows = conn.execute(
        """
        SELECT
            s.status,
            COUNT(*) AS sale_count,
            COALESCE(SUM(s.total_amount), 0) AS total_amount
        FROM sales s
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
        GROUP BY s.status
        ORDER BY s.status ASC
        """,
        (start_date, end_date),
    ).fetchall()

    payment_method_rows = conn.execute(
        """
        SELECT
            COALESCE(pm.name, 'N/A') AS payment_method,
            COUNT(*) AS sale_count,
            COALESCE(SUM(s.total_amount), 0) AS total_amount
        FROM sales s
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
        GROUP BY COALESCE(pm.name, 'N/A')
        ORDER BY total_amount DESC, payment_method ASC
        """,
        (start_date, end_date),
    ).fetchall()

    sales_trend_rows = conn.execute(
        """
        SELECT
            DATE(s.transaction_date) AS day,
            COUNT(*) FILTER (WHERE s.status = 'Paid') AS paid_count,
            COALESCE(SUM(CASE WHEN s.status = 'Paid' THEN s.total_amount ELSE 0 END), 0) AS gross_sales
        FROM sales s
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
        GROUP BY DATE(s.transaction_date)
        ORDER BY day ASC
        """,
        (start_date, end_date),
    ).fetchall()

    refund_trend_rows = conn.execute(
        """
        SELECT
            DATE(sr.refund_date) AS day,
            COALESCE(SUM(sr.refund_amount), 0) AS refund_total
        FROM sale_refunds sr
        WHERE DATE(sr.refund_date) BETWEEN %s AND %s
        GROUP BY DATE(sr.refund_date)
        ORDER BY day ASC
        """,
        (start_date, end_date),
    ).fetchall()

    top_items = conn.execute(
        """
        SELECT
            i.name AS item_name,
            SUM(si.quantity) AS quantity_sold,
            COALESCE(SUM(si.quantity * si.final_unit_price), 0) AS total_revenue,
            COALESCE(SUM(si.quantity * si.cost_per_piece_snapshot), 0) AS total_cost,
            COALESCE(SUM(si.quantity * (si.final_unit_price - si.cost_per_piece_snapshot)), 0) AS total_profit
        FROM sales_items si
        JOIN sales s ON s.id = si.sale_id
        JOIN items i ON i.id = si.item_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
        GROUP BY i.id, i.name
        ORDER BY quantity_sold DESC, total_revenue DESC, i.name ASC
        LIMIT 10
        """,
        (start_date, end_date),
    ).fetchall()

    top_services = conn.execute(
        """
        SELECT
            sv.name AS service_name,
            COUNT(*) AS times_sold,
            COALESCE(SUM(ss.price), 0) AS total_revenue
        FROM sales_services ss
        JOIN sales s ON s.id = ss.sale_id
        JOIN services sv ON sv.id = ss.service_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
        GROUP BY sv.id, sv.name
        ORDER BY total_revenue DESC, times_sold DESC, sv.name ASC
        LIMIT 10
        """,
        (start_date, end_date),
    ).fetchall()

    top_customers = conn.execute(
        """
        SELECT
            COALESCE(c.customer_name, s.customer_name, 'Walk-in') AS customer_name,
            COUNT(*) AS sale_count,
            COALESCE(SUM(s.total_amount), 0) AS total_revenue
        FROM sales s
        LEFT JOIN customers c ON c.id = s.customer_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
        GROUP BY COALESCE(c.customer_name, s.customer_name, 'Walk-in')
        ORDER BY total_revenue DESC, sale_count DESC, customer_name ASC
        LIMIT 10
        """,
        (start_date, end_date),
    ).fetchall()

    conn.close()

    gross_sales = round(_num(summary_row["gross_sales"]), 2)
    total_refunds = round(_num(refund_row["total_refunds"]), 2)
    total_debt_collected = round(_num(debt_row["total_debt_collected"]), 2)
    net_sales = round(gross_sales + total_debt_collected - total_refunds, 2)
    paid_transactions = int(summary_row["paid_transactions"] or 0)
    average_sale_value = round(gross_sales / paid_transactions, 2) if paid_transactions else 0.0

    trend_map = {
        str(row["day"]): {
            "gross_sales": round(_num(row["gross_sales"]), 2),
            "paid_count": int(row["paid_count"] or 0),
        }
        for row in sales_trend_rows
    }
    refund_map = {
        str(row["day"]): round(_num(row["refund_total"]), 2)
        for row in refund_trend_rows
    }

    trend_labels = []
    gross_series = []
    transaction_series = []
    refund_series = []
    for day in _daterange(_parse_iso_date(start_date), _parse_iso_date(end_date)):
        iso_day = day.isoformat()
        trend_labels.append(day.strftime("%b %d"))
        gross_series.append(trend_map.get(iso_day, {}).get("gross_sales", 0.0))
        transaction_series.append(trend_map.get(iso_day, {}).get("paid_count", 0))
        refund_series.append(refund_map.get(iso_day, 0.0))

    status_lookup = {
        row["status"]: {
            "sale_count": int(row["sale_count"] or 0),
            "total_amount": round(_num(row["total_amount"]), 2),
        }
        for row in status_rows
    }
    status_order = ["Paid", "Partial", "Unresolved"]
    status_breakdown = [
        {
            "status": status,
            "sale_count": status_lookup.get(status, {}).get("sale_count", 0),
            "total_amount": status_lookup.get(status, {}).get("total_amount", 0.0),
        }
        for status in status_order
    ]

    payment_method_breakdown = [
        {
            "payment_method": row["payment_method"],
            "sale_count": int(row["sale_count"] or 0),
            "total_amount": round(_num(row["total_amount"]), 2),
        }
        for row in payment_method_rows
    ]

    return {
        "summary": {
            "gross_sales": gross_sales,
            "net_sales": net_sales,
            "total_refunds": total_refunds,
            "total_debt_collected": total_debt_collected,
            "average_sale_value": average_sale_value,
            "total_transactions": int(summary_row["total_transactions"] or 0),
            "paid_transactions": paid_transactions,
            "partial_transactions": int(summary_row["partial_transactions"] or 0),
            "unresolved_transactions": int(summary_row["unresolved_transactions"] or 0),
            "partial_sales_amount": round(_num(summary_row["partial_sales_amount"]), 2),
            "unresolved_sales_amount": round(_num(summary_row["unresolved_sales_amount"]), 2),
            "product_revenue": round(_num(product_service_row["product_revenue"]), 2),
            "product_cost": round(_num(product_service_row["product_cost"]), 2),
            "product_profit": round(_num(product_service_row["product_profit"]), 2),
            "service_revenue": round(_num(product_service_row["service_revenue"]), 2),
        },
        "charts": {
            "sales_trend": {
                "labels": trend_labels,
                "gross_sales": gross_series,
                "paid_transactions": transaction_series,
                "refunds": refund_series,
            },
            "status_breakdown": {
                "labels": [row["status"] for row in status_breakdown],
                "counts": [row["sale_count"] for row in status_breakdown],
                "amounts": [row["total_amount"] for row in status_breakdown],
            },
            "payment_methods": {
                "labels": [row["payment_method"] for row in payment_method_breakdown],
                "amounts": [row["total_amount"] for row in payment_method_breakdown],
            },
            "revenue_mix": {
                "labels": ["Items", "Services"],
                "amounts": [
                    round(_num(product_service_row["product_revenue"]), 2),
                    round(_num(product_service_row["service_revenue"]), 2),
                ],
            },
        },
        "tables": {
            "top_items": [
                {
                    "name": row["item_name"],
                    "quantity_sold": int(row["quantity_sold"] or 0),
                    "total_revenue": round(_num(row["total_revenue"]), 2),
                    "total_cost": round(_num(row["total_cost"]), 2),
                    "total_profit": round(_num(row["total_profit"]), 2),
                }
                for row in top_items
            ],
            "top_services": [
                {
                    "name": row["service_name"],
                    "times_sold": int(row["times_sold"] or 0),
                    "total_revenue": round(_num(row["total_revenue"]), 2),
                }
                for row in top_services
            ],
            "top_customers": [
                {
                    "name": row["customer_name"],
                    "sale_count": int(row["sale_count"] or 0),
                    "total_revenue": round(_num(row["total_revenue"]), 2),
                }
                for row in top_customers
            ],
            "payment_methods": payment_method_breakdown,
            "status_breakdown": status_breakdown,
        },
    }
