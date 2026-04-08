from db.database import get_db
from utils.formatters import format_date


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

MECHANIC_QUOTA = 625.0
MECHANIC_PAYOUT_CAP = 500.0


def _num(value):
    return float(value or 0)


def _bool_flag(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _build_sale_payment_summary_map(conn, sale_ids):
    normalized_sale_ids = [int(sale_id) for sale_id in (sale_ids or []) if sale_id is not None]
    if not normalized_sale_ids:
        return {}

    rows = conn.execute(
        """
        SELECT
            s.id AS sale_id,
            COALESCE(
                NULLIF(STRING_AGG(DISTINCT pm.name, ' + ' ORDER BY pm.name), ''),
                legacy_pm.name,
                '—'
            ) AS payment_method
        FROM sales s
        LEFT JOIN sale_payments sp ON sp.sale_id = s.id
        LEFT JOIN payment_methods pm ON pm.id = sp.payment_method_id
        LEFT JOIN payment_methods legacy_pm ON legacy_pm.id = s.payment_method_id
        WHERE s.id = ANY(%s)
        GROUP BY s.id, legacy_pm.name
        """,
        (normalized_sale_ids,),
    ).fetchall()
    return {int(row["sale_id"]): row["payment_method"] for row in rows}


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
        return {
            "total_non_cash_sales": 0.0,
            "total_non_cash_claimed": 0.0,
            "total_non_cash_floating": 0.0,
        }

    sale_totals = {
        int(row["id"]): round(_num(row["total_amount"]), 2)
        for row in sale_rows
    }
    claimed_sale_ids = set()
    if sale_totals:
        sale_ids = list(sale_totals.keys())
        placeholders = ",".join(["%s"] * len(sale_ids))
        claimed_rows = conn.execute(
            """
            SELECT DISTINCT cfc.sale_id
            FROM cash_float_claims cfc
            JOIN cash_entries ce ON ce.id = cfc.cash_entry_id
            WHERE cfc.sale_id = ANY(%s)
              AND COALESCE(ce.is_deleted, FALSE) = FALSE
              AND DATE(ce.created_at) <= %s
            """,
            [sale_ids, end_date],
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
            """
            SELECT DISTINCT cdpc.debt_payment_id
            FROM cash_debt_payment_claims cdpc
            JOIN cash_entries ce ON ce.id = cdpc.cash_entry_id
            WHERE cdpc.debt_payment_id = ANY(%s)
              AND COALESCE(ce.is_deleted, FALSE) = FALSE
              AND DATE(ce.created_at) <= %s
            """,
            [debt_payment_ids, end_date],
        ).fetchall()
        claimed_debt_payment_ids = {int(row["debt_payment_id"]) for row in claimed_rows}

    total_non_cash_sales = round(sum(sale_totals.values()) + sum(debt_payment_totals.values()), 2)
    total_non_cash_claimed = round(
        sum(amount for sale_id, amount in sale_totals.items() if sale_id in claimed_sale_ids)
        + sum(
            amount
            for debt_payment_id, amount in debt_payment_totals.items()
            if debt_payment_id in claimed_debt_payment_ids
        ),
        2,
    )

    return {
        "total_non_cash_sales": total_non_cash_sales,
        "total_non_cash_claimed": total_non_cash_claimed,
        "total_non_cash_floating": round(total_non_cash_sales - total_non_cash_claimed, 2),
    }


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
            bundle_cost_total = _num(bundle.get("included_items_cost_total"))
            items_summary[key]["quantity"] += 1
            items_summary[key]["total"] += bundle_revenue
            items_summary[key]["cost_total"] += bundle_cost_total
            items_summary[key]["profit_total"] += round(bundle_revenue - bundle_cost_total, 2)
    return sorted(items_summary.values(), key=lambda x: x["item_name"])


def _summarize_mechanic_supply_items(mechanic_supply_sales):
    items_summary = {}
    for sale in mechanic_supply_sales:
        for item in sale.get("products", []):
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {
                    "item_name": key,
                    "quantity": 0,
                    "total": 0.0,
                }
            items_summary[key]["quantity"] += int(item.get("quantity") or 0)
            items_summary[key]["total"] += _num(item.get("line_total"))
    return sorted(items_summary.values(), key=lambda x: x["item_name"])


def _summarize_mechanic_supply_items_with_cost(mechanic_supply_sales):
    items_summary = {}
    for sale in mechanic_supply_sales:
        for item in sale.get("products", []):
            key = item["item_name"]
            if key not in items_summary:
                items_summary[key] = {
                    "item_name": key,
                    "quantity": 0,
                    "revenue_total": 0.0,
                    "cost_total": 0.0,
                }
            items_summary[key]["quantity"] += int(item.get("quantity") or 0)
            items_summary[key]["revenue_total"] += _num(item.get("line_total"))
            items_summary[key]["cost_total"] += _num(item.get("cost_total"))
    return sorted(items_summary.values(), key=lambda x: x["item_name"])


def _build_mechanic_supply_report_context(start_date, end_date):
    conn = get_db()
    sales_rows = conn.execute(
        """
        SELECT
            s.id,
            s.sales_number,
            s.transaction_date,
            s.total_amount,
            s.notes,
            m.name AS mechanic_name
        FROM sales s
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') = 'MECHANIC_SUPPLY'
        ORDER BY s.transaction_date ASC, s.id ASC
        """,
        (start_date, end_date),
    ).fetchall()

    if not sales_rows:
        conn.close()
        return {
            "transactions": [],
            "items_summary": [],
            "total_transactions": 0,
            "total_revenue_reference": 0.0,
            "total_cost": 0.0,
        }

    sale_ids = [row["id"] for row in sales_rows]
    item_rows = conn.execute(
        """
        SELECT
            si.sale_id,
            i.name AS item_name,
            si.quantity,
            si.final_unit_price,
            si.cost_per_piece_snapshot,
            (si.quantity * si.final_unit_price) AS line_total,
            (si.quantity * si.cost_per_piece_snapshot) AS cost_total
        FROM sales_items si
        JOIN items i ON i.id = si.item_id
        WHERE si.sale_id = ANY(%s)
        ORDER BY si.sale_id ASC, i.name ASC
        """,
        (sale_ids,),
    ).fetchall()
    conn.close()

    items_by_sale = {}
    for row in item_rows:
        items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    transactions = []
    total_revenue_reference = 0.0
    total_cost = 0.0
    for sale in sales_rows:
        sale_id = int(sale["id"])
        products = items_by_sale.get(sale_id, [])
        sale_cost = round(sum(_num(item.get("cost_total")) for item in products), 2)
        sale_revenue_reference = round(_num(sale.get("total_amount")), 2)
        total_revenue_reference += sale_revenue_reference
        total_cost += sale_cost
        transactions.append({
            "sales_number": sale["sales_number"] or f"#{sale_id}",
            "mechanic_name": sale["mechanic_name"] or "-",
            "transaction_date": format_date(sale["transaction_date"], show_time=True),
            "products": products,
            "revenue_reference_total": sale_revenue_reference,
            "cost_total": sale_cost,
            "notes": sale["notes"] or "",
        })

    return {
        "transactions": transactions,
        "items_summary": _summarize_mechanic_supply_items_with_cost(transactions),
        "total_transactions": len(transactions),
        "total_revenue_reference": round(total_revenue_reference, 2),
        "total_cost": round(total_cost, 2),
    }


def _load_bundles_by_sale(conn, sale_ids):
    if not sale_ids:
        return {}

    bundle_rows = conn.execute(
        """
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
        WHERE sb.sale_id = ANY(%s)
        ORDER BY sb.sale_id ASC, sb.id ASC
        """,
        (sale_ids,),
    ).fetchall()

    if not bundle_rows:
        return {}

    bundle_ids = [row["id"] for row in bundle_rows]

    bundle_service_rows = conn.execute(
        """
        SELECT
            sbs.sales_bundle_id,
            sbs.service_id,
            sbs.service_name_snapshot,
            sbs.sort_order
        FROM sales_bundle_services sbs
        WHERE sbs.sales_bundle_id = ANY(%s)
        ORDER BY sbs.sales_bundle_id ASC, sbs.sort_order ASC, sbs.id ASC
        """,
        (bundle_ids,),
    ).fetchall()

    bundle_item_rows = conn.execute(
        """
        SELECT
            sbi.sales_bundle_id,
            sbi.item_id,
            sbi.item_name_snapshot,
            sbi.quantity,
            sbi.cost_per_piece_snapshot,
            sbi.selling_price_snapshot,
            sbi.line_total_snapshot,
            sbi.is_included,
            sbi.sort_order
        FROM sales_bundle_items sbi
        WHERE sbi.sales_bundle_id = ANY(%s)
        ORDER BY sbi.sales_bundle_id ASC, sbi.sort_order ASC, sbi.id ASC
        """,
        (bundle_ids,),
    ).fetchall()

    services_by_bundle = {}
    items_by_bundle = {}

    for row in bundle_service_rows:
        services_by_bundle.setdefault(row["sales_bundle_id"], []).append(dict(row))

    for row in bundle_item_rows:
        item_data = dict(row)
        item_data["estimated_selling_total"] = round(
            _num(item_data.get("line_total_snapshot")),
            2,
        )
        item_data["estimated_cost_total"] = round(
            _num(item_data.get("cost_per_piece_snapshot")) * int(item_data.get("quantity") or 0),
            2,
        )
        items_by_bundle.setdefault(row["sales_bundle_id"], []).append(item_data)

    bundles_by_sale = {}
    for row in bundle_rows:
        bundle = dict(row)
        bundle_items = items_by_bundle.get(bundle["id"], [])
        included_items_cost_total = round(
            sum(
                _num(item.get("estimated_cost_total"))
                for item in bundle_items
                if int(item.get("is_included") or 0) == 1
            ),
            2,
        )
        bundle["services"] = services_by_bundle.get(bundle["id"], [])
        bundle["items"] = bundle_items
        bundle["included_items_cost_total"] = included_items_cost_total
        bundle["service_component_total"] = round(_num(bundle.get("mechanic_share_snapshot")), 2)
        bundles_by_sale.setdefault(bundle["sale_id"], []).append(bundle)

    return bundles_by_sale


def _load_services_by_sale(conn, sale_ids):
    if not sale_ids:
        return {}

    service_rows = conn.execute(
        """
        SELECT
            ss.sale_id,
            ss.service_id,
            ss.price,
            ss.mechanic_id,
            sv.name AS service_name,
            COALESCE(sv.mechanic_payout_exempt, 0) AS mechanic_payout_exempt,
            m.name AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup
        FROM sales_services ss
        JOIN sales s ON s.id = ss.sale_id
        JOIN services sv ON sv.id = ss.service_id
        LEFT JOIN mechanics m ON m.id = ss.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
          ON mqto.mechanic_id = ss.mechanic_id
         AND mqto.quota_date = DATE(s.transaction_date)
        WHERE ss.sale_id = ANY(%s)
        ORDER BY ss.sale_id ASC, sv.name ASC, ss.id ASC
        """,
        (sale_ids,),
    ).fetchall()

    services_by_sale = {}
    for row in service_rows:
        payload = dict(row)
        payload["mechanic_id"] = int(payload["mechanic_id"]) if payload.get("mechanic_id") is not None else None
        payload["mechanic_payout_exempt"] = _bool_flag(payload.get("mechanic_payout_exempt"), default=False)
        services_by_sale.setdefault(row["sale_id"], []).append(payload)
    return services_by_sale


def _get_debt_payout_allocations(conn, *, report_date=None, start_date=None, end_date=None, report_dates=None):
    normalized_dates = sorted({str(value) for value in (report_dates or []) if value})
    if normalized_dates:
        target_dates = set(normalized_dates)
        upper_bound_date = normalized_dates[-1]
        def _is_target_payout_date(value):
            return str(value) in target_dates
    elif report_date:
        upper_bound_date = report_date
        def _is_target_payout_date(value):
            return str(value) == str(report_date)
    elif start_date and end_date:
        upper_bound_date = end_date
        def _is_target_payout_date(value):
            day = str(value)
            return str(start_date) <= day <= str(end_date)
    else:
        return []

    payment_rows = conn.execute(
        f"""
        WITH sale_service_totals AS (
            SELECT
                ss.sale_id,
                ss.mechanic_id,
                SUM(CASE WHEN COALESCE(sv.mechanic_payout_exempt, 0) = 1 THEN ss.price ELSE 0 END) AS exempt_service_total,
                SUM(CASE WHEN COALESCE(sv.mechanic_payout_exempt, 0) = 1 THEN 0 ELSE ss.price END) AS eligible_service_total
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.mechanic_id IS NOT NULL
            GROUP BY ss.sale_id, ss.mechanic_id
        ),
        sale_service_totals_by_sale AS (
            SELECT
                sale_id,
                SUM(exempt_service_total) AS total_exempt_service_total,
                SUM(eligible_service_total) AS total_eligible_service_total
            FROM sale_service_totals
            GROUP BY sale_id
        )
        SELECT
            dp.id AS debt_payment_id,
            dp.sale_id,
            dp.amount_paid,
            dp.service_portion,
            dp.paid_at,
            DATE(dp.paid_at) AS payout_date,
            sst.mechanic_id,
            m.name AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup,
            COALESCE(sst.exempt_service_total, 0) AS exempt_service_total,
            COALESCE(sst.eligible_service_total, 0) AS eligible_service_total,
            COALESCE(st.total_exempt_service_total, 0) AS total_exempt_service_total,
            COALESCE(st.total_eligible_service_total, 0) AS total_eligible_service_total
        FROM debt_payments dp
        JOIN sale_service_totals sst
          ON sst.sale_id = dp.sale_id
        JOIN sale_service_totals_by_sale st
          ON st.sale_id = dp.sale_id
        LEFT JOIN mechanics m
          ON m.id = sst.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
          ON mqto.mechanic_id = sst.mechanic_id
         AND mqto.quota_date = DATE(dp.paid_at)
        WHERE DATE(dp.paid_at) <= %s
        ORDER BY dp.paid_at ASC, dp.id ASC, sst.mechanic_id ASC
        """,
        (upper_bound_date,),
    ).fetchall()

    allocations = []
    grouped_by_payment = {}
    sale_exempt_remaining = {}

    for row in payment_rows:
        payment_key = (int(row["debt_payment_id"] or 0), int(row["sale_id"] or 0))
        grouped_by_payment.setdefault(payment_key, []).append(dict(row))
        sale_id = int(row["sale_id"] or 0)
        if sale_id not in sale_exempt_remaining:
            sale_exempt_remaining[sale_id] = round(_num(row.get("total_exempt_service_total")), 2)

    for payment_key in sorted(
        grouped_by_payment.keys(),
        key=lambda key: (
            grouped_by_payment[key][0].get("paid_at"),
            grouped_by_payment[key][0].get("debt_payment_id"),
            grouped_by_payment[key][0].get("sale_id"),
        ),
    ):
        group_rows = grouped_by_payment[payment_key]
        sale_id = int(group_rows[0]["sale_id"] or 0)
        payment_service_portion = round(_num(group_rows[0].get("service_portion")), 2)
        exempt_remaining = round(sale_exempt_remaining.get(sale_id, 0.0), 2)
        exempt_applied = min(payment_service_portion, exempt_remaining)
        eligible_portion = round(max(0.0, payment_service_portion - exempt_applied), 2)
        sale_exempt_remaining[sale_id] = round(max(0.0, exempt_remaining - exempt_applied), 2)

        eligible_total = round(_num(group_rows[0].get("total_eligible_service_total")), 2)
        running_allocated = 0.0
        eligible_rows = [row for row in group_rows if _num(row.get("eligible_service_total")) > 0]

        is_target_payment = _is_target_payout_date(group_rows[0].get("payout_date"))

        for index, row in enumerate(eligible_rows):
            if eligible_portion <= 0:
                allocated = 0.0
            elif index == len(eligible_rows) - 1:
                allocated = round(eligible_portion - running_allocated, 2)
            elif eligible_total <= 0:
                allocated = 0.0
            else:
                allocated = round(
                    eligible_portion * _num(row.get("eligible_service_total")) / eligible_total,
                    2,
                )
                running_allocated = round(running_allocated + allocated, 2)

            row["service_portion"] = max(0.0, allocated)
            row["shop_only_service_portion"] = 0.0
            if is_target_payment:
                allocations.append(row)

        exempt_total = round(_num(group_rows[0].get("total_exempt_service_total")), 2)
        exempt_rows = [row for row in group_rows if _num(row.get("exempt_service_total")) > 0]
        running_exempt_allocated = 0.0
        for index, row in enumerate(exempt_rows):
            if exempt_applied <= 0:
                allocated_exempt = 0.0
            elif index == len(exempt_rows) - 1:
                allocated_exempt = round(exempt_applied - running_exempt_allocated, 2)
            elif exempt_total <= 0:
                allocated_exempt = 0.0
            else:
                allocated_exempt = round(
                    exempt_applied * _num(row.get("exempt_service_total")) / exempt_total,
                    2,
                )
                running_exempt_allocated = round(running_exempt_allocated + allocated_exempt, 2)

            if allocated_exempt <= 0:
                continue

            shop_row = dict(row)
            shop_row["service_portion"] = 0.0
            shop_row["shop_only_service_portion"] = max(0.0, allocated_exempt)
            if is_target_payment:
                allocations.append(shop_row)

    return allocations


def _compose_sale_mechanic_label(sale, services=None, bundles=None):
    names = []
    seen = set()

    for service in services or []:
        name = str(service.get("mechanic_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)

    if bundles:
        bundle_owner_name = str((sale or {}).get("mechanic_name") or "").strip()
        if bundle_owner_name and bundle_owner_name not in seen:
            seen.add(bundle_owner_name)
            names.append(bundle_owner_name)

    if names:
        return " / ".join(names)

    fallback_name = str((sale or {}).get("mechanic_name") or "").strip()
    return fallback_name or "-"
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
    mechanic_map = {}
    debt_mechanic_map = {}
    bundles_by_sale = bundles_by_sale or {}

    for sale in sales_rows:
        sale_id = sale["id"]
        service_rows = services_by_sale.get(sale_id, [])
        service_totals_by_mechanic = {}

        for svc in service_rows:
            mechanic_id = svc.get("mechanic_id") or sale.get("mechanic_id")
            if not mechanic_id:
                continue
            mechanic_id = int(mechanic_id)
            entry = service_totals_by_mechanic.setdefault(mechanic_id, {
                "mechanic_name": svc.get("mechanic_name") or sale.get("mechanic_name") or "-",
                "commission_rate": _num(svc.get("commission_rate") if svc.get("commission_rate") is not None else sale.get("commission_rate")),
                "applies_quota_topup": _bool_flag(
                    svc.get("applies_quota_topup"),
                    default=_bool_flag(sale.get("applies_quota_topup"), default=True),
                ),
                "eligible_services_total": 0.0,
                "shop_only_services_total": 0.0,
            })
            if _bool_flag(svc.get("mechanic_payout_exempt"), default=False):
                entry["shop_only_services_total"] += _num(svc["price"])
            else:
                entry["eligible_services_total"] += _num(svc["price"])

        bundle_owner_mechanic_id = int(sale["mechanic_id"]) if sale.get("mechanic_id") is not None else None
        bundle_shop_share = round(
            sum(_num(bundle.get("shop_share_snapshot")) for bundle in bundles_by_sale.get(sale_id, [])),
            2,
        )
        bundle_mech_share = round(
            sum(_num(bundle.get("mechanic_share_snapshot")) for bundle in bundles_by_sale.get(sale_id, [])),
            2,
        )

        if sale["status"] == "Paid":
            for mechanic_id, mechanic_data in service_totals_by_mechanic.items():
                if mechanic_id not in mechanic_map:
                    mechanic_map[mechanic_id] = {
                        "mechanic_name": mechanic_data["mechanic_name"],
                        "commission_rate": mechanic_data["commission_rate"],
                        "applies_quota_topup": mechanic_data["applies_quota_topup"],
                        "paid_services_total": 0.0,
                        "shop_only_services_total": 0.0,
                        "bundle_shop_share_total": 0.0,
                        "bundle_mech_share_total": 0.0,
                    }
                mechanic_map[mechanic_id]["paid_services_total"] += round(_num(mechanic_data["eligible_services_total"]), 2)
                mechanic_map[mechanic_id]["shop_only_services_total"] += round(_num(mechanic_data["shop_only_services_total"]), 2)

            if bundle_owner_mechanic_id and (bundle_mech_share > 0 or bundle_shop_share > 0):
                if bundle_owner_mechanic_id not in mechanic_map:
                    mechanic_map[bundle_owner_mechanic_id] = {
                        "mechanic_name": sale["mechanic_name"] or "-",
                        "commission_rate": _num(sale["commission_rate"]),
                        "applies_quota_topup": _bool_flag(sale.get("applies_quota_topup"), default=True),
                        "paid_services_total": 0.0,
                        "shop_only_services_total": 0.0,
                        "bundle_shop_share_total": 0.0,
                        "bundle_mech_share_total": 0.0,
                    }
                mechanic_map[bundle_owner_mechanic_id]["bundle_shop_share_total"] += bundle_shop_share
                mechanic_map[bundle_owner_mechanic_id]["bundle_mech_share_total"] += bundle_mech_share

    for row in debt_collected_rows:
        mech_id         = row["mechanic_id"]
        service_portion = round(_num(row["service_portion"]), 2)
        if mech_id and service_portion > 0:
            if mech_id not in debt_mechanic_map:
                debt_mechanic_map[mech_id] = {
                    "mechanic_name":      row["mechanic_name"] or "-",
                    "commission_rate":    _num(row["commission_rate"]),
                    "applies_quota_topup": _bool_flag(row.get("applies_quota_topup"), default=True),
                    "debt_service_total": 0.0,
                    "shop_only_debt_service_total": 0.0,
                }
            debt_mechanic_map[mech_id]["debt_service_total"] += service_portion

        shop_only_service_portion = round(_num(row.get("shop_only_service_portion")), 2)
        if mech_id and shop_only_service_portion > 0:
            if mech_id not in debt_mechanic_map:
                debt_mechanic_map[mech_id] = {
                    "mechanic_name":      row["mechanic_name"] or "-",
                    "commission_rate":    _num(row["commission_rate"]),
                    "applies_quota_topup": _bool_flag(row.get("applies_quota_topup"), default=True),
                    "debt_service_total": 0.0,
                    "shop_only_debt_service_total": 0.0,
                }
            debt_mechanic_map[mech_id]["shop_only_debt_service_total"] += shop_only_service_portion

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
        applies_quota_topup = _bool_flag(
            regular.get("applies_quota_topup"),
            default=_bool_flag(debt.get("applies_quota_topup"), default=True),
        )

        paid_services        = round(_num(regular.get("paid_services_total", 0.0)), 2)
        shop_only_paid_services = round(_num(regular.get("shop_only_services_total", 0.0)), 2)
        bundle_shop_share    = round(_num(regular.get("bundle_shop_share_total", 0.0)), 2)
        bundle_mech_share    = round(_num(regular.get("bundle_mech_share_total", 0.0)), 2)
        debt_service_portion = round(_num(debt.get("debt_service_total", 0.0)), 2)
        shop_only_debt_services = round(_num(debt.get("shop_only_debt_service_total", 0.0)), 2)

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

        if applies_quota_topup and payout_base_total > 0 and combined_services <= MECHANIC_QUOTA:
            shop_topup = max(0.0, round(MECHANIC_PAYOUT_CAP - total_mech_cut_this, 2))
        else:
            shop_topup = 0.0

        payout_shop_share = round(
            (payout_base_total - regular_mech_cut) + debt_shop_share + shop_only_paid_services + shop_only_debt_services,
            2,
        )
        total_shop_share = round(payout_shop_share + bundle_shop_share, 2)
        total_payout     = round(total_mech_cut_this + shop_topup, 2)

        total_mech_cut        += total_mech_cut_this
        total_shop_topup      += shop_topup
        total_shop_commission += total_shop_share
        total_mech_cut_from_paid  += regular_mech_cut
        total_shop_comm_from_paid += regular_shop_share + bundle_shop_share + shop_only_paid_services
        total_mech_cut_from_debt  += debt_mech_cut

        mechanic_summary.append({
            "mechanic_id":           mech_id,
            "mechanic_name":         mechanic_name,
            "commission_rate":       commission_rate,
            "applies_quota_topup":   1 if applies_quota_topup else 0,
            "paid_services_total":   paid_services,
            "shop_only_services_total": round(shop_only_paid_services + shop_only_debt_services, 2),
            "bundle_shop_share":     bundle_shop_share,
            "bundle_mech_share":     bundle_mech_share,
            "payout_base_total":     payout_base_total,
            "regular_mech_cut":      regular_mech_cut,
            "payout_shop_share":     payout_shop_share,
            "shop_topup":            shop_topup,
            "debt_service_portion":  debt_service_portion,
            "shop_only_debt_service_portion": shop_only_debt_services,
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

def _aggregate_mechanic_summary_rows(summary_rows):
    grouped = {}
    numeric_fields = [
        "paid_services_total",
        "shop_only_services_total",
        "bundle_shop_share",
        "bundle_mech_share",
        "payout_base_total",
        "regular_mech_cut",
        "payout_shop_share",
        "shop_topup",
        "debt_service_portion",
        "shop_only_debt_service_portion",
        "debt_mech_cut",
        "services_total",
        "mechanic_cut",
        "shop_commission_share",
        "total_payout",
    ]

    for row in summary_rows:
        mech_id = row["mechanic_id"]
        if mech_id not in grouped:
            grouped[mech_id] = dict(row)
            continue

        aggregate = grouped[mech_id]
        for field in numeric_fields:
            aggregate[field] = round(_num(aggregate.get(field)) + _num(row.get(field)), 2)
        aggregate["applies_quota_topup"] = 1 if (
            _bool_flag(aggregate.get("applies_quota_topup"), default=True)
            or _bool_flag(row.get("applies_quota_topup"), default=True)
        ) else 0

    return sorted(grouped.values(), key=lambda x: x["mechanic_name"])


def _sum_mechanic_totals(total_rows):
    totals = {
        "total_mech_cut": 0.0,
        "total_shop_topup": 0.0,
        "total_shop_commission": 0.0,
        "total_mech_cut_from_paid": 0.0,
        "total_shop_comm_from_paid": 0.0,
        "total_mech_cut_from_debt": 0.0,
    }
    for row in total_rows:
        for key in totals:
            totals[key] = round(totals[key] + _num(row.get(key)), 2)
    return totals


def _calculate_range_mechanic_rollups(sales_rows, debt_collected_rows, services_by_sale, bundles_by_sale):
    sales_by_day = {}
    debt_by_day = {}

    for row in sales_rows:
        sale_day = str(row["transaction_date"])[:10]
        sales_by_day.setdefault(sale_day, []).append(row)

    for row in debt_collected_rows:
        paid_day = str(row["paid_at"])[:10]
        debt_by_day.setdefault(paid_day, []).append(row)

    all_days = sorted(set(sales_by_day.keys()) | set(debt_by_day.keys()))
    daily_summary_rows = []
    daily_totals = []
    quota_failures = []

    for day in all_days:
        day_mechanic_summary, day_totals = _calculate_mechanic_payouts(
            *_build_mechanic_maps(
                sales_by_day.get(day, []),
                debt_by_day.get(day, []),
                services_by_sale,
                bundles_by_sale,
            )
        )
        daily_summary_rows.extend(day_mechanic_summary)
        daily_totals.append(day_totals)

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

    return (
        _aggregate_mechanic_summary_rows(daily_summary_rows),
        _sum_mechanic_totals(daily_totals),
        quota_failures,
    )


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
    sales_rows = conn.execute("""
        SELECT
            DATE(s.transaction_date) AS payout_date,
            s.id,
            s.status,
            m.id                     AS mechanic_id,
            m.name                   AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup
        FROM sales s
        LEFT JOIN mechanics m ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
            ON mqto.mechanic_id = s.mechanic_id
           AND mqto.quota_date = DATE(s.transaction_date)
        WHERE DATE(s.transaction_date) = ANY(%s::date[])
          AND s.mechanic_id IS NOT NULL
    """, (normalized_dates,)).fetchall()

    debt_collected_rows = _get_debt_payout_allocations(conn, report_dates=normalized_dates)

    if not sales_rows and not debt_collected_rows:
        conn.close()
        return {day: [] for day in normalized_dates}

    sale_ids = [row["id"] for row in sales_rows]
    services_by_sale = {}
    bundles_by_sale = {}
    if sale_ids:
        services_by_sale = _load_services_by_sale(conn, sale_ids)
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
            COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.name  AS mechanic_name,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.status IN ('Unresolved', 'Partial')
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        GROUP BY s.id, m.name
        ORDER BY s.transaction_date ASC
    """).fetchall()

    if not unresolved_rows:
        return []

    sale_ids     = [row["id"] for row in unresolved_rows]
    payment_method_map = _build_sale_payment_summary_map(conn, sale_ids)
    items_rows = conn.execute("""
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
        WHERE si.sale_id = ANY(%s)
        ORDER BY si.sale_id, i.name
    """, (sale_ids,)).fetchall()

    items_by_sale    = {}
    services_by_sale = _load_services_by_sale(conn, sale_ids)
    bundles_by_sale = _load_bundles_by_sale(conn, sale_ids)
    for row in items_rows:
        items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    result = []
    for sale in unresolved_rows:
        sale_id    = sale["id"]
        total_amount = _num(sale["total_amount"])
        total_paid = round(_num(sale["total_paid"]), 2)
        remaining  = round(total_amount - total_paid, 2)
        sale_services = services_by_sale.get(sale_id, [])
        sale_bundles = bundles_by_sale.get(sale_id, [])
        result.append({
            "sales_number":     sale["sales_number"] or f"#{sale_id}",
            "customer_name":    sale["customer_name"] or "Walk-in",
            "mechanic_name":    _compose_sale_mechanic_label(sale, sale_services, sale_bundles),
            "total_amount":     round(total_amount, 2),
            "total_paid":       total_paid,
            "remaining":        remaining,
            "status":           sale["status"],
            "payment_method":   payment_method_map.get(sale_id, "—"),
            "notes":            sale["notes"] or "",
            "transaction_date": format_date(sale["transaction_date"]),
            "products":         items_by_sale.get(sale_id, []),
            "services":         sale_services,
            "bundles":          sale_bundles,
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
            COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.id              AS mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup,
            se.exchange_number,
            se.original_sale_id
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
            ON mqto.mechanic_id = s.mechanic_id
           AND mqto.quota_date = DATE(s.transaction_date)
        LEFT JOIN sale_exchanges se  ON se.replacement_sale_id = s.id
        WHERE DATE(s.transaction_date) = %s
        ORDER BY s.transaction_date ASC
    """, (report_date,)).fetchall()

    all_unresolved = get_all_unresolved_sales(conn)
    sale_payment_map = _build_sale_payment_summary_map(conn, [row["id"] for row in sales_rows])

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
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup,
            pm.name           AS payment_method
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
            ON mqto.mechanic_id = s.mechanic_id
           AND mqto.quota_date = DATE(dp.paid_at)
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE DATE(dp.paid_at) = %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ORDER BY dp.paid_at ASC
    """, (report_date,)).fetchall()
    debt_payout_rows = _get_debt_payout_allocations(conn, report_date=report_date)

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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
        items_rows = conn.execute("""
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
            WHERE si.sale_id = ANY(%s)
            ORDER BY si.sale_id, i.name
        """, (paid_sale_ids,)).fetchall()
        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    if all_sale_ids:
        services_by_sale = _load_services_by_sale(conn, all_sale_ids)
        bundles_by_sale = _load_bundles_by_sale(conn, all_sale_ids)

    refund_ids = [row["id"] for row in refund_rows]
    if refund_ids:
        refund_item_rows = conn.execute("""
            SELECT
                sri.refund_id,
                i.name AS item_name,
                sri.quantity,
                sri.unit_price,
                sri.line_total
            FROM sale_refund_items sri
            JOIN items i ON i.id = sri.item_id
            WHERE sri.refund_id = ANY(%s)
            ORDER BY sri.refund_id ASC, i.name ASC, sri.id ASC
        """, (refund_ids,)).fetchall()
        for row in refund_item_rows:
            refund_items_by_id.setdefault(row["refund_id"], []).append({
                "item_name": row["item_name"],
                "quantity": int(row["quantity"] or 0),
                "unit_price": round(_num(row["unit_price"]), 2),
                "line_total": round(_num(row["line_total"]), 2),
            })

    non_cash_metrics = _get_non_cash_floating_metrics(conn, report_date, report_date)
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
    financial_paid_sales = []
    total_gross = 0.0
    total_service_revenue = 0.0
    total_product_cost = 0.0
    total_product_profit = 0.0

    for sale in sales_rows:
        sale_id = sale["id"]
        transaction_class = sale.get("transaction_class") or "NEW_SALE"
        is_mechanic_supply = transaction_class == "MECHANIC_SUPPLY"
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
                sum(_num(bundle.get("included_items_cost_total")) for bundle in sale_bundles),
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
            sale_payload = {
                "sales_number": sale["sales_number"] or f"#{sale_id}",
                "customer_name": sale["customer_name"] or ("-" if is_mechanic_supply else "Walk-in"),
                "mechanic_name": _compose_sale_mechanic_label(sale, services_by_sale.get(sale_id, []), sale_bundles),
                "services_total": services_total,
                "standalone_services_total": round(standalone_services_total, 2),
                "bundle_service_total": bundle_service_total,
                "bundle_shop_total": bundle_shop_total,
                "service_revenue_total": service_revenue_total,
                "product_cost_total": sale_product_cost,
                "product_profit_total": sale_product_profit,
                "total_amount": round(total_amount, 2),
                "status": sale["status"],
                "payment_method": sale_payment_map.get(sale["id"], "-"),
                "notes": sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products": sale_products,
                "services": services_by_sale.get(sale_id, []),
                "bundles": sale_bundles,
                "report_label": "Mechanic Supply" if is_mechanic_supply else ("Exchange/Replacement" if sale["exchange_number"] else "Sale"),
                "exchange_number": sale["exchange_number"] or "",
                "transaction_class": transaction_class,
                "exclude_from_calculations": 1 if is_mechanic_supply else 0,
            }
            paid_sales.append(sale_payload)
            if not is_mechanic_supply:
                financial_paid_sales.append(sale_payload)
                total_service_revenue += service_revenue_total
                total_gross += total_amount
                total_product_cost += sale_product_cost
                total_product_profit += sale_product_profit

    mechanic_map, debt_mechanic_map = _build_mechanic_maps(
        [sale for sale in sales_rows if (sale.get("transaction_class") or "NEW_SALE") != "MECHANIC_SUPPLY"],
        debt_payout_rows,
        services_by_sale,
        bundles_by_sale,
    )
    mechanic_summary, totals = _calculate_mechanic_payouts(mechanic_map, debt_mechanic_map)
    total_bundle_shop_share = round(
        sum(_num(sale.get("bundle_shop_total")) for sale in financial_paid_sales),
        2,
    )
    total_shop_comm_from_paid = round(
        total_service_revenue - totals["total_mech_cut_from_paid"] - total_bundle_shop_share,
        2,
    )

    paid_sales = sorted(
        paid_sales,
        key=lambda sale: 1 if sale.get("transaction_class") == "MECHANIC_SUPPLY" else 0,
    )
    items_summary = _summarize_items_for_profit(financial_paid_sales)
    mechanic_supply_sales = [
        sale for sale in paid_sales if sale.get("transaction_class") == "MECHANIC_SUPPLY"
    ]
    mechanic_supply_items_summary = _summarize_mechanic_supply_items(mechanic_supply_sales)
    total_profit_with_shop_share = round(
        total_product_profit + totals["total_shop_commission"] - totals["total_shop_topup"],
        2,
    )

    return {
        "sales": paid_sales,
        "mechanic_supply_sales": mechanic_supply_sales,
        "mechanic_supply_items_summary": mechanic_supply_items_summary,
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
        "total_profit_with_shop_share": total_profit_with_shop_share,
        "total_non_cash_sales": non_cash_metrics["total_non_cash_sales"],
        "total_non_cash_claimed": non_cash_metrics["total_non_cash_claimed"],
        "total_non_cash_floating": non_cash_metrics["total_non_cash_floating"],
        "debt_collected": debt_collected,
        "total_debt_collected": total_debt_collected,
        "total_mech_cut_from_paid": totals["total_mech_cut_from_paid"],
        "total_bundle_shop_share": total_bundle_shop_share,
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
            COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            m.id              AS mechanic_id,
            m.name            AS mechanic_name,
            m.commission_rate,
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup,
            se.exchange_number,
            se.original_sale_id
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
            ON mqto.mechanic_id = s.mechanic_id
           AND mqto.quota_date = DATE(s.transaction_date)
        LEFT JOIN sale_exchanges se  ON se.replacement_sale_id = s.id
        WHERE DATE(s.transaction_date) BETWEEN %s AND %s
        ORDER BY s.transaction_date ASC
    """, (start_date, end_date)).fetchall()

    all_unresolved = get_all_unresolved_sales(conn)
    sale_payment_map = _build_sale_payment_summary_map(conn, [row["id"] for row in sales_rows])

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
            COALESCE(mqto.applies_quota_topup, 1) AS applies_quota_topup,
            pm.name           AS payment_method
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN mechanic_quota_topup_overrides mqto
            ON mqto.mechanic_id = s.mechanic_id
           AND mqto.quota_date = DATE(dp.paid_at)
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE DATE(dp.paid_at) BETWEEN %s AND %s
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
        ORDER BY dp.paid_at ASC
    """, (start_date, end_date)).fetchall()
    debt_payout_rows = _get_debt_payout_allocations(conn, start_date=start_date, end_date=end_date)

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
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
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
        items_rows = conn.execute("""
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
            WHERE si.sale_id = ANY(%s)
            ORDER BY si.sale_id, i.name
        """, (paid_sale_ids,)).fetchall()
        for row in items_rows:
            items_by_sale.setdefault(row["sale_id"], []).append(dict(row))

    if all_sale_ids:
        services_by_sale = _load_services_by_sale(conn, all_sale_ids)
        bundles_by_sale = _load_bundles_by_sale(conn, all_sale_ids)

    refund_ids = [row["id"] for row in refund_rows]
    if refund_ids:
        refund_item_rows = conn.execute("""
            SELECT
                sri.refund_id,
                i.name AS item_name,
                sri.quantity,
                sri.unit_price,
                sri.line_total
            FROM sale_refund_items sri
            JOIN items i ON i.id = sri.item_id
            WHERE sri.refund_id = ANY(%s)
            ORDER BY sri.refund_id ASC, i.name ASC, sri.id ASC
        """, (refund_ids,)).fetchall()
        for row in refund_item_rows:
            refund_items_by_id.setdefault(row["refund_id"], []).append({
                "item_name": row["item_name"],
                "quantity": int(row["quantity"] or 0),
                "unit_price": round(_num(row["unit_price"]), 2),
                "line_total": round(_num(row["line_total"]), 2),
            })

    non_cash_metrics = _get_non_cash_floating_metrics(conn, start_date, end_date)
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
    financial_paid_sales = []
    total_gross = 0.0
    total_service_revenue = 0.0
    total_product_cost = 0.0
    total_product_profit = 0.0

    for sale in sales_rows:
        sale_id = sale["id"]
        transaction_class = sale.get("transaction_class") or "NEW_SALE"
        is_mechanic_supply = transaction_class == "MECHANIC_SUPPLY"
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
                sum(_num(bundle.get("included_items_cost_total")) for bundle in sale_bundles),
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
            sale_payload = {
                "sales_number": sale["sales_number"] or f"#{sale_id}",
                "customer_name": sale["customer_name"] or ("-" if is_mechanic_supply else "Walk-in"),
                "mechanic_name": _compose_sale_mechanic_label(sale, services_by_sale.get(sale_id, []), sale_bundles),
                "services_total": services_total,
                "standalone_services_total": round(standalone_services_total, 2),
                "bundle_service_total": bundle_service_total,
                "bundle_shop_total": bundle_shop_total,
                "service_revenue_total": service_revenue_total,
                "product_cost_total": sale_product_cost,
                "product_profit_total": sale_product_profit,
                "total_amount": round(total_amount, 2),
                "status": sale["status"],
                "payment_method": sale_payment_map.get(sale["id"], "-"),
                "notes": sale["notes"] or "",
                "transaction_date": format_date(sale["transaction_date"]),
                "products": sale_products,
                "services": services_by_sale.get(sale_id, []),
                "bundles": sale_bundles,
                "report_label": "Mechanic Supply" if is_mechanic_supply else ("Exchange/Replacement" if sale["exchange_number"] else "Sale"),
                "exchange_number": sale["exchange_number"] or "",
                "transaction_class": transaction_class,
                "exclude_from_calculations": 1 if is_mechanic_supply else 0,
            }
            paid_sales.append(sale_payload)
            if not is_mechanic_supply:
                financial_paid_sales.append(sale_payload)
                total_service_revenue += service_revenue_total
                total_gross += total_amount
                total_product_cost += sale_product_cost
                total_product_profit += sale_product_profit

    mechanic_summary, totals, quota_failures = _calculate_range_mechanic_rollups(
        [sale for sale in sales_rows if (sale.get("transaction_class") or "NEW_SALE") != "MECHANIC_SUPPLY"],
        debt_payout_rows,
        services_by_sale,
        bundles_by_sale,
    )
    total_bundle_shop_share = round(
        sum(_num(sale.get("bundle_shop_total")) for sale in financial_paid_sales),
        2,
    )
    total_shop_comm_from_paid = round(
        total_service_revenue - totals["total_mech_cut_from_paid"] - total_bundle_shop_share,
        2,
    )

    paid_sales = sorted(
        paid_sales,
        key=lambda sale: 1 if sale.get("transaction_class") == "MECHANIC_SUPPLY" else 0,
    )
    items_summary = _summarize_items_for_profit(financial_paid_sales)
    mechanic_supply_sales = [
        sale for sale in paid_sales if sale.get("transaction_class") == "MECHANIC_SUPPLY"
    ]
    mechanic_supply_items_summary = _summarize_mechanic_supply_items(mechanic_supply_sales)
    total_profit_with_shop_share = round(
        total_product_profit + totals["total_shop_commission"] - totals["total_shop_topup"],
        2,
    )

    return {
        "sales": paid_sales,
        "mechanic_supply_sales": mechanic_supply_sales,
        "mechanic_supply_items_summary": mechanic_supply_items_summary,
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
        "total_profit_with_shop_share": total_profit_with_shop_share,
        "total_non_cash_sales": non_cash_metrics["total_non_cash_sales"],
        "total_non_cash_claimed": non_cash_metrics["total_non_cash_claimed"],
        "total_non_cash_floating": non_cash_metrics["total_non_cash_floating"],
        "debt_collected": debt_collected,
        "total_debt_collected": total_debt_collected,
        "total_mech_cut_from_paid": totals["total_mech_cut_from_paid"],
        "total_bundle_shop_share": total_bundle_shop_share,
        "total_shop_comm_from_paid": total_shop_comm_from_paid,
        "total_mech_cut_from_debt": totals["total_mech_cut_from_debt"],
        "refunds": refunds,
        "total_refunds": total_refunds,
        "quota_failures": sorted(
            quota_failures,
            key=lambda row: (row["date"], row["mechanic_name"]),
        ),
    }
