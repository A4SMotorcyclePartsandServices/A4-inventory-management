from datetime import datetime, timedelta

from db.database import get_db
from services.reports_service import (
    _calculate_range_mechanic_rollups,
    _get_debt_payout_allocations,
    _load_bundles_by_sale,
    _load_services_by_sale,
)


def _num(value):
    return float(value or 0)


def _get_non_cash_floating_metrics(conn, start_date, end_date):
    sale_rows = conn.execute(
        """
        SELECT
            s.id,
            SUM(sp.amount) AS total_amount
        FROM sale_payments sp
        JOIN sales s ON s.id = sp.sale_id
        JOIN payment_methods pm ON pm.id = sp.payment_method_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND s.status = 'Paid'
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
          AND pm.category IN ('Bank', 'Online')
        GROUP BY s.id
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


def _normalize_top_items_limit(value, default=10, max_value=50):
    try:
        limit = int(value or default)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, max_value))


def _get_total_shop_topup(conn, start_date, end_date):
    sales_rows = conn.execute(
        """
        SELECT
            s.id,
            s.transaction_date,
            s.status,
            m.id AS mechanic_id,
            m.name AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup
        FROM sales s
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
          ON mqto.mechanic_id = s.mechanic_id
         AND mqto.quota_date = DATE(s.transaction_date)
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ORDER BY s.transaction_date ASC
        """,
        (start_date, end_date),
    ).fetchall()

    debt_collected_rows = conn.execute(
        """
        SELECT
            dp.sale_id,
            dp.paid_at,
            s.mechanic_id,
            m.name AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
          ON mqto.mechanic_id = s.mechanic_id
         AND mqto.quota_date = DATE(dp.paid_at)
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ORDER BY dp.paid_at ASC
        """,
        (start_date, end_date),
    ).fetchall()

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    services_by_sale = _load_services_by_sale(conn, paid_sale_ids)
    bundles_by_sale = _load_bundles_by_sale(conn, paid_sale_ids)
    debt_payout_rows = _get_debt_payout_allocations(conn, start_date=start_date, end_date=end_date)

    _, totals, _ = _calculate_range_mechanic_rollups(
        sales_rows,
        debt_payout_rows,
        services_by_sale,
        bundles_by_sale,
    )
    return round(_num(totals["total_shop_topup"]), 2)


def get_sales_analytics_snapshot(start_date, end_date, top_items_limit=10, top_items_category=None):
    conn = get_db()
    top_items_limit = _normalize_top_items_limit(top_items_limit)
    requested_top_items_category = str(top_items_category or "").strip()

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
                COALESCE(ss.service_shop_share_total, 0) AS service_shop_share_total,
                COALESCE(sb.bundle_product_revenue, 0) AS bundle_product_revenue,
                COALESCE(sb.bundle_product_cost, 0) AS bundle_product_cost,
                COALESCE(sb.bundle_product_profit, 0) AS bundle_product_profit,
                COALESCE(sb.bundle_service_total, 0) AS bundle_service_total,
                COALESCE(sb.bundle_shop_total, 0) AS bundle_shop_total,
                COALESCE(ss.service_shop_share_total, 0) + COALESCE(sb.bundle_shop_total, 0) AS service_shop_share
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
                    ss.sale_id,
                    SUM(ss.price) AS services_total,
                    SUM(
                        ss.price - (ss.price * COALESCE(m.commission_rate, 0))
                    ) AS service_shop_share_total
                FROM sales_services ss
                LEFT JOIN mechanics m ON m.id = ss.mechanic_id
                GROUP BY ss.sale_id
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

    item_quantity_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(quantity_sold), 0) AS total_items_sold
        FROM (
            SELECT
                COALESCE(SUM(si.quantity), 0) AS quantity_sold
            FROM sales_items si
            JOIN sales s ON s.id = si.sale_id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND s.status = 'Paid'
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'

            UNION ALL

            SELECT
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(sbi.is_included, 0) = 1
                        THEN COALESCE(sbi.quantity, 0)
                        ELSE 0
                    END
                ), 0) AS quantity_sold
            FROM sales_bundle_items sbi
            JOIN sales_bundles sb ON sb.id = sbi.sales_bundle_id
            JOIN sales s ON s.id = sb.sale_id
            WHERE DATE(s.transaction_date) BETWEEN %s AND %s
              AND s.status = 'Paid'
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ) scoped_item_totals
        """,
        (start_date, end_date, start_date, end_date),
    ).fetchone()

    debt_service_shop_row = conn.execute(
        """
        WITH sale_service_totals AS (
            SELECT
                ss.sale_id,
                ss.mechanic_id,
                SUM(ss.price) AS mechanic_service_total
            FROM sales_services ss
            WHERE ss.mechanic_id IS NOT NULL
            GROUP BY ss.sale_id, ss.mechanic_id
        ),
        sale_service_totals_by_sale AS (
            SELECT
                sale_id,
                SUM(mechanic_service_total) AS total_service_total
            FROM sale_service_totals
            GROUP BY sale_id
        ),
        debt_allocations AS (
            SELECT
                dp.id AS debt_payment_id,
                dp.sale_id,
                dp.service_portion,
                sst.mechanic_id,
                COALESCE(m.commission_rate, 0) AS commission_rate,
                ROUND(
                    CASE
                        WHEN COALESCE(st.total_service_total, 0) <= 0 THEN 0
                        ELSE (dp.service_portion * sst.mechanic_service_total / st.total_service_total)
                    END,
                    2
                ) AS allocated_service_portion,
                ROW_NUMBER() OVER (
                    PARTITION BY dp.id, dp.sale_id
                    ORDER BY sst.mechanic_id ASC
                ) AS allocation_index,
                COUNT(*) OVER (
                    PARTITION BY dp.id, dp.sale_id
                ) AS allocation_count,
                SUM(
                    ROUND(
                        CASE
                            WHEN COALESCE(st.total_service_total, 0) <= 0 THEN 0
                            ELSE (dp.service_portion * sst.mechanic_service_total / st.total_service_total)
                        END,
                        2
                    )
                ) OVER (
                    PARTITION BY dp.id, dp.sale_id
                    ORDER BY sst.mechanic_id ASC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS prior_allocated_total
            FROM debt_payments dp
            JOIN sales s ON s.id = dp.sale_id
            JOIN sale_service_totals sst
              ON sst.sale_id = dp.sale_id
            JOIN sale_service_totals_by_sale st
              ON st.sale_id = dp.sale_id
            LEFT JOIN mechanics m
              ON m.id = sst.mechanic_id
            WHERE DATE(dp.paid_at) BETWEEN %s AND %s
              AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        )
        SELECT
            COALESCE(SUM(
                allocation_service_portion
                - (allocation_service_portion * commission_rate)
            ), 0) AS debt_shop_share
        FROM (
            SELECT
                commission_rate,
                GREATEST(
                    0,
                    CASE
                        WHEN allocation_index = allocation_count THEN
                            ROUND(
                                COALESCE(service_portion, 0)
                                - COALESCE(prior_allocated_total, 0),
                                2
                            )
                        ELSE COALESCE(allocated_service_portion, 0)
                    END
                ) AS allocation_service_portion
            FROM debt_allocations
        ) allocated_rows
        """,
        (start_date, end_date),
    ).fetchone()

    total_non_cash_floating = _get_non_cash_floating_metrics(conn, start_date, end_date)
    total_shop_topup = _get_total_shop_topup(conn, start_date, end_date)

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
            COUNT(DISTINCT s.id) AS sale_count,
            COALESCE(SUM(sp.amount), 0) AS total_amount
        FROM sale_payments sp
        JOIN sales s ON s.id = sp.sale_id
        LEFT JOIN payment_methods pm ON pm.id = sp.payment_method_id
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

    top_item_categories_rows = conn.execute(
        """
        SELECT DISTINCT TRIM(category) AS category
        FROM items
        WHERE NULLIF(TRIM(category), '') IS NOT NULL
          AND UPPER(TRIM(category)) <> 'SVC'
        ORDER BY TRIM(category) ASC
        """
    ).fetchall()
    top_item_categories = [row["category"] for row in top_item_categories_rows]
    normalized_category_lookup = {
        str(category).strip().lower(): category
        for category in top_item_categories
    }
    selected_top_items_category = normalized_category_lookup.get(
        requested_top_items_category.lower(),
        "",
    )

    top_items_category_filter_sql = ""
    top_items_category_params = []
    if selected_top_items_category:
        top_items_category_filter_sql = " AND LOWER(TRIM(item_category)) = %s"
        top_items_category_params.append(selected_top_items_category.lower())

    top_items = conn.execute(
        """
        SELECT
            item_name,
            item_category,
            SUM(quantity_sold) AS quantity_sold,
            COALESCE(SUM(total_revenue), 0) AS total_revenue,
            COALESCE(SUM(total_cost), 0) AS total_cost,
            COALESCE(SUM(total_profit), 0) AS total_profit
        FROM (
            SELECT
                i.name AS item_name,
                i.category AS item_category,
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
            GROUP BY i.id, i.name, i.category

            UNION ALL

            SELECT
                sbi.item_name_snapshot AS item_name,
                i.category AS item_category,
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
            LEFT JOIN items i ON i.id = sbi.item_id
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
            GROUP BY sbi.item_name_snapshot, i.category
        ) ranked_items
        WHERE 1 = 1
        """
        + top_items_category_filter_sql +
        """
        GROUP BY item_name, item_category
        ORDER BY quantity_sold DESC, total_revenue DESC, item_name ASC
        LIMIT %s
        """,
        (
            start_date,
            end_date,
            start_date,
            end_date,
            *top_items_category_params,
            top_items_limit,
        ),
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
    total_items_sold = int(round(_num(item_quantity_row["total_items_sold"])))
    average_item_cost_sold = round(
        _num(product_service_row["product_cost"]) / total_items_sold,
        2,
    ) if total_items_sold else 0.0

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
    profit_with_shop_share = round(
        _num(product_service_row["product_profit"]) + shop_share_profit - total_shop_topup,
        2,
    )

    return {
        "summary": {
            "gross_sales": gross_sales,
            "net_sales": net_sales,
            "total_refunds": total_refunds,
            "total_debt_collected": total_debt_collected,
            "average_sale_value": average_sale_value,
            "average_item_cost_sold": average_item_cost_sold,
            "total_transactions": int(summary_row["total_transactions"] or 0),
            "paid_transactions": paid_transactions,
            "total_items_sold": total_items_sold,
            "partial_transactions": int(summary_row["partial_transactions"] or 0),
            "unresolved_transactions": int(summary_row["unresolved_transactions"] or 0),
            "partial_sales_amount": round(_num(summary_row["partial_sales_amount"]), 2),
            "unresolved_sales_amount": round(_num(summary_row["unresolved_sales_amount"]), 2),
            "product_revenue": round(_num(product_service_row["product_revenue"]), 2),
            "product_cost": round(_num(product_service_row["product_cost"]), 2),
            "product_profit": round(_num(product_service_row["product_profit"]), 2),
            "shop_share_profit": shop_share_profit,
            "total_shop_topup": total_shop_topup,
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
                    "category": row["item_category"] or "",
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
        "filters": {
            "top_items_limit": top_items_limit,
            "top_items_category": selected_top_items_category,
            "top_item_categories": top_item_categories,
        },
    }
