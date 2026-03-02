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
            "sales_number":     sale["sales_number"] or f"#{sale_id}",
            "customer_name":    sale["customer_name"] or "Walk-in",
            "mechanic_name":    sale["mechanic_name"] or "—",
            "total_amount":     sale["total_amount"] or 0.0,
            "total_paid":       total_paid,
            "remaining":        remaining,
            "status":           sale["status"],
            "payment_method":   sale["payment_method"] or "—",
            "notes":            sale["notes"] or "",
            "transaction_date": format_date(sale["transaction_date"]),
            "products":         items_by_sale.get(sale_id, []),
            "services":         services_by_sale.get(sale_id, []),
        })

    return result


def get_sales_report_by_date(report_date):
    """
    Pulls all completed sales for a given date for the End-of-Day PDF report.

    Mechanic cut logic:
    - Paid sales: mechanic payout on full service cost. Quota applies.
    - Utang/unresolved sales: mechanic payout based ONLY on service_portion
      collected via debt payments on this date. Quota does NOT apply to this portion.

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

    # --- Debt payments collected on this date (includes mechanic info for payout) ---
    debt_collected_rows = conn.execute("""
        SELECT
            dp.sale_id,
            dp.amount_paid,
            dp.service_portion,
            dp.paid_at,
            dp.reference_no,
            dp.notes,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            pm.name           AS payment_method
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE DATE(dp.paid_at) = ?
        ORDER BY dp.paid_at ASC
    """, (report_date,)).fetchall()

    # --- Early return ONLY if truly nothing to show ---
    if not sales_rows and not all_unresolved and not debt_collected_rows:
        conn.close()
        return []

    # --- Split into paid vs unpaid for separate queries ---
    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids  = [row["id"] for row in sales_rows]

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

    # Fetch services for ALL sales that day (needed for display)
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

    # --- Format debt payments collected ---
    debt_collected = [
        {
            "sales_number":    row["sales_number"] or f"#{row['sale_id']}",
            "customer_name":   row["customer_name"] or "Walk-in",
            "total_amount":    row["total_amount"],
            "amount_paid":     round(row["amount_paid"], 2),
            "service_portion": round(row["service_portion"] or 0.0, 2),
            "payment_method":  row["payment_method"] or "—",
            "reference_no":    row["reference_no"] or "",
            "notes":           row["notes"] or "",
            "paid_at":         format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)

    # --- Build debt mechanic map (service_portion collected per mechanic today) ---
    # Quota does NOT apply to this portion
    debt_mechanic_map = {}
    for row in debt_collected_rows:
        mech_id         = row["mechanic_id"]
        service_portion = round(row["service_portion"] or 0.0, 2)
        if mech_id and service_portion > 0:
            if mech_id not in debt_mechanic_map:
                debt_mechanic_map[mech_id] = {
                    "mechanic_name":      row["mechanic_name"] or "—",
                    "commission_rate":    row["commission_rate"] or 0.0,
                    "debt_service_total": 0.0,
                }
            debt_mechanic_map[mech_id]["debt_service_total"] += service_portion

    # --- Build paid transaction rows (revenue display only) ---
    paid_sales            = []
    total_gross           = 0.0
    total_service_revenue = 0.0
    mechanic_map          = {}

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
                "sales_number":     sale["sales_number"] or f"#{sale_id}",
                "customer_name":    sale["customer_name"] or "Walk-in",
                "mechanic_name":    mechanic_name,
                "services_total":   round(services_total, 2),
                "total_amount":     total_amount,
                "status":           sale["status"],
                "payment_method":   sale["payment_method"] or "—",
                "notes":            sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":         items_by_sale.get(sale_id, []),
                "services":         services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount

            # Only paid sales go into regular mechanic map (quota applies)
            if mechanic_id and services_total > 0:
                if mechanic_id not in mechanic_map:
                    mechanic_map[mechanic_id] = {
                        "mechanic_name":       mechanic_name,
                        "commission_rate":     commission_rate,
                        "paid_services_total": 0.0,
                    }
                mechanic_map[mechanic_id]["paid_services_total"] += services_total

    # --- Per-mechanic quota + commission ---
    mechanic_summary      = []
    total_mech_cut        = 0.0
    total_shop_topup      = 0.0
    total_shop_commission = 0.0
    total_mech_cut_from_paid  = 0.0
    total_shop_comm_from_paid = 0.0
    total_mech_cut_from_debt  = 0.0

    all_mech_ids = set(mechanic_map.keys()) | set(debt_mechanic_map.keys())

    for mech_id in all_mech_ids:
        regular = mechanic_map.get(mech_id, {})
        debt    = debt_mechanic_map.get(mech_id, {})

        mechanic_name   = regular.get("mechanic_name") or debt.get("mechanic_name") or "—"
        commission_rate = regular.get("commission_rate") or debt.get("commission_rate") or 0.0

        paid_services        = round(regular.get("paid_services_total", 0.0), 2)
        debt_service_portion = round(debt.get("debt_service_total", 0.0), 2)

        # Regular payout (paid sales)
        regular_mech_cut   = round(paid_services * commission_rate, 2)
        regular_shop_share = round(paid_services - regular_mech_cut, 2)

        # Debt service payout — NO quota
        debt_mech_cut   = round(debt_service_portion * commission_rate, 2)
        debt_shop_share = round(debt_service_portion - debt_mech_cut, 2)

        # Totals — quota check on COMBINED cut, not just regular
        total_mech_cut_this = round(regular_mech_cut + debt_mech_cut, 2)

        combined_services = round(paid_services + debt_service_portion, 2)

        if paid_services > 0 and combined_services < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - total_mech_cut_this, 2))
        else:
            shop_topup = 0.0
        total_shop_share    = round(regular_shop_share + debt_shop_share, 2)
        total_payout        = round(total_mech_cut_this + shop_topup, 2)

        total_mech_cut        += total_mech_cut_this
        total_shop_topup      += shop_topup
        total_shop_commission += total_shop_share
        total_mech_cut_from_paid  += regular_mech_cut
        total_shop_comm_from_paid += regular_shop_share
        total_mech_cut_from_debt  += debt_mech_cut

        mechanic_summary.append({
            "mechanic_name":         mechanic_name,
            "commission_rate":       commission_rate,
            "paid_services_total":   paid_services,
            "regular_mech_cut":      regular_mech_cut,
            "shop_topup":            shop_topup,
            "debt_service_portion":  debt_service_portion,
            "debt_mech_cut":         debt_mech_cut,
            "services_total":        round(paid_services + debt_service_portion, 2),
            "mechanic_cut":          total_mech_cut_this,
            "shop_commission_share": total_shop_share,
            "total_payout":          total_payout,
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
        "sales":                  paid_sales,
        "unresolved":             all_unresolved,
        "mechanic_summary":       mechanic_summary,
        "items_summary":          items_summary_list,
        "total_gross":            round(total_gross, 2),
        "total_mech_cut":         total_mech_cut,
        "total_shop_topup":       total_shop_topup,
        "net_revenue":            round(total_gross - total_mech_cut - total_shop_topup + total_debt_collected, 2),
        "total_shop_commission":  round(total_shop_commission, 2),
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "debt_collected":         debt_collected,
        "total_debt_collected":   total_debt_collected,
        "total_mech_cut_from_paid":  round(total_mech_cut_from_paid, 2),
        "total_shop_comm_from_paid": round(total_shop_comm_from_paid, 2),
        "total_mech_cut_from_debt":  round(total_mech_cut_from_debt, 2),
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

    # --- Debt payments collected within this date range (includes mechanic info for payout) ---
    debt_collected_rows = conn.execute("""
        SELECT
            dp.sale_id,
            dp.amount_paid,
            dp.service_portion,
            dp.paid_at,
            dp.reference_no,
            dp.notes,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            pm.name           AS payment_method
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE DATE(dp.paid_at) BETWEEN ? AND ?
        ORDER BY dp.paid_at ASC
    """, (start_date, end_date)).fetchall()

    # --- Early return ONLY if truly nothing to show ---
    if not sales_rows and not all_unresolved and not debt_collected_rows:
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

    # --- Format debt payments collected ---
    debt_collected = [
        {
            "sales_number":    row["sales_number"] or f"#{row['sale_id']}",
            "customer_name":   row["customer_name"] or "Walk-in",
            "total_amount":    row["total_amount"],
            "amount_paid":     round(row["amount_paid"], 2),
            "service_portion": round(row["service_portion"] or 0.0, 2),
            "payment_method":  row["payment_method"] or "—",
            "reference_no":    row["reference_no"] or "",
            "notes":           row["notes"] or "",
            "paid_at":         format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)

    # --- Build debt mechanic map (service_portion collected per mechanic in range) ---
    debt_mechanic_map = {}
    for row in debt_collected_rows:
        mech_id         = row["mechanic_id"]
        service_portion = round(row["service_portion"] or 0.0, 2)
        if mech_id and service_portion > 0:
            if mech_id not in debt_mechanic_map:
                debt_mechanic_map[mech_id] = {
                    "mechanic_name":      row["mechanic_name"] or "—",
                    "commission_rate":    row["commission_rate"] or 0.0,
                    "debt_service_total": 0.0,
                }
            debt_mechanic_map[mech_id]["debt_service_total"] += service_portion

    # --- Build paid transaction rows (revenue display only) ---
    paid_sales            = []
    total_gross           = 0.0
    total_service_revenue = 0.0
    mechanic_map          = {}

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
                "sales_number":     sale["sales_number"] or f"#{sale_id}",
                "customer_name":    sale["customer_name"] or "Walk-in",
                "mechanic_name":    mechanic_name,
                "services_total":   round(services_total, 2),
                "total_amount":     total_amount,
                "status":           sale["status"],
                "payment_method":   sale["payment_method"] or "—",
                "notes":            sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":         items_by_sale.get(sale_id, []),
                "services":         services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount

            # Only paid sales in regular mechanic map (quota applies)
            if mechanic_id and services_total > 0:
                if mechanic_id not in mechanic_map:
                    mechanic_map[mechanic_id] = {
                        "mechanic_name":       mechanic_name,
                        "commission_rate":     commission_rate,
                        "paid_services_total": 0.0,
                    }
                mechanic_map[mechanic_id]["paid_services_total"] += services_total

    # --- Per-mechanic quota + commission ---
    mechanic_summary      = []
    total_mech_cut        = 0.0
    total_shop_topup      = 0.0
    total_shop_commission = 0.0
    total_mech_cut_from_paid  = 0.0
    total_shop_comm_from_paid = 0.0
    total_mech_cut_from_debt  = 0.0

    all_mech_ids = set(mechanic_map.keys()) | set(debt_mechanic_map.keys())

    for mech_id in all_mech_ids:
        regular = mechanic_map.get(mech_id, {})
        debt    = debt_mechanic_map.get(mech_id, {})

        mechanic_name   = regular.get("mechanic_name") or debt.get("mechanic_name") or "—"
        commission_rate = regular.get("commission_rate") or debt.get("commission_rate") or 0.0

        paid_services        = round(regular.get("paid_services_total", 0.0), 2)
        debt_service_portion = round(debt.get("debt_service_total", 0.0), 2)

        # Regular payout (paid sales)
        regular_mech_cut   = round(paid_services * commission_rate, 2)
        regular_shop_share = round(paid_services - regular_mech_cut, 2)

        # Debt service payout — NO quota
        debt_mech_cut   = round(debt_service_portion * commission_rate, 2)
        debt_shop_share = round(debt_service_portion - debt_mech_cut, 2)

        # Totals — quota check on COMBINED cut, not just regular
        total_mech_cut_this = round(regular_mech_cut + debt_mech_cut, 2)

        combined_services = round(paid_services + debt_service_portion, 2)

        if paid_services > 0 and combined_services < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - total_mech_cut_this, 2))
        else:
            shop_topup = 0.0
        total_shop_share    = round(regular_shop_share + debt_shop_share, 2)
        total_payout        = round(total_mech_cut_this + shop_topup, 2)

        total_mech_cut        += total_mech_cut_this
        total_shop_topup      += shop_topup
        total_shop_commission += total_shop_share
        total_mech_cut_from_paid  += regular_mech_cut
        total_shop_comm_from_paid += regular_shop_share
        total_mech_cut_from_debt  += debt_mech_cut

        mechanic_summary.append({
            "mechanic_name":         mechanic_name,
            "commission_rate":       commission_rate,
            "paid_services_total":   paid_services,
            "regular_mech_cut":      regular_mech_cut,
            "shop_topup":            shop_topup,
            "debt_service_portion":  debt_service_portion,
            "debt_mech_cut":         debt_mech_cut,
            "services_total":        round(paid_services + debt_service_portion, 2),
            "mechanic_cut":          total_mech_cut_this,
            "shop_commission_share": total_shop_share,
            "total_payout":          total_payout,
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
        "sales":                  paid_sales,
        "unresolved":             all_unresolved,
        "mechanic_summary":       mechanic_summary,
        "items_summary":          items_summary_list,
        "total_gross":            round(total_gross, 2),
        "total_mech_cut":         total_mech_cut,
        "total_shop_topup":       total_shop_topup,
        "net_revenue":            round(total_gross - total_mech_cut - total_shop_topup + total_debt_collected, 2),
        "total_shop_commission":  round(total_shop_commission, 2),
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "debt_collected":         debt_collected,
        "total_debt_collected":   total_debt_collected,
        "total_mech_cut_from_paid":  round(total_mech_cut_from_paid, 2),
        "total_shop_comm_from_paid": round(total_shop_comm_from_paid, 2),
        "total_mech_cut_from_debt":  round(total_mech_cut_from_debt, 2),
    }