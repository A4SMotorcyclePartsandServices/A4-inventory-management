from db.database import get_db
from utils.formatters import format_date


def get_sales_by_date(report_date):
    conn = get_db()

    rows = conn.execute("""
        SELECT 
            items.name,
            inventory_transactions.quantity,
            inventory_transactions.transaction_date,
            inventory_transactions.user_name
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE transaction_type = 'OUT'
        AND DATE(transaction_date) = ?
    """, (report_date,)).fetchall()

    conn.close()
    return rows


def get_sales_by_range(start_date, end_date):
    conn = get_db()

    rows = conn.execute("""
        SELECT 
            items.name,
            inventory_transactions.quantity,
            inventory_transactions.transaction_date,
            inventory_transactions.user_name
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE transaction_type = 'OUT'
          AND DATE(transaction_date) BETWEEN ? AND ?
    """, (start_date, end_date)).fetchall()

    conn.close()
    return rows


def get_all_unresolved_sales(conn):
    """
    Pulls ALL sales with status Unresolved or Partial across every date.
    Includes payment progress so the PDF can show accurate remaining balances.
    """
    unresolved_rows = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.name  AS mechanic_name,
            pm.name AS payment_method,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.status IN ('Unresolved', 'Partial')
        GROUP BY s.id
        ORDER BY s.transaction_date ASC
    """).fetchall()

    if not unresolved_rows:
        return []

    sale_ids     = [row["id"] for row in unresolved_rows]
    placeholders = ",".join("?" * len(sale_ids))

    items_rows = conn.execute(f"""
        SELECT
            si.sale_id,
            i.name                  AS item_name,
            si.quantity,
            si.original_unit_price,
            si.discount_percent,
            si.discount_amount,
            si.final_unit_price,
            (si.quantity * si.final_unit_price) AS line_total
        FROM sales_items si
        JOIN items i ON i.id = si.item_id
        WHERE si.sale_id IN ({placeholders})
        ORDER BY si.sale_id, i.name
    """, sale_ids).fetchall()

    services_rows = conn.execute(f"""
        SELECT
            ss.sale_id,
            sv.name AS service_name,
            ss.price
        FROM sales_services ss
        JOIN services sv ON sv.id = ss.service_id
        WHERE ss.sale_id IN ({placeholders})
        ORDER BY ss.sale_id, sv.name
    """, sale_ids).fetchall()

    items_by_sale = {}
    for row in items_rows:
        items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    services_by_sale = {}
    for row in services_rows:
        services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    result = []
    for sale in unresolved_rows:
        sale_id    = sale["id"]
        total_paid = round(sale["total_paid"], 2)
        remaining  = round(sale["total_amount"] - total_paid, 2)

        result.append({
            "sales_number":   sale["sales_number"] or f"#{sale_id}",
            "customer_name":  sale["customer_name"] or "Walk-in",
            "mechanic_name":  sale["mechanic_name"] or "—",
            "total_amount":   sale["total_amount"] or 0.0,
            "total_paid":     total_paid,
            "remaining":      remaining,
            "status":         sale["status"],
            "payment_method": sale["payment_method"] or "—",
            "notes":          sale["notes"] or "",
            "transaction_date": format_date(sale["transaction_date"]),
            "products":       items_by_sale.get(sale_id, []),
            "services":       services_by_sale.get(sale_id, []),
        })

    return result


def get_sales_report_by_date(report_date):
    """
    Pulls all completed sales for a given date for the End-of-Day PDF report.

    Mechanic cut logic (applied per mechanic across the WHOLE day):
    - Services from ALL sales that day (Paid, Partial, Unresolved) count toward mechanic payout
    - This is because the mechanic did the work regardless of customer payment status
    - Revenue numbers (gross, net) are still Paid-only
    - If total services < MECHANIC_QUOTA, shop tops up the difference as an expense

    NOTE: When adding multi-branch support later, add a branch_id column to the
    sales table and filter by branch_id here.
    """
    conn = get_db()

    MECHANIC_QUOTA = 500.0

    # --- All sales rows for the day (ALL statuses) ---
    sales_rows = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.id              AS mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            pm.name           AS payment_method
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        WHERE DATE(s.transaction_date) = ?
        ORDER BY s.transaction_date ASC
    """, (report_date,)).fetchall()

    # --- All unresolved across ALL dates (not filtered by report_date) ---
    all_unresolved = get_all_unresolved_sales(conn)

    if not sales_rows and not all_unresolved:
        conn.close()
        return []

    # --- Split into paid vs unpaid for separate queries ---
    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids  = [row["id"] for row in sales_rows]  # for mechanic calc

    items_by_sale    = {}
    services_by_sale = {}

    # Fetch items for Paid sales only (revenue display)
    if paid_sale_ids:
        placeholders = ",".join("?" * len(paid_sale_ids))

        items_rows = conn.execute(f"""
            SELECT
                si.sale_id,
                i.name                  AS item_name,
                si.quantity,
                si.original_unit_price,
                si.discount_percent,
                si.discount_amount,
                si.final_unit_price,
                (si.quantity * si.final_unit_price) AS line_total
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id IN ({placeholders})
            ORDER BY si.sale_id, i.name
        """, paid_sale_ids).fetchall()

        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    # Fetch services for ALL sales that day (mechanic payout needs all of them)
    if all_sale_ids:
        placeholders = ",".join("?" * len(all_sale_ids))

        services_rows = conn.execute(f"""
            SELECT
                ss.sale_id,
                sv.name   AS service_name,
                ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id IN ({placeholders})
            ORDER BY ss.sale_id, sv.name
        """, all_sale_ids).fetchall()

        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    conn.close()

    # --- Build paid transaction rows (revenue display only) ---
    paid_sales  = []
    total_gross = 0.0
    total_service_revenue = 0.0
    total_unresolved_service_revenue = 0.0
    mechanic_map = {}

    for sale in sales_rows:
        sale_id         = sale["id"]
        mechanic_id     = sale["mechanic_id"]
        mechanic_name   = sale["mechanic_name"] or "—"
        commission_rate = sale["commission_rate"] or 0.0
        services_total  = sum(
            svc["price"] for svc in services_by_sale.get(sale_id, [])
        )

        # Revenue rows: Paid only
        if sale["status"] == "Paid":
            total_amount = sale["total_amount"] or 0.0
            total_service_revenue += services_total
            paid_sales.append({
                "sales_number":   sale["sales_number"] or f"#{sale_id}",
                "customer_name":  sale["customer_name"] or "Walk-in",
                "mechanic_name":  mechanic_name,
                "services_total": round(services_total, 2),
                "total_amount":   total_amount,
                "status":         sale["status"],
                "payment_method": sale["payment_method"] or "—",
                "notes":          sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":       items_by_sale.get(sale_id, []),
                "services":       services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount
        else:
            total_unresolved_service_revenue += services_total

        # Mechanic map: ALL statuses — mechanic did the work regardless
        if mechanic_id and services_total > 0:
            if mechanic_id not in mechanic_map:
                mechanic_map[mechanic_id] = {
                    "mechanic_name":   mechanic_name,
                    "commission_rate": commission_rate,
                    "services_total":  0.0,
                }
            mechanic_map[mechanic_id]["services_total"] += services_total

    # --- Per-mechanic quota + commission ---
    mechanic_summary = []
    total_mech_cut   = 0.0
    total_shop_topup = 0.0
    total_shop_commission = 0.0

    for mech_id, mech in mechanic_map.items():
        services_total  = round(mech["services_total"], 2)
        commission_rate = mech["commission_rate"]

        mech_cut = round(services_total * commission_rate, 2)
        shop_commission_share = round(services_total - mech_cut, 2)

        if services_total > 0 and services_total < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - mech_cut, 2))
        else:
            shop_topup = 0.0

        total_payout = round(mech_cut + shop_topup, 2)

        total_mech_cut   += mech_cut
        total_shop_topup += shop_topup
        total_shop_commission += shop_commission_share

        mechanic_summary.append({
            "mechanic_name":   mech["mechanic_name"],
            "commission_rate": commission_rate,
            "services_total":  services_total,
            "mechanic_cut":    mech_cut,
            "shop_topup":      shop_topup,
            "total_payout":    total_payout,
            "shop_commission_share": shop_commission_share,
        })

    mechanic_summary.sort(key=lambda x: x["mechanic_name"])

    # --- Items summary (Paid only) ---
    items_summary = {}
    for sale in paid_sales:
        for item in sale["products"]:
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {"item_name": item["item_name"], "quantity": 0, "total": 0.0}
            items_summary[key]["quantity"] += item["quantity"]
            items_summary[key]["total"]    += item["line_total"]

    items_summary_list = sorted(items_summary.values(), key=lambda x: x["item_name"])

    total_mech_cut   = round(total_mech_cut, 2)
    total_shop_topup = round(total_shop_topup, 2)

    return {
        "sales":            paid_sales,
        "unresolved":       all_unresolved,
        "mechanic_summary": mechanic_summary,
        "items_summary":    items_summary_list,
        "total_gross":      round(total_gross, 2),
        "total_mech_cut":   total_mech_cut,
        "total_shop_topup": total_shop_topup,
        "net_revenue":      round(total_gross - total_mech_cut - total_shop_topup, 2),
        "total_shop_commission": round(total_shop_commission, 2),
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "total_unresolved_service_revenue": round(total_unresolved_service_revenue, 2),
    }

def get_sales_report_by_range(start_date, end_date):
    """
    Pulls all completed sales between start_date and end_date (inclusive)
    for a multi-day PDF report.

    Quota note: MECHANIC_QUOTA is applied once across the full range total
    per mechanic, not per-day. If you later need per-day quota enforcement,
    this function will need to be restructured to group by date first.

    Multi-branch note: add branch_id filter here when branches are added.
    """
    conn = get_db()

    MECHANIC_QUOTA = 500.0

    sales_rows = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.id              AS mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            pm.name           AS payment_method
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        WHERE DATE(s.transaction_date) BETWEEN ? AND ?
        ORDER BY s.transaction_date ASC
    """, (start_date, end_date)).fetchall()

    all_unresolved = get_all_unresolved_sales(conn)

    if not sales_rows and not all_unresolved:
        conn.close()
        return []

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids  = [row["id"] for row in sales_rows]

    items_by_sale    = {}
    services_by_sale = {}

    if paid_sale_ids:
        placeholders = ",".join("?" * len(paid_sale_ids))
        items_rows = conn.execute(f"""
            SELECT
                si.sale_id,
                i.name                  AS item_name,
                si.quantity,
                si.original_unit_price,
                si.discount_percent,
                si.discount_amount,
                si.final_unit_price,
                (si.quantity * si.final_unit_price) AS line_total
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id IN ({placeholders})
            ORDER BY si.sale_id, i.name
        """, paid_sale_ids).fetchall()
        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    if all_sale_ids:
        placeholders = ",".join("?" * len(all_sale_ids))
        services_rows = conn.execute(f"""
            SELECT
                ss.sale_id,
                sv.name   AS service_name,
                ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id IN ({placeholders})
            ORDER BY ss.sale_id, sv.name
        """, all_sale_ids).fetchall()
        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    conn.close()

    paid_sales   = []
    total_gross  = 0.0
    total_service_revenue = 0.0
    total_unresolved_service_revenue = 0.0
    mechanic_map = {}

    for sale in sales_rows:
        sale_id         = sale["id"]
        mechanic_id     = sale["mechanic_id"]
        mechanic_name   = sale["mechanic_name"] or "—"
        commission_rate = sale["commission_rate"] or 0.0
        services_total  = sum(svc["price"] for svc in services_by_sale.get(sale_id, []))

        if sale["status"] == "Paid":
            total_amount = sale["total_amount"] or 0.0
            total_service_revenue += services_total
            paid_sales.append({
                "sales_number":   sale["sales_number"] or f"#{sale_id}",
                "customer_name":  sale["customer_name"] or "Walk-in",
                "mechanic_name":  mechanic_name,
                "services_total": round(services_total, 2),
                "total_amount":   total_amount,
                "status":         sale["status"],
                "payment_method": sale["payment_method"] or "—",
                "notes":          sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":       items_by_sale.get(sale_id, []),
                "services":       services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount
        else:
            total_unresolved_service_revenue += services_total

        if mechanic_id and services_total > 0:
            if mechanic_id not in mechanic_map:
                mechanic_map[mechanic_id] = {
                    "mechanic_name":   mechanic_name,
                    "commission_rate": commission_rate,
                    "services_total":  0.0,
                }
            mechanic_map[mechanic_id]["services_total"] += services_total

    mechanic_summary = []
    total_mech_cut   = 0.0
    total_shop_topup = 0.0
    total_shop_commission = 0.0

    for mech_id, mech in mechanic_map.items():
        services_total  = round(mech["services_total"], 2)
        commission_rate = mech["commission_rate"]

        mech_cut = round(services_total * commission_rate, 2)
        shop_commission_share = round(services_total - mech_cut, 2)

        if services_total > 0 and services_total < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - mech_cut, 2))
        else:
            shop_topup = 0.0

        total_payout = round(mech_cut + shop_topup, 2)
        total_mech_cut   += mech_cut
        total_shop_topup += shop_topup
        total_shop_commission += shop_commission_share

        mechanic_summary.append({
            "mechanic_name":   mech["mechanic_name"],
            "commission_rate": commission_rate,
            "services_total":  services_total,
            "mechanic_cut":    mech_cut,
            "shop_topup":      shop_topup,
            "total_payout":    total_payout,
            "shop_commission_share": shop_commission_share,
        })

    mechanic_summary.sort(key=lambda x: x["mechanic_name"])

    items_summary = {}
    for sale in paid_sales:
        for item in sale["products"]:
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {"item_name": item["item_name"], "quantity": 0, "total": 0.0}
            items_summary[key]["quantity"] += item["quantity"]
            items_summary[key]["total"]    += item["line_total"]

    items_summary_list = sorted(items_summary.values(), key=lambda x: x["item_name"])
    total_mech_cut   = round(total_mech_cut, 2)
    total_shop_topup = round(total_shop_topup, 2)

    return {
        "sales":            paid_sales,
        "unresolved":       all_unresolved,
        "mechanic_summary": mechanic_summary,
        "items_summary":    items_summary_list,
        "total_gross":      round(total_gross, 2),
        "total_mech_cut":   total_mech_cut,
        "total_shop_topup": total_shop_topup,
        "net_revenue":      round(total_gross - total_mech_cut - total_shop_topup, 2),
        "total_shop_commission": round(total_shop_commission, 2),
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "total_unresolved_service_revenue": round(total_unresolved_service_revenue, 2),
    }