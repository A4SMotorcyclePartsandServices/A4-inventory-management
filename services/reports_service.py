from db.database import get_db
from utils.formatters import format_date


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

MECHANIC_QUOTA = 500.0


def _num(value):
    return float(value or 0)


def _summarize_items_for_profit(paid_sales):
    items_summary = {}
    for sale in paid_sales:
        for item in sale["products"]:
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {
                    "item_name": key,
                    "quantity": 0,
                    "total": 0.0,
                    "cost_total": 0.0,
                    "profit_total": 0.0,
                }
            items_summary[key]["quantity"] += int(item["quantity"] or 0)
            items_summary[key]["total"] += _num(item["line_total"])
            items_summary[key]["cost_total"] += _num(item.get("cost_total"))
            items_summary[key]["profit_total"] += _num(item.get("profit_total"))
        for bundle in sale.get("bundles", []):
            key = f"Bundle - {bundle['bundle_name_snapshot']} ({bundle['subcategory_name_snapshot']})"
            if key not in items_summary:
                items_summary[key] = {
                    "item_name": key,
                    "quantity": 0,
                    "total": 0.0,
                    "cost_total": 0.0,
                    "profit_total": 0.0,
                }
            bundle_revenue = _num(bundle.get("item_value_reference_snapshot"))
            bundle_cost_total = _num(bundle.get("included_items_selling_total"))
            items_summary[key]["quantity"] += 1
            items_summary[key]["total"] += bundle_revenue
            items_summary[key]["cost_total"] += bundle_cost_total
            items_summary[key]["profit_total"] += round(bundle_revenue - bundle_cost_total, 2)
    return sorted(items_summary.values(), key=lambda x: x["item_name"])


def _load_bundles_by_sale(conn, sale_ids):
    if not sale_ids:
        return {}

    placeholders = ",".join(["%s"] * len(sale_ids))

    bundle_rows = conn.execute(
        f"""
        SELECT
            sb.id,
            sb.sale_id,
            sb.bundle_id,
            sb.bundle_version_id,
            sb.bundle_variant_id,
            sb.bundle_name_snapshot,
            sb.vehicle_category_snapshot,
            sb.bundle_version_no_snapshot,
            sb.subcategory_name_snapshot,
            sb.item_value_reference_snapshot,
            sb.shop_share_snapshot,
            sb.mechanic_share_snapshot,
            sb.bundle_price_snapshot
        FROM sales_bundles sb
        WHERE sb.sale_id IN ({placeholders})
        ORDER BY sb.sale_id ASC, sb.id ASC
        """,
        sale_ids,
    ).fetchall()

    if not bundle_rows:
        return {}

    bundle_id_placeholders = ",".join(["%s"] * len(bundle_rows))
    bundle_ids = [row["id"] for row in bundle_rows]

    bundle_service_rows = conn.execute(
        f"""
        SELECT
            sbs.sales_bundle_id,
            sbs.service_id,
            sbs.service_name_snapshot,
            sbs.sort_order
        FROM sales_bundle_services sbs
        WHERE sbs.sales_bundle_id IN ({bundle_id_placeholders})
        ORDER BY sbs.sales_bundle_id ASC, sbs.sort_order ASC, sbs.id ASC
        """,
        bundle_ids,
    ).fetchall()

    bundle_item_rows = conn.execute(
        f"""
        SELECT
            sbi.sales_bundle_id,
            sbi.item_id,
            sbi.item_name_snapshot,
            sbi.quantity,
            sbi.is_included,
            sbi.sort_order,
            COALESCE(i.a4s_selling_price, 0) AS current_selling_price
        FROM sales_bundle_items sbi
        LEFT JOIN items i ON i.id = sbi.item_id
        WHERE sbi.sales_bundle_id IN ({bundle_id_placeholders})
        ORDER BY sbi.sales_bundle_id ASC, sbi.sort_order ASC, sbi.id ASC
        """,
        bundle_ids,
    ).fetchall()

    services_by_bundle = {}
    items_by_bundle = {}

    for row in bundle_service_rows:
        services_by_bundle.setdefault(row["sales_bundle_id"], []).append(dict(row))

    for row in bundle_item_rows:
        item_data = dict(row)
        item_data["estimated_selling_total"] = round(
            _num(item_data.get("current_selling_price")) * int(item_data.get("quantity") or 0),
            2,
        )
        items_by_bundle.setdefault(row["sales_bundle_id"], []).append(item_data)

    bundles_by_sale = {}
    for row in bundle_rows:
        bundle = dict(row)
        bundle_items = items_by_bundle.get(bundle["id"], [])
        included_items_selling_total = round(
            sum(
                _num(item.get("estimated_selling_total"))
                for item in bundle_items
                if int(item.get("is_included") or 0) == 1
            ),
            2,
        )
        bundle["services"] = services_by_bundle.get(bundle["id"], [])
        bundle["items"] = bundle_items
        bundle["included_items_selling_total"] = included_items_selling_total
        bundle["service_component_total"] = round(_num(bundle.get("mechanic_share_snapshot")), 2)
        bundles_by_sale.setdefault(bundle["sale_id"], []).append(bundle)

    return bundles_by_sale
# ─────────────────────────────────────────────
# PRIVATE HELPERS — shared by daily, range, and cash ledger panel
# ─────────────────────────────────────────────

def _build_mechanic_maps(sales_rows, debt_collected_rows, services_by_sale, bundles_by_sale=None):
    """
    Builds mechanic_map (from paid sales) and debt_mechanic_map (from debt payments).
    Extracted so the identical logic isn't duplicated in daily vs range reports.

    mechanic_map      — regular paid services, quota applies
    debt_mechanic_map — debt service portions collected, quota does NOT apply
    """
    mechanic_map      = {}
    debt_mechanic_map = {}
    bundles_by_sale   = bundles_by_sale or {}

    for sale in sales_rows:
        sale_id         = sale["id"]
        mechanic_id     = sale["mechanic_id"]
        mechanic_name   = sale["mechanic_name"] or "—"
        commission_rate = _num(sale["commission_rate"])
        services_total  = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))
        bundle_shop_share = round(
            sum(_num(bundle.get("shop_share_snapshot")) for bundle in bundles_by_sale.get(sale_id, [])),
            2,
        )
        bundle_mech_share = round(
            sum(_num(bundle.get("mechanic_share_snapshot")) for bundle in bundles_by_sale.get(sale_id, [])),
            2,
        )
        payout_service_total = round(services_total + bundle_mech_share, 2)

        if sale["status"] == "Paid" and mechanic_id and (payout_service_total > 0 or bundle_shop_share > 0):
            if mechanic_id not in mechanic_map:
                mechanic_map[mechanic_id] = {
                    "mechanic_name":            mechanic_name,
                    "commission_rate":          commission_rate,
                    "paid_services_total":      0.0,
                    "bundle_shop_share_total":  0.0,
                    "bundle_mech_share_total":  0.0,
                }
            mechanic_map[mechanic_id]["paid_services_total"] += services_total
            mechanic_map[mechanic_id]["bundle_shop_share_total"] += bundle_shop_share
            mechanic_map[mechanic_id]["bundle_mech_share_total"] += bundle_mech_share

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
        bundle_shop_share    = round(_num(regular.get("bundle_shop_share_total", 0.0)), 2)
        bundle_mech_share    = round(_num(regular.get("bundle_mech_share_total", 0.0)), 2)
        debt_service_portion = round(_num(debt.get("debt_service_total", 0.0)), 2)

        payout_base_total = round(paid_services + bundle_mech_share, 2)
        regular_mech_cut   = round(payout_base_total * commission_rate, 2)
        regular_shop_share = round(paid_services - (paid_services * commission_rate), 2)

        debt_mech_cut   = round(debt_service_portion * commission_rate, 2)
        debt_shop_share = round(debt_service_portion - debt_mech_cut, 2)

        total_mech_cut_this = round(regular_mech_cut + debt_mech_cut, 2)
        combined_services = round(
            paid_services + bundle_mech_share + debt_service_portion,
            2,
        )

        if payout_base_total > 0 and combined_services < MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_QUOTA - total_mech_cut_this, 2))
        else:
            shop_topup = 0.0

        total_shop_share = round(regular_shop_share + bundle_shop_share + debt_shop_share, 2)
        total_payout     = round(total_mech_cut_this + shop_topup, 2)

        total_mech_cut        += total_mech_cut_this
        total_shop_topup      += shop_topup
        total_shop_commission += total_shop_share
        total_mech_cut_from_paid  += regular_mech_cut
        total_shop_comm_from_paid += regular_shop_share + bundle_shop_share
        total_mech_cut_from_debt  += debt_mech_cut

        mechanic_summary.append({
            "mechanic_id":           mech_id,
            "mechanic_name":         mechanic_name,
            "commission_rate":       commission_rate,
            "paid_services_total":   paid_services,
            "bundle_shop_share":     bundle_shop_share,
            "bundle_mech_share":     bundle_mech_share,
            "payout_base_total":     payout_base_total,
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
    bundles_by_sale = {}
    if sale_ids:
        sale_id_placeholders = ",".join(["%s"] * len(sale_ids))
        services_rows = conn.execute(f"""
            SELECT ss.sale_id, ss.price
            FROM sales_services ss
            WHERE ss.sale_id IN ({sale_id_placeholders})
        """, sale_ids).fetchall()
        for row in services_rows:
            services_by_sale.setdefault(row["sale_id"], []).append({"price": row["price"]})
        bundles_by_sale = _load_bundles_by_sale(conn, sale_ids)

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
            bundles_by_sale,
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
    bundles_by_sale = _load_bundles_by_sale(conn, sale_ids)
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
            "bundles":          bundles_by_sale.get(sale_id, []),
        })
    return result


def get_sales_report_by_date(report_date):
    """
    Pulls all completed sales for a given date for the End-of-Day PDF report.
    Return value is identical to before - PDF template is untouched.
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
            pm.name           AS payment_method,
            se.exchange_number,
            se.original_sale_id
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN sale_exchanges se  ON se.replacement_sale_id = s.id
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

    refund_rows = conn.execute("""
        SELECT
            sr.id,
            sr.refund_number,
            sr.refund_amount,
            sr.reason,
            sr.notes,
            sr.refund_date,
            sr.refunded_by_username,
            s.sales_number,
            s.customer_name,
            se.exchange_number,
            se.replacement_sale_id
        FROM sale_refunds sr
        JOIN sales s ON s.id = sr.sale_id
        LEFT JOIN sale_exchanges se ON se.refund_id = sr.id
        WHERE DATE(sr.refund_date) = %s
        ORDER BY sr.refund_date ASC, sr.id ASC
    """, (report_date,)).fetchall()

    if not sales_rows and not all_unresolved and not debt_collected_rows and not refund_rows:
        conn.close()
        return []

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids = [row["id"] for row in sales_rows]
    items_by_sale = {}
    services_by_sale = {}
    bundles_by_sale = {}
    refund_items_by_id = {}

    if paid_sale_ids:
        placeholders = ",".join(["%s"] * len(paid_sale_ids))
        items_rows = conn.execute(f"""
            SELECT
                si.sale_id,
                i.name AS item_name,
                si.quantity,
                si.original_unit_price,
                si.discount_percent,
                si.discount_amount,
                si.final_unit_price,
                si.cost_per_piece_snapshot,
                (si.quantity * si.final_unit_price) AS line_total,
                (si.quantity * si.cost_per_piece_snapshot) AS cost_total,
                (si.quantity * (si.final_unit_price - si.cost_per_piece_snapshot)) AS profit_total
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
        bundles_by_sale = _load_bundles_by_sale(conn, all_sale_ids)

    refund_ids = [row["id"] for row in refund_rows]
    if refund_ids:
        placeholders = ",".join(["%s"] * len(refund_ids))
        refund_item_rows = conn.execute(f"""
            SELECT
                sri.refund_id,
                i.name AS item_name,
                sri.quantity,
                sri.unit_price,
                sri.line_total
            FROM sale_refund_items sri
            JOIN items i ON i.id = sri.item_id
            WHERE sri.refund_id IN ({placeholders})
            ORDER BY sri.refund_id ASC, i.name ASC, sri.id ASC
        """, refund_ids).fetchall()
        for row in refund_item_rows:
            refund_items_by_id.setdefault(row["refund_id"], []).append({
                "item_name": row["item_name"],
                "quantity": int(row["quantity"] or 0),
                "unit_price": round(_num(row["unit_price"]), 2),
                "line_total": round(_num(row["line_total"]), 2),
            })

    conn.close()

    debt_collected = [
        {
            "sales_number": row["sales_number"] or f"#{row['sale_id']}",
            "customer_name": row["customer_name"] or "Walk-in",
            "total_amount": round(_num(row["total_amount"]), 2),
            "amount_paid": round(_num(row["amount_paid"]), 2),
            "service_portion": round(_num(row["service_portion"]), 2),
            "payment_method": row["payment_method"] or "-",
            "reference_no": row["reference_no"] or "",
            "notes": row["notes"] or "",
            "paid_at": format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)
    refunds = [
        {
            "refund_number": row["refund_number"] or f"Refund #{row['id']}",
            "sales_number": row["sales_number"] or "-",
            "customer_name": row["customer_name"] or "Walk-in",
            "refund_amount": round(_num(row["refund_amount"]), 2),
            "reason": row["reason"] or "",
            "notes": row["notes"] or "",
            "refund_date": format_date(row["refund_date"], show_time=True),
            "refunded_by_username": row["refunded_by_username"] or "System",
            "items": refund_items_by_id.get(row["id"], []),
            "report_label": "Exchange/Refund" if row["exchange_number"] else "Refund",
            "exchange_number": row["exchange_number"] or "",
        }
        for row in refund_rows
    ]
    total_refunds = round(sum(r["refund_amount"] for r in refunds), 2)

    paid_sales = []
    total_gross = 0.0
    total_service_revenue = 0.0
    total_product_cost = 0.0
    total_product_profit = 0.0

    for sale in sales_rows:
        sale_id = sale["id"]
        sale_bundles = bundles_by_sale.get(sale_id, [])
        standalone_services_total = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))
        bundle_service_total = round(sum(_num(bundle.get("service_component_total")) for bundle in sale_bundles), 2)
        bundle_shop_total = round(sum(_num(bundle.get("shop_share_snapshot")) for bundle in sale_bundles), 2)
        bundle_product_revenue = round(sum(_num(bundle.get("item_value_reference_snapshot")) for bundle in sale_bundles), 2)
        services_total = round(standalone_services_total + bundle_service_total, 2)
        service_revenue_total = round(standalone_services_total + bundle_service_total + bundle_shop_total, 2)
        if sale["status"] == "Paid":
            total_amount = _num(sale["total_amount"])
            sale_products = items_by_sale.get(sale_id, [])
            bundle_product_cost = round(
                sum(_num(bundle.get("included_items_selling_total")) for bundle in sale_bundles),
                2,
            )
            sale_product_cost = round(
                sum(_num(item.get("cost_total")) for item in sale_products) + bundle_product_cost,
                2,
            )
            sale_product_profit = round(
                sum(_num(item.get("profit_total")) for item in sale_products)
                + bundle_product_revenue
                - bundle_product_cost,
                2,
            )
            total_service_revenue += service_revenue_total
            paid_sales.append({
                "sales_number": sale["sales_number"] or f"#{sale_id}",
                "customer_name": sale["customer_name"] or "Walk-in",
                "mechanic_name": sale["mechanic_name"] or "-",
                "services_total": services_total,
                "standalone_services_total": round(standalone_services_total, 2),
                "bundle_service_total": bundle_service_total,
                "bundle_shop_total": bundle_shop_total,
                "service_revenue_total": service_revenue_total,
                "product_cost_total": sale_product_cost,
                "product_profit_total": sale_product_profit,
                "total_amount": round(total_amount, 2),
                "status": sale["status"],
                "payment_method": sale["payment_method"] or "-",
                "notes": sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products": sale_products,
                "services": services_by_sale.get(sale_id, []),
                "bundles": sale_bundles,
                "report_label": "Exchange/Replacement" if sale["exchange_number"] else "Sale",
                "exchange_number": sale["exchange_number"] or "",
            })
            total_gross += total_amount
            total_product_cost += sale_product_cost
            total_product_profit += sale_product_profit

    mechanic_map, debt_mechanic_map = _build_mechanic_maps(
        sales_rows, debt_collected_rows, services_by_sale, bundles_by_sale
    )
    mechanic_summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)
    total_shop_comm_from_paid = round(total_service_revenue - totals["total_mech_cut_from_paid"], 2)

    items_summary = _summarize_items_for_profit(paid_sales)

    return {
        "sales": paid_sales,
        "unresolved": all_unresolved,
        "mechanic_summary": mechanic_summary,
        "items_summary": items_summary,
        "total_gross": round(total_gross, 2),
        "total_mech_cut": totals["total_mech_cut"],
        "total_shop_topup": totals["total_shop_topup"],
        "net_revenue": round(total_gross - total_refunds - totals["total_mech_cut"] - totals["total_shop_topup"] + total_debt_collected, 2),
        "total_shop_commission": totals["total_shop_commission"],
        "total_service_revenue": round(total_service_revenue, 2),
        "total_product_revenue": round(total_gross - total_service_revenue - total_refunds, 2),
        "total_product_cost": round(total_product_cost, 2),
        "total_product_profit": round(total_product_profit, 2),
        "debt_collected": debt_collected,
        "total_debt_collected": total_debt_collected,
        "total_mech_cut_from_paid": totals["total_mech_cut_from_paid"],
        "total_shop_comm_from_paid": total_shop_comm_from_paid,
        "total_mech_cut_from_debt": totals["total_mech_cut_from_debt"],
        "refunds": refunds,
        "total_refunds": total_refunds,
    }


def get_sales_report_by_range(start_date, end_date):
    """
    Pulls all completed sales between start_date and end_date (inclusive).
    Return value is identical to before - PDF template is untouched.
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
            pm.name           AS payment_method,
            se.exchange_number,
            se.original_sale_id
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN sale_exchanges se  ON se.replacement_sale_id = s.id
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

    refund_rows = conn.execute("""
        SELECT
            sr.id,
            sr.refund_number,
            sr.refund_amount,
            sr.reason,
            sr.notes,
            sr.refund_date,
            sr.refunded_by_username,
            s.sales_number,
            s.customer_name,
            se.exchange_number,
            se.replacement_sale_id
        FROM sale_refunds sr
        JOIN sales s ON s.id = sr.sale_id
        LEFT JOIN sale_exchanges se ON se.refund_id = sr.id
        WHERE DATE(sr.refund_date) BETWEEN %s AND %s
        ORDER BY sr.refund_date ASC, sr.id ASC
    """, (start_date, end_date)).fetchall()

    if not sales_rows and not all_unresolved and not debt_collected_rows and not refund_rows:
        conn.close()
        return []

    paid_sale_ids = [row["id"] for row in sales_rows if row["status"] == "Paid"]
    all_sale_ids = [row["id"] for row in sales_rows]
    items_by_sale = {}
    services_by_sale = {}
    bundles_by_sale = {}
    refund_items_by_id = {}

    if paid_sale_ids:
        placeholders = ",".join(["%s"] * len(paid_sale_ids))
        items_rows = conn.execute(f"""
            SELECT
                si.sale_id,
                i.name AS item_name,
                si.quantity,
                si.original_unit_price,
                si.discount_percent,
                si.discount_amount,
                si.final_unit_price,
                si.cost_per_piece_snapshot,
                (si.quantity * si.final_unit_price) AS line_total,
                (si.quantity * si.cost_per_piece_snapshot) AS cost_total,
                (si.quantity * (si.final_unit_price - si.cost_per_piece_snapshot)) AS profit_total
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
        bundles_by_sale = _load_bundles_by_sale(conn, all_sale_ids)

    refund_ids = [row["id"] for row in refund_rows]
    if refund_ids:
        placeholders = ",".join(["%s"] * len(refund_ids))
        refund_item_rows = conn.execute(f"""
            SELECT
                sri.refund_id,
                i.name AS item_name,
                sri.quantity,
                sri.unit_price,
                sri.line_total
            FROM sale_refund_items sri
            JOIN items i ON i.id = sri.item_id
            WHERE sri.refund_id IN ({placeholders})
            ORDER BY sri.refund_id ASC, i.name ASC, sri.id ASC
        """, refund_ids).fetchall()
        for row in refund_item_rows:
            refund_items_by_id.setdefault(row["refund_id"], []).append({
                "item_name": row["item_name"],
                "quantity": int(row["quantity"] or 0),
                "unit_price": round(_num(row["unit_price"]), 2),
                "line_total": round(_num(row["line_total"]), 2),
            })

    conn.close()

    debt_collected = [
        {
            "sales_number": row["sales_number"] or f"#{row['sale_id']}",
            "customer_name": row["customer_name"] or "Walk-in",
            "total_amount": round(_num(row["total_amount"]), 2),
            "amount_paid": round(_num(row["amount_paid"]), 2),
            "service_portion": round(_num(row["service_portion"]), 2),
            "payment_method": row["payment_method"] or "-",
            "reference_no": row["reference_no"] or "",
            "notes": row["notes"] or "",
            "paid_at": format_date(row["paid_at"], show_time=True),
        }
        for row in debt_collected_rows
    ]
    total_debt_collected = round(sum(r["amount_paid"] for r in debt_collected), 2)
    refunds = [
        {
            "refund_number": row["refund_number"] or f"Refund #{row['id']}",
            "sales_number": row["sales_number"] or "-",
            "customer_name": row["customer_name"] or "Walk-in",
            "refund_amount": round(_num(row["refund_amount"]), 2),
            "reason": row["reason"] or "",
            "notes": row["notes"] or "",
            "refund_date": format_date(row["refund_date"], show_time=True),
            "refunded_by_username": row["refunded_by_username"] or "System",
            "items": refund_items_by_id.get(row["id"], []),
            "report_label": "Exchange/Refund" if row["exchange_number"] else "Refund",
            "exchange_number": row["exchange_number"] or "",
        }
        for row in refund_rows
    ]
    total_refunds = round(sum(r["refund_amount"] for r in refunds), 2)

    paid_sales = []
    total_gross = 0.0
    total_service_revenue = 0.0
    total_product_cost = 0.0
    total_product_profit = 0.0

    for sale in sales_rows:
        sale_id = sale["id"]
        sale_bundles = bundles_by_sale.get(sale_id, [])
        standalone_services_total = sum(_num(svc["price"]) for svc in services_by_sale.get(sale_id, []))
        bundle_service_total = round(sum(_num(bundle.get("service_component_total")) for bundle in sale_bundles), 2)
        bundle_shop_total = round(sum(_num(bundle.get("shop_share_snapshot")) for bundle in sale_bundles), 2)
        bundle_product_revenue = round(sum(_num(bundle.get("item_value_reference_snapshot")) for bundle in sale_bundles), 2)
        services_total = round(standalone_services_total + bundle_service_total, 2)
        service_revenue_total = round(standalone_services_total + bundle_service_total + bundle_shop_total, 2)
        if sale["status"] == "Paid":
            total_amount = _num(sale["total_amount"])
            sale_products = items_by_sale.get(sale_id, [])
            bundle_product_cost = round(
                sum(_num(bundle.get("included_items_selling_total")) for bundle in sale_bundles),
                2,
            )
            sale_product_cost = round(
                sum(_num(item.get("cost_total")) for item in sale_products) + bundle_product_cost,
                2,
            )
            sale_product_profit = round(
                sum(_num(item.get("profit_total")) for item in sale_products)
                + bundle_product_revenue
                - bundle_product_cost,
                2,
            )
            total_service_revenue += service_revenue_total
            paid_sales.append({
                "sales_number": sale["sales_number"] or f"#{sale_id}",
                "customer_name": sale["customer_name"] or "Walk-in",
                "mechanic_name": sale["mechanic_name"] or "-",
                "services_total": services_total,
                "standalone_services_total": round(standalone_services_total, 2),
                "bundle_service_total": bundle_service_total,
                "bundle_shop_total": bundle_shop_total,
                "service_revenue_total": service_revenue_total,
                "product_cost_total": sale_product_cost,
                "product_profit_total": sale_product_profit,
                "total_amount": round(total_amount, 2),
                "status": sale["status"],
                "payment_method": sale["payment_method"] or "-",
                "notes": sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products": sale_products,
                "services": services_by_sale.get(sale_id, []),
                "bundles": sale_bundles,
                "report_label": "Exchange/Replacement" if sale["exchange_number"] else "Sale",
                "exchange_number": sale["exchange_number"] or "",
            })
            total_gross += total_amount
            total_product_cost += sale_product_cost
            total_product_profit += sale_product_profit

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
                bundles_by_sale,
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

    quota_failures = [
        row for row in quota_failures
        if start_date <= row["date"] <= end_date
    ]

    mechanic_map, debt_mechanic_map = _build_mechanic_maps(
        sales_rows, debt_collected_rows, services_by_sale, bundles_by_sale
    )
    mechanic_summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)
    total_shop_comm_from_paid = round(total_service_revenue - totals["total_mech_cut_from_paid"], 2)

    items_summary = _summarize_items_for_profit(paid_sales)

    return {
        "sales": paid_sales,
        "unresolved": all_unresolved,
        "mechanic_summary": mechanic_summary,
        "items_summary": items_summary,
        "total_gross": round(total_gross, 2),
        "total_mech_cut": totals["total_mech_cut"],
        "total_shop_topup": totals["total_shop_topup"],
        "net_revenue": round(total_gross - total_refunds - totals["total_mech_cut"] - totals["total_shop_topup"] + total_debt_collected, 2),
        "total_shop_commission": totals["total_shop_commission"],
        "total_service_revenue": round(total_service_revenue, 2),
        "total_product_revenue": round(total_gross - total_service_revenue - total_refunds, 2),
        "total_product_cost": round(total_product_cost, 2),
        "total_product_profit": round(total_product_profit, 2),
        "debt_collected": debt_collected,
        "total_debt_collected": total_debt_collected,
        "total_mech_cut_from_paid": totals["total_mech_cut_from_paid"],
        "total_shop_comm_from_paid": total_shop_comm_from_paid,
        "total_mech_cut_from_debt": totals["total_mech_cut_from_debt"],
        "refunds": refunds,
        "total_refunds": total_refunds,
        "quota_failures": sorted(
            quota_failures,
            key=lambda row: (row["date"], row["mechanic_name"]),
        ),
    }
