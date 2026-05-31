from db.database import get_db
from services.inventory_service import DEMAND_OUT_REASONS


def normalize_top_items_limit(value, default=10, max_value=50):
    try:
        numeric_value = int(value)
    except (TypeError, ValueError):
        numeric_value = default
    return max(1, min(numeric_value, max_value))


def _current_stock_subquery():
    return """
        SELECT
            item_id,
            SUM(
                CASE
                    WHEN transaction_type = 'IN' THEN quantity
                    WHEN transaction_type = 'OUT' THEN -quantity
                    ELSE 0
                END
            ) AS current_stock
        FROM inventory_transactions
        GROUP BY item_id
    """


def _fetch_top_item_categories(conn, exclude_services=False):
    service_filter = "AND UPPER(TRIM(category)) <> 'SVC'" if exclude_services else ""
    rows = conn.execute(
        f"""
        SELECT DISTINCT TRIM(category) AS category
        FROM items
        WHERE NULLIF(TRIM(category), '') IS NOT NULL
          {service_filter}
        ORDER BY TRIM(category) ASC
        """
    ).fetchall()
    return [row["category"] for row in rows if row["category"]]


def _resolve_selected_category(categories, requested_category):
    normalized_lookup = {
        str(category).strip().lower(): category
        for category in categories
    }
    return normalized_lookup.get(str(requested_category or "").strip().lower(), "")


def get_hot_items(limit=10, category=None):
    normalized_limit = normalize_top_items_limit(limit)
    conn = get_db()
    try:
        categories = _fetch_top_item_categories(conn)
        selected_category = _resolve_selected_category(categories, category)

        query_parts = [
            f"""
            SELECT
                items.name,
                items.description,
                items.category,
                COALESCE(stock_totals.current_stock, 0) AS current_stock,
                SUM(inventory_transactions.quantity) AS total_sold_last_30_days
            FROM inventory_transactions
            JOIN items ON items.id = inventory_transactions.item_id
            LEFT JOIN ({_current_stock_subquery()}) stock_totals
                ON stock_totals.item_id = items.id
            WHERE inventory_transactions.transaction_type = 'OUT'
              AND inventory_transactions.change_reason = ANY(%s)
              AND inventory_transactions.transaction_date >= (NOW() - INTERVAL '30 days')
            """
        ]
        params = [list(DEMAND_OUT_REASONS)]

        if selected_category:
            query_parts.append("AND LOWER(TRIM(items.category)) = %s")
            params.append(selected_category.lower())

        query_parts.append("""
            GROUP BY items.id, items.name, items.description, items.category, stock_totals.current_stock
            ORDER BY total_sold_last_30_days DESC, items.name ASC
            LIMIT %s
        """)
        params.append(normalized_limit)

        rows = [dict(row) for row in conn.execute("\n".join(query_parts), params).fetchall()]
        return {
            "items": rows,
            "categories": categories,
            "selected_category": selected_category,
            "selected_limit": normalized_limit,
        }
    finally:
        conn.close()


def get_sales_top_items(start_date, end_date, limit=10, category=None, external_conn=None):
    normalized_limit = normalize_top_items_limit(limit)
    conn = external_conn or get_db()
    try:
        categories = _fetch_top_item_categories(conn, exclude_services=True)
        selected_category = _resolve_selected_category(categories, category)

        category_filter_sql = ""
        category_params = []
        if selected_category:
            category_filter_sql = " AND LOWER(TRIM(item_category)) = %s"
            category_params.append(selected_category.lower())

        rows = conn.execute(
            f"""
            SELECT
                ranked_items.item_id,
                item_name,
                MAX(item_description) AS item_description,
                item_category,
                COALESCE(MAX(inv.current_stock), 0) AS current_stock,
                SUM(quantity_sold) AS quantity_sold,
                COALESCE(SUM(total_revenue), 0) AS total_revenue,
                COALESCE(SUM(total_cost), 0) AS total_cost,
                COALESCE(SUM(total_profit), 0) AS total_profit
            FROM (
                SELECT
                    i.id AS item_id,
                    i.name AS item_name,
                    COALESCE(i.description, '') AS item_description,
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
                  AND COALESCE(s.is_voided, FALSE) = FALSE
                  AND NOT EXISTS (
                      SELECT 1
                      FROM sale_refund_items sri
                      WHERE sri.sale_item_id = si.id
                  )
                GROUP BY i.id, i.name, i.description, i.category

                UNION ALL

                SELECT
                    sbi.item_id AS item_id,
                    sbi.item_name_snapshot AS item_name,
                    COALESCE(i.description, '') AS item_description,
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
                  AND COALESCE(s.is_voided, FALSE) = FALSE
                GROUP BY sbi.item_id, sbi.item_name_snapshot, i.description, i.category
            ) ranked_items
            LEFT JOIN ({_current_stock_subquery()}) inv
                ON inv.item_id = ranked_items.item_id
            WHERE 1 = 1
            {category_filter_sql}
            GROUP BY ranked_items.item_id, item_name, item_category
            ORDER BY quantity_sold DESC, total_revenue DESC, item_name ASC
            LIMIT %s
            """,
            (
                start_date,
                end_date,
                start_date,
                end_date,
                *category_params,
                normalized_limit,
            ),
        ).fetchall()

        items = []
        for row in rows:
            items.append({
                "name": row["item_name"],
                "description": row["item_description"] or "",
                "category": row["item_category"] or "",
                "current_stock": int(row["current_stock"] or 0),
                "quantity_sold": int(row["quantity_sold"] or 0),
                "total_revenue": round(float(row["total_revenue"] or 0), 2),
                "total_cost": round(float(row["total_cost"] or 0), 2),
                "total_profit": round(float(row["total_profit"] or 0), 2),
            })

        return {
            "items": items,
            "categories": categories,
            "selected_category": selected_category,
            "selected_limit": normalized_limit,
        }
    finally:
        if external_conn is None:
            conn.close()
