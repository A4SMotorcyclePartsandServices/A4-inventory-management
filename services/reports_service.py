from db.database import get_db
from utils.formatters import format_date


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

MECHANIC_QUOTA = 500.0


def _num(value):
    return float(value or 0)
# ─────────────────────────────────────────────
# PRIVATE HELPERS — shared by daily, range, and cash ledger panel
# ─────────────────────────────────────────────

def _build_mechanic_maps(sales_rows, debt_collected_rows, services_by_sale):
    """
    Builds mechanic_map (from paid sales) and debt_mechanic_map (from debt payments).
    Extracted so the identical logic isn't duplicated in daily vs range reports.

    mechanic_map      — regular paid services, quota applies
    debt_mechanic_map — debt service portions collected, quota does NOT apply
    """
    mechanic_map      = {}
    debt_mechanic_map = {}

    for sale in sales_rows:
        sale_id         = sale["id"]
        mechanic_id     = sale["mechanic_id"]
        mechanic_name   = sale["mechanic_name"] or "—"
        commission_rate = _num(sale["commission_rate"])
        services_total  = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))

        if sale["status"] == "Paid" and mechanic_id and services_total > 0:
            if mechanic_id not in mechanic_map:
                mechanic_map[mechanic_id] = {
                    "mechanic_name":       mechanic_name,
                    "commission_rate":     commission_rate,
                    "paid_services_total": 0.0,
                }
            mechanic_map[mechanic_id]["paid_services_total"] += services_total

    for row in debt_collected_rows:
        mech_id         = row["mechanic_id"]
        service_portion = round(_num(row["service_portion"]), 2)
        if mech_id and service_portion > 0:
            if mech_id not in debt_mechanic_map:
                debt_mechanic_map[mech_id] = {
                    "mechanic_name":      row["mechanic_name"] or "—",
                    "commission_rate":    _num(row["commission_rate"]),
                    "debt_service_total": 0.0,
                }
            debt_mechanic_map[mech_id]["debt_service_total"] += service_portion

    return mechanic_map, debt_mechanic_map


def _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map):
    """
    Runs quota + commission math for every mechanic found in either map.
    Returns the mechanic_summary list plus all running totals.

    This is the single source of truth for payout math.
    Called by:
      - get_sales_report_by_date
      - get_sales_report_by_range
      - get_mechanic_payouts_for_date  (cash ledger panel)
    """
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
        commission_rate = _num(regular.get("commission_rate") or debt.get("commission_rate"))

        paid_services        = round(_num(regular.get("paid_services_total", 0.0)), 2)
        debt_service_portion = round(_num(debt.get("debt_service_total", 0.0)), 2)

        regular_mech_cut   = round(paid_services * commission_rate, 2)
        regular_shop_share = round(paid_services - regular_mech_cut, 2)

        debt_mech_cut   = round(debt_service_portion * commission_rate, 2)
        debt_shop_share = round(debt_service_portion - debt_mech_cut, 2)

        total_mech_cut_this = round(regular_mech_cut + debt_mech_cut, 2)
        combined_services   = round(paid_services + debt_service_portion, 2)

        if paid_services > 0 and combined_services < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - total_mech_cut_this, 2))
        else:
            shop_topup = 0.0

        total_shop_share = round(regular_shop_share + debt_shop_share, 2)
        total_payout     = round(total_mech_cut_this + shop_topup, 2)

        total_mech_cut        += total_mech_cut_this
        total_shop_topup      += shop_topup
        total_shop_commission += total_shop_share
        total_mech_cut_from_paid  += regular_mech_cut
        total_shop_comm_from_paid += regular_shop_share
        total_mech_cut_from_debt  += debt_mech_cut

        mechanic_summary.append({
            "mechanic_id":           mech_id,
            "mechanic_name":         mechanic_name,
            "commission_rate":       commission_rate,
            "paid_services_total":   paid_services,
            "regular_mech_cut":      regular_mech_cut,
            "shop_topup":            shop_topup,
            "debt_service_portion":  debt_service_portion,
            "debt_mech_cut":         debt_mech_cut,
            "services_total":        combined_services,
            "mechanic_cut":          total_mech_cut_this,
            "shop_commission_share": total_shop_share,
            "total_payout":          total_payout,
        })

    mechanic_summary.sort(key=lambda x: x["mechanic_name"])

    return mechanic_summary, {
        "total_mech_cut":             round(total_mech_cut, 2),
        "total_shop_topup":           round(total_shop_topup, 2),
        "total_shop_commission":      round(total_shop_commission, 2),
        "total_mech_cut_from_paid":   round(total_mech_cut_from_paid, 2),
        "total_shop_comm_from_paid":  round(total_shop_comm_from_paid, 2),
        "total_mech_cut_from_debt":   round(total_mech_cut_from_debt, 2),
    }


# ─────────────────────────────────────────────
# PUBLIC — Cash Ledger Panel
# ─────────────────────────────────────────────

def _format_cash_panel_payout_rows(mechanic_summary):
    return [
        {
            "mechanic_id": row["mechanic_id"],
            "mechanic_name": row["mechanic_name"],
            "total_payout": row["total_payout"],
            "has_topup": row["shop_topup"] > 0,
            "auto_description": (
                f"{row['mechanic_name']} + quota top up"
                if row["shop_topup"] > 0
                else row["mechanic_name"]
            ),
        }
        for row in mechanic_summary
        if row["total_payout"] > 0
    ]


def get_mechanic_payouts_for_dates(report_dates):
    """
    Batched mechanic payout lookup for the cash ledger panel.
    Returns { 'YYYY-MM-DD': [ { mechanic payout row }, ... ], ... }.
    """
    normalized_dates = sorted({str(d) for d in (report_dates or []) if d})
    if not normalized_dates:
        return {}

    conn = get_db()
    placeholders = ",".join(["%s"] * len(normalized_dates))

    sales_rows = conn.execute(f"""
        SELECT
            DATE(s.transaction_date) AS payout_date,
            s.id,
            s.status,
            m.id                     AS mechanic_id,
            m.name                   AS mechanic_name,
            m.commission_rate
        FROM sales s
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        WHERE DATE(s.transaction_date) IN ({placeholders})
          AND s.mechanic_id IS NOT NULL
    """, normalized_dates).fetchall()

    debt_collected_rows = conn.execute(f"""
        SELECT
            DATE(dp.paid_at) AS payout_date,
            dp.service_portion,
            s.mechanic_id,
            m.name           AS mechanic_name,
            m.commission_rate
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        WHERE DATE(dp.paid_at) IN ({placeholders})
          AND s.mechanic_id IS NOT NULL
    """, normalized_dates).fetchall()

    if not sales_rows and not debt_collected_rows:
        conn.close()
        return {day: [] for day in normalized_dates}

    sale_ids = [row["id"] for row in sales_rows]
    services_by_sale = {}
    if sale_ids:
        sale_id_placeholders = ",".join(["%s"] * len(sale_ids))
        services_rows = conn.execute(f"""
            SELECT ss.sale_id, ss.price
            FROM sales_services ss
            WHERE ss.sale_id IN ({sale_id_placeholders})
        """, sale_ids).fetchall()
        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append({"price": row["price"]})

    conn.close()

    sales_by_date = {}
    debt_by_date = {}
    for row in sales_rows:
        day = str(row["payout_date"])
        sales_by_date.setdefault(day, []).append(row)
    for row in debt_collected_rows:
        day = str(row["payout_date"])
        debt_by_date.setdefault(day, []).append(row)

    payouts_by_date = {}
    for day in normalized_dates:
        mechanic_map, debt_mechanic_map = _build_mechanic_maps(
            sales_by_date.get(day, []),
            debt_by_date.get(day, []),
            services_by_sale,
        )
        if not mechanic_map and not debt_mechanic_map:
            payouts_by_date[day] = []
            continue

        mechanic_summary, _ = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)
        payouts_by_date[day] = _format_cash_panel_payout_rows(mechanic_summary)

    return payouts_by_date


def get_mechanic_payouts_for_date(report_date):
    """
    Returns each mechanic's calculated payout for a given date.
    Used exclusively by the cash ledger's Pending Payouts panel.

    Only queries what's needed — no items, no PDF formatting, no unresolved sales.
    Returns a flat list:
      [{ mechanic_id, mechanic_name, total_payout, has_topup }, ...]

    has_topup lets the panel show a visual indicator when quota top-up was applied,
    so staff understands why the number might be higher than expected.

    NOTE (future branches): add branch_id filter to sales query when ready.
    """
    return get_mechanic_payouts_for_dates([report_date]).get(report_date, [])


# ─────────────────────────────────────────────
# PUBLIC — existing report functions (unchanged return values)
# ─────────────────────────────────────────────

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
        AND DATE(transaction_date) = %s
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
        AND DATE(transaction_date) BETWEEN %s AND %s
    """, (start_date, end_date)).fetchall()
    conn.close()
    return rows


def get_all_unresolved_sales(conn):
    """
    Pulls ALL sales with status Unresolved or Partial across every date.
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
        GROUP BY s.id, m.name, pm.name
        ORDER BY s.transaction_date ASC
    """).fetchall()

    if not unresolved_rows:
        return []

    sale_ids     = [row["id"] for row in unresolved_rows]
    placeholders = ",".join(["%s"] * len(sale_ids))

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

    items_by_sale    = {}
    services_by_sale = {}
    for row in items_rows:
        items_by_sale.setdefault(row["sale_id"], []).append(dict(row))
    for row in services_rows:
        services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    result = []
    for sale in unresolved_rows:
        sale_id    = sale["id"]
        total_amount = _num(sale["total_amount"])
        total_paid = round(_num(sale["total_paid"]), 2)
        remaining  = round(total_amount - total_paid, 2)
        result.append({
            "sales_number":     sale["sales_number"] or f"#{sale_id}",
            "customer_name":    sale["customer_name"] or "Walk-in",
            "mechanic_name":    sale["mechanic_name"] or "—",
            "total_amount":     round(total_amount, 2),
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
    Return value is identical to before — PDF template is untouched.
    """
    conn = get_db()

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
        WHERE DATE(s.transaction_date) = %s
        ORDER BY s.transaction_date ASC
    """, (report_date,)).fetchall()

    all_unresolved = get_all_unresolved_sales(conn)

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
        WHERE DATE(dp.paid_at) = %s
        ORDER BY dp.paid_at ASC
    """, (report_date,)).fetchall()

    if not sales_rows and not all_unresolved and not debt_collected_rows:
        conn.close()
        return []

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids  = [row["id"] for row in sales_rows]
    items_by_sale    = {}
    services_by_sale = {}

    if paid_sale_ids:
        placeholders = ",".join(["%s"] * len(paid_sale_ids))
        items_rows = conn.execute(f"""
            SELECT si.sale_id, i.name AS item_name, si.quantity,
                   si.original_unit_price, si.discount_percent,
                   si.discount_amount, si.final_unit_price,
                   (si.quantity * si.final_unit_price) AS line_total
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id IN ({placeholders})
            ORDER BY si.sale_id, i.name
        """, paid_sale_ids).fetchall()
        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    if all_sale_ids:
        placeholders = ",".join(["%s"] * len(all_sale_ids))
        services_rows = conn.execute(f"""
            SELECT ss.sale_id, sv.name AS service_name, ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id IN ({placeholders})
            ORDER BY ss.sale_id, sv.name
        """, all_sale_ids).fetchall()
        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    conn.close()

    debt_collected = [
        {
            "sales_number":    row["sales_number"] or f"#{row['sale_id']}",
            "customer_name":   row["customer_name"] or "Walk-in",
            "total_amount":    round(_num(row["total_amount"]), 2),
            "amount_paid":     round(_num(row["amount_paid"]), 2),
            "service_portion": round(_num(row["service_portion"]), 2),
            "payment_method":  row["payment_method"] or "—",
            "reference_no":    row["reference_no"] or "",
            "notes":           row["notes"] or "",
            "paid_at":         format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)

    paid_sales            = []
    total_gross           = 0.0
    total_service_revenue = 0.0

    for sale in sales_rows:
        sale_id       = sale["id"]
        services_total  = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))
        if sale["status"] == "Paid":
            total_amount = _num(sale["total_amount"])
            total_service_revenue += services_total
            paid_sales.append({
                "sales_number":     sale["sales_number"] or f"#{sale_id}",
                "customer_name":    sale["customer_name"] or "Walk-in",
                "mechanic_name":    sale["mechanic_name"] or "—",
                "services_total":   round(services_total, 2),
                "total_amount":     round(total_amount, 2),
                "status":           sale["status"],
                "payment_method":   sale["payment_method"] or "—",
                "notes":            sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":         items_by_sale.get(sale_id, []),
                "services":         services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount

    mechanic_map, debt_mechanic_map = _build_mechanic_maps(
        sales_rows, debt_collected_rows, services_by_sale
    )
    mechanic_summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)

    items_summary = {}
    for sale in paid_sales:
        for item in sale["products"]:
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {"item_name": key, "quantity": 0, "total": 0.0}
            items_summary[key]["quantity"] += int(item["quantity"] or 0)
            items_summary[key]["total"]    += _num(item["line_total"])

    return {
        "sales":                  paid_sales,
        "unresolved":             all_unresolved,
        "mechanic_summary":       mechanic_summary,
        "items_summary":          sorted(items_summary.values(), key=lambda x: x["item_name"]),
        "total_gross":            round(total_gross, 2),
        "total_mech_cut":         totals["total_mech_cut"],
        "total_shop_topup":       totals["total_shop_topup"],
        "net_revenue":            round(total_gross - totals["total_mech_cut"] - totals["total_shop_topup"] + total_debt_collected, 2),
        "total_shop_commission":  totals["total_shop_commission"],
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "debt_collected":         debt_collected,
        "total_debt_collected":   total_debt_collected,
        "total_mech_cut_from_paid":  totals["total_mech_cut_from_paid"],
        "total_shop_comm_from_paid": totals["total_shop_comm_from_paid"],
        "total_mech_cut_from_debt":  totals["total_mech_cut_from_debt"],
    }


def get_sales_report_by_range(start_date, end_date):
    """
    Pulls all completed sales between start_date and end_date (inclusive).
    Return value is identical to before — PDF template is untouched.
    """
    conn = get_db()

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
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
        ORDER BY s.transaction_date ASC
    """, (start_date, end_date)).fetchall()

    all_unresolved = get_all_unresolved_sales(conn)

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
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
        ORDER BY dp.paid_at ASC
    """, (start_date, end_date)).fetchall()

    if not sales_rows and not all_unresolved and not debt_collected_rows:
        conn.close()
        return []

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids  = [row["id"] for row in sales_rows]
    items_by_sale    = {}
    services_by_sale = {}

    if paid_sale_ids:
        placeholders = ",".join(["%s"] * len(paid_sale_ids))
        items_rows = conn.execute(f"""
            SELECT si.sale_id, i.name AS item_name, si.quantity,
                   si.original_unit_price, si.discount_percent,
                   si.discount_amount, si.final_unit_price,
                   (si.quantity * si.final_unit_price) AS line_total
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id IN ({placeholders})
            ORDER BY si.sale_id, i.name
        """, paid_sale_ids).fetchall()
        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    if all_sale_ids:
        placeholders = ",".join(["%s"] * len(all_sale_ids))
        services_rows = conn.execute(f"""
            SELECT ss.sale_id, sv.name AS service_name, ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id IN ({placeholders})
            ORDER BY ss.sale_id, sv.name
        """, all_sale_ids).fetchall()
        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    conn.close()

    debt_collected = [
        {
            "sales_number":    row["sales_number"] or f"#{row['sale_id']}",
            "customer_name":   row["customer_name"] or "Walk-in",
            "total_amount":    round(_num(row["total_amount"]), 2),
            "amount_paid":     round(_num(row["amount_paid"]), 2),
            "service_portion": round(_num(row["service_portion"]), 2),
            "payment_method":  row["payment_method"] or "—",
            "reference_no":    row["reference_no"] or "",
            "notes":           row["notes"] or "",
            "paid_at":         format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)

    paid_sales            = []
    total_gross           = 0.0
    total_service_revenue = 0.0

    for sale in sales_rows:
        sale_id        = sale["id"]
        services_total  = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))
        if sale["status"] == "Paid":
            total_amount = _num(sale["total_amount"])
            total_service_revenue += services_total
            paid_sales.append({
                "sales_number":     sale["sales_number"] or f"#{sale_id}",
                "customer_name":    sale["customer_name"] or "Walk-in",
                "mechanic_name":    sale["mechanic_name"] or "—",
                "services_total":   round(services_total, 2),
                "total_amount":     round(total_amount, 2),
                "status":           sale["status"],
                "payment_method":   sale["payment_method"] or "—",
                "notes":            sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products":         items_by_sale.get(sale_id, []),
                "services":         services_by_sale.get(sale_id, []),
            })
            total_gross += total_amount

    # Build date-bucketed records so the range report can include daily quota misses.
    sales_by_day = {}
    debt_by_day = {}

    for row in sales_rows:
        sale_day = str(row["transaction_date"])[:10]
        sales_by_day.setdefault(sale_day, []).append(row)

    for row in debt_collected_rows:
        paid_day = str(row["paid_at"])[:10]
        debt_by_day.setdefault(paid_day, []).append(row)

    all_days = set(sales_by_day.keys()) | set(debt_by_day.keys())
    quota_failures = []

    for day in sorted(all_days):
        day_mechanic_summary, _ = _calculate_mechanic_payouts(
            *_build_mechanic_maps(
                sales_by_day.get(day, []),
                debt_by_day.get(day, []),
                services_by_sale,
            )
        )

        for row in day_mechanic_summary:
            if row["shop_topup"] > 0:
                quota_failures.append({
                    "date": day,
                    "date_display": format_date(day),
                    "mechanic_id": row["mechanic_id"],
                    "mechanic_name": row["mechanic_name"],
                    "commission_rate": row["commission_rate"],
                    "paid_services_total": row["paid_services_total"],
                    "debt_service_portion": row["debt_service_portion"],
                    "services_total": row["services_total"],
                    "mechanic_cut": row["mechanic_cut"],
                    "shop_topup": row["shop_topup"],
                    "total_payout": row["total_payout"],
                })

    # Keep only days in requested range (in case local DB stores timezone-shifted timestamps).
    quota_failures = [
        row for row in quota_failures
        if start_date <= row["date"] <= end_date
    ]

    mechanic_map, debt_mechanic_map = _build_mechanic_maps(
        sales_rows, debt_collected_rows, services_by_sale
    )
    mechanic_summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)

    items_summary = {}
    for sale in paid_sales:
        for item in sale["products"]:
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {"item_name": key, "quantity": 0, "total": 0.0}
            items_summary[key]["quantity"] += int(item["quantity"] or 0)
            items_summary[key]["total"]    += _num(item["line_total"])

    return {
        "sales":                  paid_sales,
        "unresolved":             all_unresolved,
        "mechanic_summary":       mechanic_summary,
        "items_summary":          sorted(items_summary.values(), key=lambda x: x["item_name"]),
        "total_gross":            round(total_gross, 2),
        "total_mech_cut":         totals["total_mech_cut"],
        "total_shop_topup":       totals["total_shop_topup"],
        "net_revenue":            round(total_gross - totals["total_mech_cut"] - totals["total_shop_topup"] + total_debt_collected, 2),
        "total_shop_commission":  totals["total_shop_commission"],
        "total_service_revenue":  round(total_service_revenue, 2),
        "total_product_revenue":  round(total_gross - total_service_revenue, 2),
        "debt_collected":         debt_collected,
        "total_debt_collected":   total_debt_collected,
        "total_mech_cut_from_paid":  totals["total_mech_cut_from_paid"],
        "total_shop_comm_from_paid": totals["total_shop_comm_from_paid"],
        "total_mech_cut_from_debt":  totals["total_mech_cut_from_debt"],
        "quota_failures":            sorted(
            quota_failures,
            key=lambda row: (row["date"], row["mechanic_name"]),
        ),
    }



