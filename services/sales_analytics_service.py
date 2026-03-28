from datetime import datetime, timedelta

from db.database import get_db


def _num(value):
    return float(value or 0)


def _get_non_cash_floating_metrics(conn, start_date, end_date):
    sale_rows = conn.execute(
        """
        SELECT
            s.id,
            s.total_amount
        FROM sales s
        JOIN payment_methods pm ON pm.id = s.payment_method_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
          AND pm.category IN ('Bank', 'Online')
        """,
        (start_date, end_date),
    ).fetchall()

    debt_payment_rows = conn.execute(
        """
        SELECT
            dp.id,
            dp.amount_paid
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
          AND pm.category IN ('Bank', 'Online')
        """,
        (start_date, end_date),
    ).fetchall()

    if not sale_rows and not debt_payment_rows:
        return 0.0

    sale_totals = {
        int(row["id"]): round(_num(row["total_amount"]), 2)
        for row in sale_rows
    }
    claimed_sale_ids = set()
    if sale_totals:
        sale_ids = list(sale_totals.keys())
        placeholders = ",".join(["%s"] * len(sale_ids))
        claimed_rows = conn.execute(
            f"""
            SELECT DISTINCT cfc.sale_id
            FROM cash_float_claims cfc
            JOIN cash_entries ce ON ce.id = cfc.cash_entry_id
            WHERE cfc.sale_id IN ({placeholders})
              AND COALESCE(ce.is_deleted, FALSE) = FALSE
              AND DATE(ce.created_at) <= %s
            """,
            sale_ids + [end_date],
        ).fetchall()
        claimed_sale_ids = {int(row["sale_id"]) for row in claimed_rows}

    debt_payment_totals = {
        int(row["id"]): round(_num(row["amount_paid"]), 2)
        for row in debt_payment_rows
    }
    claimed_debt_payment_ids = set()
    if debt_payment_totals:
        debt_payment_ids = list(debt_payment_totals.keys())
        placeholders = ",".join(["%s"] * len(debt_payment_ids))
        claimed_rows = conn.execute(
            f"""
            SELECT DISTINCT cdpc.debt_payment_id
            FROM cash_debt_payment_claims cdpc
            JOIN cash_entries ce ON ce.id = cdpc.cash_entry_id
            WHERE cdpc.debt_payment_id IN ({placeholders})
              AND COALESCE(ce.is_deleted, FALSE) = FALSE
              AND DATE(ce.created_at) <= %s
            """,
            debt_payment_ids + [end_date],
        ).fetchall()
        claimed_debt_payment_ids = {int(row["debt_payment_id"]) for row in claimed_rows}

    return round(
        sum(amount for sale_id, amount in sale_totals.items() if sale_id not in claimed_sale_ids)
        + sum(
            amount
            for debt_payment_id, amount in debt_payment_totals.items()
            if debt_payment_id not in claimed_debt_payment_ids
        ),
        2,
    )


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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        """,
        (start_date, end_date),
    ).fetchone()

    debt_row = conn.execute(
        """
        SELECT COALESCE(SUM(dp.amount_paid), 0) AS total_debt_collected
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
            COALESCE(SUM(items_total + bundle_product_revenue), 0) AS product_revenue,
            COALESCE(SUM(item_cost_total + bundle_product_cost), 0) AS product_cost,
            COALESCE(SUM(item_profit_total + bundle_product_profit), 0) AS product_profit,
            COALESCE(SUM(services_total + bundle_service_total + bundle_shop_total), 0) AS service_revenue,
            COALESCE(SUM(service_shop_share), 0) AS shop_share_profit
        FROM (
            SELECT
                s.id,
                COALESCE(si.items_total, 0) AS items_total,
                COALESCE(si.item_cost_total, 0) AS item_cost_total,
                COALESCE(si.item_profit_total, 0) AS item_profit_total,
                COALESCE(ss.services_total, 0) AS services_total,
                COALESCE(sb.bundle_product_revenue, 0) AS bundle_product_revenue,
                COALESCE(sb.bundle_product_cost, 0) AS bundle_product_cost,
                COALESCE(sb.bundle_product_profit, 0) AS bundle_product_profit,
                COALESCE(sb.bundle_service_total, 0) AS bundle_service_total,
                COALESCE(sb.bundle_shop_total, 0) AS bundle_shop_total,
                (
                    (
                        COALESCE(ss.services_total, 0)
                        + COALESCE(sb.bundle_service_total, 0)
                    ) * (1 - COALESCE(m.commission_rate, 0))
                ) + COALESCE(sb.bundle_shop_total, 0) AS service_shop_share
            FROM sales s
            LEFT JOIN mechanics m ON m.id = s.mechanic_id
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
            LEFT JOIN (
                SELECT
                    sb.sale_id,
                    SUM(COALESCE(sb.item_value_reference_snapshot, 0)) AS bundle_product_revenue,
                    SUM(
                        CASE
                            WHEN COALESCE(sbi.is_included, 0) = 1
                            THEN COALESCE(sbi.quantity, 0) * COALESCE(sbi.cost_per_piece_snapshot, 0)
                            ELSE 0
                        END
                    ) AS bundle_product_cost,
                    SUM(COALESCE(sb.item_value_reference_snapshot, 0))
                    - SUM(
                        CASE
                            WHEN COALESCE(sbi.is_included, 0) = 1
                            THEN COALESCE(sbi.quantity, 0) * COALESCE(sbi.cost_per_piece_snapshot, 0)
                            ELSE 0
                        END
                    ) AS bundle_product_profit,
                    SUM(COALESCE(sb.mechanic_share_snapshot, 0)) AS bundle_service_total,
                    SUM(COALESCE(sb.shop_share_snapshot, 0)) AS bundle_shop_total
                FROM sales_bundles sb
                LEFT JOIN sales_bundle_items sbi ON sbi.sales_bundle_id = sb.id
                GROUP BY sb.sale_id
            ) sb ON sb.sale_id = s.id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND s.status = 'Paid'
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ) scoped_sales
        """,
        (start_date, end_date),
    ).fetchone()

    debt_service_shop_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(
                COALESCE(dp.service_portion, 0)
                - (COALESCE(dp.service_portion, 0) * COALESCE(m.commission_rate, 0))
            ), 0) AS debt_shop_share
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        """,
        (start_date, end_date),
    ).fetchone()

    total_non_cash_floating = _get_non_cash_floating_metrics(conn, start_date, end_date)

    status_rows = conn.execute(
        """
        SELECT
            s.status,
            COUNT(*) AS sale_count,
            COALESCE(SUM(s.total_amount), 0) AS total_amount
        FROM sales s
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
            item_name,
            SUM(quantity_sold) AS quantity_sold,
            COALESCE(SUM(total_revenue), 0) AS total_revenue,
            COALESCE(SUM(total_cost), 0) AS total_cost,
            COALESCE(SUM(total_profit), 0) AS total_profit
        FROM (
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
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
            GROUP BY i.id, i.name

            UNION ALL

            SELECT
                sbi.item_name_snapshot AS item_name,
                SUM(COALESCE(sbi.quantity, 0)) AS quantity_sold,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(bundle_totals.bundle_reference_total, 0) > 0
                        THEN COALESCE(sb.item_value_reference_snapshot, 0)
                             * (
                                 (COALESCE(sbi.selling_price_snapshot, 0) * COALESCE(sbi.quantity, 0))
                                 / bundle_totals.bundle_reference_total
                             )
                        ELSE 0
                    END
                ), 0) AS total_revenue,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(sbi.is_included, 0) = 1
                        THEN COALESCE(sbi.quantity, 0) * COALESCE(sbi.cost_per_piece_snapshot, 0)
                        ELSE 0
                    END
                ), 0) AS total_cost,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(bundle_totals.bundle_reference_total, 0) > 0
                        THEN (
                            COALESCE(sb.item_value_reference_snapshot, 0)
                            * (
                                (COALESCE(sbi.selling_price_snapshot, 0) * COALESCE(sbi.quantity, 0))
                                / bundle_totals.bundle_reference_total
                            )
                        ) - (
                            CASE
                                WHEN COALESCE(sbi.is_included, 0) = 1
                                THEN COALESCE(sbi.quantity, 0) * COALESCE(sbi.cost_per_piece_snapshot, 0)
                                ELSE 0
                            END
                        )
                        ELSE 0
                    END
                ), 0) AS total_profit
            FROM sales_bundles sb
            JOIN sales s ON s.id = sb.sale_id
            JOIN sales_bundle_items sbi ON sbi.sales_bundle_id = sb.id
            LEFT JOIN (
                SELECT
                    sales_bundle_id,
                    SUM(COALESCE(selling_price_snapshot, 0) * COALESCE(quantity, 0)) AS bundle_reference_total
                FROM sales_bundle_items
                GROUP BY sales_bundle_id
            ) bundle_totals ON bundle_totals.sales_bundle_id = sb.id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND s.status = 'Paid'
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
            GROUP BY sbi.item_name_snapshot
        ) ranked_items
        GROUP BY item_name
        ORDER BY quantity_sold DESC, total_revenue DESC, item_name ASC
        LIMIT 10
        """,
        (start_date, end_date, start_date, end_date),
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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        GROUP BY COALESCE(c.customer_name, s.customer_name, 'Walk-in')
        ORDER BY total_revenue DESC, sale_count DESC, customer_name ASC
        LIMIT 10
        """,
        (start_date, end_date),
    ).fetchall()

    mechanic_supply_analytics = conn.execute(
        """
        WITH mechanic_supply_item_totals AS (
            SELECT
                COALESCE(s.mechanic_id, 0) AS mechanic_key,
                COALESCE(m.name, 'Unassigned') AS mechanic_name,
                i.name AS item_name,
                SUM(si.quantity) AS total_quantity
            FROM sales s
            LEFT JOIN mechanics m ON m.id = s.mechanic_id
            JOIN sales_items si ON si.sale_id = s.id
            JOIN items i ON i.id = si.item_id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND COALESCE(s.transaction_class, 'NEW_SALE') = 'MECHANIC_SUPPLY'
            GROUP BY COALESCE(s.mechanic_id, 0), COALESCE(m.name, 'Unassigned'), i.name
        ),
        mechanic_supply_item_ranked AS (
            SELECT
                mechanic_key,
                mechanic_name,
                item_name,
                total_quantity,
                ROW_NUMBER() OVER (
                    PARTITION BY mechanic_key
                    ORDER BY total_quantity DESC, item_name ASC
                ) AS row_no
            FROM mechanic_supply_item_totals
        )
        SELECT
            COALESCE(m.name, 'Unassigned') AS mechanic_name,
            COUNT(DISTINCT s.id) AS transaction_count,
            COALESCE(SUM(si.quantity), 0) AS total_items,
            COUNT(DISTINCT si.item_id) AS distinct_items,
            MAX(CASE WHEN ranked.row_no = 1 THEN ranked.item_name END) AS most_requested_item,
            COALESCE(SUM(si.quantity * si.final_unit_price), 0) AS total_cost
        FROM sales s
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        LEFT JOIN sales_items si ON si.sale_id = s.id
        LEFT JOIN mechanic_supply_item_ranked ranked
            ON ranked.mechanic_key = COALESCE(s.mechanic_id, 0)
           AND ranked.row_no = 1
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') = 'MECHANIC_SUPPLY'
        GROUP BY COALESCE(s.mechanic_id, 0), COALESCE(m.name, 'Unassigned')
        ORDER BY total_cost DESC, transaction_count DESC, mechanic_name ASC
        LIMIT 10
        """,
        (start_date, end_date, start_date, end_date),
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

    shop_share_profit = round(
        _num(product_service_row["shop_share_profit"]) + _num(debt_service_shop_row["debt_shop_share"]),
        2,
    )
    profit_with_shop_share = round(_num(product_service_row["product_profit"]) + shop_share_profit, 2)

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
            "shop_share_profit": shop_share_profit,
            "profit_with_shop_share": profit_with_shop_share,
            "service_revenue": round(_num(product_service_row["service_revenue"]), 2),
            "total_non_cash_floating": total_non_cash_floating,
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
            "mechanic_supply_analytics": [
                {
                    "mechanic_name": row["mechanic_name"],
                    "transaction_count": int(row["transaction_count"] or 0),
                    "total_items": int(row["total_items"] or 0),
                    "distinct_items": int(row["distinct_items"] or 0),
                    "most_requested_item": row["most_requested_item"] or "-",
                    "total_cost": round(_num(row["total_cost"]), 2),
                }
                for row in mechanic_supply_analytics
            ],
        },
    }
