# Add to imports at the top
import csv
import io
from datetime import datetime, date
from flask import Response
from db.database import get_db
from flask import Blueprint, request, render_template, redirect, url_for, flash, session
from auth.utils import login_required, admin_required
from services.loyalty_service import get_all_programs
from services.reports_service import (
    get_sales_by_date,
    get_sales_by_range,
    get_sales_report_by_date,
    get_sales_report_by_range,
    _build_mechanic_supply_report_context,
)
from services.transactions_service import get_purchase_order_export_data, get_sale_refund_context
from services.cash_service import get_cash_entries_for_report
from services.inventory_service import attach_restock_recommendation
from utils.formatters import format_date

reports_bp = Blueprint("reports", __name__)


def _normalize_item_category(value):
    normalized = str(value or "").strip().upper()
    if normalized == "SVC":
        return "SVC"
    if normalized == "ACC":
        return "ACC"
    if normalized == "OIL":
        return "OIL"
    if normalized == "PMS":
        return "PMS"
    return normalized


def _normalize_requested_item_categories(values):
    normalized = []
    for value in values or []:
        category = _normalize_item_category(value)
        if category in {"OIL", "PMS", "ACC"} and category not in normalized:
            normalized.append(category)
    return normalized


def _get_items_export_rows(selected_categories=None):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                i.id,
                i.name,
                i.description,
                i.category,
                i.pack_size,
                i.vendor_price,
                i.cost_per_piece,
                i.a4s_selling_price,
                i.markup,
                i.reorder_level,
                COALESCE(v.vendor_name, i.vendor) AS vendor_name,
                COALESCE(inv.current_stock, 0) AS current_stock
            FROM items i
            LEFT JOIN vendors v ON v.id = i.vendor_id
            LEFT JOIN (
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
            ) AS inv ON inv.item_id = i.id
            ORDER BY i.name ASC
        """).fetchall()

        items = [dict(row) for row in rows]
        attach_restock_recommendation(
            conn,
            items,
            item_id_key="id",
            category_key="category",
            current_stock_key="current_stock",
        )

        items = [
            item
            for item in items
            if _normalize_item_category(item.get("category")) != "SVC"
        ]

        normalized_categories = _normalize_requested_item_categories(selected_categories)
        if normalized_categories:
            items = [
                item
                for item in items
                if _normalize_item_category(item.get("category")) in normalized_categories
            ]

        return items
    finally:
        conn.close()


def _parse_strict_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _get_validated_date_arg(param_name, *, flash_label):
    raw_value = (request.args.get(param_name) or "").strip()
    if not raw_value:
        return None

    parsed = _parse_strict_iso_date(raw_value)
    if parsed is None:
        flash(f"{flash_label} must be a valid date in YYYY-MM-DD format.", "warning")
        return None
    return parsed


def _loyalty_reward_label(program):
    reward_type = str(program.get("reward_type") or "").upper()
    reward_value = float(program.get("reward_value") or 0)
    mapping = {
        "NONE": "Earn-only campaign",
        "FREE_SERVICE": "Free Service",
        "FREE_ITEM": "Free Item",
        "DISCOUNT_PERCENT": f"{reward_value:g}% off",
        "DISCOUNT_AMOUNT": f"P{reward_value:,.2f} off",
        "RAFFLE_ENTRY": f"{reward_value:.0f} raffle {'entry' if int(reward_value or 1) == 1 else 'entries'}",
    }
    return mapping.get(reward_type, reward_type or "-")


def _loyalty_rule_condition_text(rule):
    conditions = []
    if int(rule.get("requires_any_service") or 0) == 1:
        conditions.append("Any service")
    if int(rule.get("requires_any_item") or 0) == 1:
        conditions.append("Any item")
    if rule.get("service_name"):
        conditions.append(f"Service: {rule['service_name']}")
    elif rule.get("service_id"):
        conditions.append(f"Service #{rule['service_id']}")
    if rule.get("item_name"):
        conditions.append(f"Item: {rule['item_name']}")
    elif rule.get("item_id"):
        conditions.append(f"Item #{rule['item_id']}")
    return " + ".join(conditions) if conditions else "Applies without extra conditions"


def _build_loyalty_program_report(program):
    rules = []
    for idx, raw_rule in enumerate(program.get("point_rules") or [], start=1):
        rule = dict(raw_rule)
        rule["display_name"] = rule.get("rule_name") or f"Rule {idx}"
        rule["condition_text"] = _loyalty_rule_condition_text(rule)
        rules.append(rule)

    is_expired = int(program.get("is_expired") or 0) == 1
    is_active = int(program.get("is_active") or 0) == 1
    program_mode = str(program.get("program_mode") or "REDEEMABLE").upper()
    reward_basis = str(program.get("reward_basis") or "STAMPS").replace("_", " ").title()

    reward_description = (program.get("reward_description") or "").strip()
    reward_label = _loyalty_reward_label(program)
    reward_display = reward_description or reward_label
    if reward_description and reward_label and reward_description != reward_label:
        reward_subtext = reward_label
    else:
        reward_subtext = ""

    return {
        **program,
        "display_type": "Service" if program.get("program_type") == "SERVICE" else "Item",
        "status_label": "Expired" if is_expired else ("Active" if is_active else "Inactive"),
        "qualifying_display": program.get("qualifying_name") or f"ID: {program.get('qualifying_id')}",
        "period_display": f"{format_date(program.get('period_start'))} to {format_date(program.get('period_end'))}",
        "reward_basis_display": reward_basis,
        "program_mode_display": "Earn Only" if program_mode == "EARN_ONLY" else "Redeemable",
        "reward_display": reward_display,
        "reward_subtext": reward_subtext,
        "rules": rules,
        "rule_count": len(rules),
        "stamp_enabled_bool": int(program.get("stamp_enabled") or 0) == 1,
        "points_enabled_bool": int(program.get("points_enabled") or 0) == 1,
        "threshold_display": int(program.get("threshold") or 0),
        "points_threshold_display": int(program.get("points_threshold") or 0),
    }


def _build_sales_report_context():
    report_date = _get_validated_date_arg("report_date", flash_label="Report date")
    if request.args.get("report_date") and report_date is None:
        return None

    start_date = _get_validated_date_arg("start_date", flash_label="Start date")
    if request.args.get("start_date") and start_date is None:
        return None

    end_date = _get_validated_date_arg("end_date", flash_label="End date")
    if request.args.get("end_date") and end_date is None:
        return None

    if report_date:
        report_date_iso = report_date.isoformat()
        data = get_sales_report_by_date(report_date_iso)
        date_label = format_date(report_date_iso)
        is_range = False
        cash_data = get_cash_entries_for_report(report_date_iso, report_date_iso)
    elif start_date and end_date:
        if end_date < start_date:
            flash("End date cannot be before start date.", "warning")
            return None
        start_date_iso = start_date.isoformat()
        end_date_iso = end_date.isoformat()
        data = get_sales_report_by_range(start_date_iso, end_date_iso)
        date_label = f"{format_date(start_date_iso)} to {format_date(end_date_iso)}"
        is_range = True
        cash_data = get_cash_entries_for_report(start_date_iso, end_date_iso)
    else:
        flash("Please select a date.", "warning")
        return None

    if not data:
        data = {
            "sales": [],
            "unresolved": [],
            "mechanic_summary": [],
            "quota_failures": [],
            "items_summary": [],
            "total_gross": 0.0,
            "total_mech_cut": 0.0,
            "total_shop_topup": 0.0,
            "net_revenue": 0.0,
            "total_product_revenue": 0.0,
            "total_product_cost": 0.0,
            "total_product_profit": 0.0,
            "total_shop_commission": 0.0,
            "total_non_cash_sales": 0.0,
            "total_non_cash_claimed": 0.0,
            "total_non_cash_floating": 0.0,
            "debt_collected": [],
            "total_debt_collected": 0.0,
            "refunds": [],
            "total_refunds": 0.0,
            "total_service_revenue": 0.0,
            "total_shop_comm_from_paid": 0.0,
            "total_bundle_shop_share": 0.0,
            "total_mech_cut_from_paid": 0.0,
            "total_mech_cut_from_debt": 0.0,
        }

    return {
        "report_date": date_label,
        "data": data,
        "is_range": is_range,
        "cash_data": cash_data,
    }


@reports_bp.route("/reports/sales-receipt/<int:sale_id>")
@login_required
def sales_receipt_report(sale_id):
    try:
        data = get_sale_refund_context(sale_id)
    except ValueError:
        return "Sale not found.", 404
    except Exception as exc:
        return f"Unable to load sale receipt: {exc}", 500

    if not data:
        return "Sale not found.", 404

    return render_template(
        "reports/sales_receipt_pdf.html",
        sale=data,
        generated_at=format_date(datetime.now(), show_time=True),
    )


@reports_bp.route("/reports/purchase-order/<int:po_id>")
@login_required
def purchase_order_report(po_id):
    po, items = get_purchase_order_export_data(po_id)
    if not po:
        return "Purchase order not found.", 404

    po_data = dict(po)
    report_data = {
        "id": po_data.get("id"),
        "po_number": po_data.get("po_number") or "-",
        "vendor_name": po_data.get("vendor_name") or "-",
        "vendor_address": po_data.get("vendor_address") or "-",
        "vendor_contact_person": po_data.get("vendor_contact_person") or "-",
        "vendor_contact_no": po_data.get("vendor_contact_no") or "-",
        "status": po_data.get("display_status") or (po_data.get("status") or "PENDING"),
        "created_at": format_date(po_data.get("created_at"), show_time=True),
        "received_at": format_date(po_data.get("received_at"), show_time=True),
        "total_amount": float(po_data.get("total_amount") or 0),
        "receipt_history": [],
        "items": [],
    }

    for receipt in po_data.get("receipt_history") or []:
        report_data["receipt_history"].append({
            **receipt,
            "received_at": format_date(receipt.get("received_at"), show_time=True),
        })

    for idx, row in enumerate(items, start=1):
        item = dict(row)
        qty_ordered = int(item.get("quantity_ordered") or 0)
        unit_cost = float(item.get("unit_cost") or 0)
        report_data["items"].append({
            "item_no": idx,
            "name": item.get("name") or "",
            "quantity_ordered": qty_ordered,
            "quantity_received": int(item.get("quantity_received") or 0),
            "unit_cost": unit_cost,
            "purchase_mode": item.get("purchase_mode") or "PIECE",
            "subtotal": qty_ordered * unit_cost,
        })

    rows_per_page = 18
    all_items = report_data["items"]
    if all_items:
        report_data["item_pages"] = [
            all_items[i:i + rows_per_page]
            for i in range(0, len(all_items), rows_per_page)
        ]
    else:
        report_data["item_pages"] = [[]]

    return render_template("reports/purchase_order_pdf.html", po=report_data)


@reports_bp.route("/reports/loyalty-program/<int:program_id>")
@login_required
def loyalty_program_report(program_id):
    programs = get_all_programs(include_rules=True)
    program = next((row for row in programs if int(row.get("id") or 0) == program_id), None)
    if not program:
        return "Loyalty program not found.", 404

    return render_template(
        "reports/loyalty_info_pdf.html",
        program=_build_loyalty_program_report(program),
        generated_at=datetime.now().strftime("%b %d, %Y %I:%M %p"),
    )


@reports_bp.route("/reports/daily")
@login_required
def daily_report():
    report_date = _get_validated_date_arg("date", flash_label="Report date")
    if request.args.get("date") and report_date is None:
        return redirect(url_for("index"))

    if not report_date:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))
    return redirect(url_for("reports.sales_summary_report", report_date=report_date.isoformat()))


@reports_bp.route("/reports/range")
@login_required
def range_report():
    start = _get_validated_date_arg("start", flash_label="Start date")
    if request.args.get("start") and start is None:
        return redirect(url_for("index"))

    end = _get_validated_date_arg("end", flash_label="End date")
    if request.args.get("end") and end is None:
        return redirect(url_for("index"))

    if not start or not end:
        flash("Please select a date range.", "warning")
        return redirect(url_for("index"))
    return redirect(url_for("reports.sales_summary_report", start_date=start.isoformat(), end_date=end.isoformat()))


@reports_bp.route("/reports/sales-summary")
@login_required
def sales_summary_report():
    context = _build_sales_report_context()
    if context is None:
        return redirect(url_for("index"))
    return render_template("reports/sales_report_pdf.html", **context)


@reports_bp.route("/reports/sales-report-summary")
@login_required
def sales_report_summary_pdf():
    context = _build_sales_report_context()
    if context is None:
        return redirect(url_for("index"))
    return render_template("reports/sales_summary_pdf.html", **context)


@reports_bp.route("/reports/mechanic-supply")
@login_required
def mechanic_supply_report():
    report_date = _get_validated_date_arg("report_date", flash_label="Report date")
    if request.args.get("report_date") and report_date is None:
        return redirect(url_for("index"))

    start_date = _get_validated_date_arg("start_date", flash_label="Start date")
    if request.args.get("start_date") and start_date is None:
        return redirect(url_for("index"))

    end_date = _get_validated_date_arg("end_date", flash_label="End date")
    if request.args.get("end_date") and end_date is None:
        return redirect(url_for("index"))

    if report_date:
        report_date_iso = report_date.isoformat()
        data = _build_mechanic_supply_report_context(report_date_iso, report_date_iso)
        date_label = format_date(report_date_iso)
    elif start_date and end_date:
        if end_date < start_date:
            flash("End date cannot be before start date.", "warning")
            return redirect(url_for("index"))
        start_date_iso = start_date.isoformat()
        end_date_iso = end_date.isoformat()
        data = _build_mechanic_supply_report_context(start_date_iso, end_date_iso)
        date_label = f"{format_date(start_date_iso)} to {format_date(end_date_iso)}"
    else:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))

    return render_template(
        "reports/mechanic_supply_pdf.html",
        report_date=date_label,
        data=data,
    )


@reports_bp.route("/reports/items-overall")
@admin_required
def items_overall_report():
    selected_categories = _normalize_requested_item_categories(request.args.getlist("category"))
    items = _get_items_export_rows(selected_categories)
    total_stock = sum(int(item.get("current_stock") or 0) for item in items)
    low_stock_count = sum(1 for item in items if item.get("should_restock"))
    total_inventory_cost = round(
        sum(
            float(item.get("cost_per_piece") or 0) * float(item.get("current_stock") or 0)
            for item in items
        ),
        2,
    )
    potential_inventory_income = round(
        sum(
            float(item.get("a4s_selling_price") or 0) * float(item.get("current_stock") or 0)
            for item in items
        ),
        2,
    )
    category_labels = {
        "OIL": "Oil",
        "PMS": "Pms",
        "ACC": "Acc",
    }
    selected_category_labels = [category_labels[category] for category in selected_categories]

    return render_template(
        "reports/items_overall_report.html",
        report={
            "items": items,
            "generated_at": datetime.now().strftime("%b %d, %Y %I:%M %p"),
            "total_items": len(items),
            "total_stock": total_stock,
            "total_inventory_cost": total_inventory_cost,
            "potential_inventory_income": potential_inventory_income,
            "low_stock_count": low_stock_count,
            "selected_categories": selected_category_labels,
        },
    )


@reports_bp.route("/export/inventory-snapshot")
@login_required
def export_inventory_snapshot():
    """
    Exports all items with current stock, total units sold all-time, selling price, and total revenue.
    Used for BIR audit purposes.

    Future scalability note: add ?branch_id= param here when multi-branch is ready.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT
            i.id,
            i.name,
            i.category,
            i.a4s_selling_price,
            COALESCE(inv.current_stock, 0) AS current_stock,
            COALESCE(inv.total_sold, 0) AS total_sold,
            COALESCE(sale_totals.total_revenue, 0) AS total_revenue
        FROM items i
        LEFT JOIN (
            SELECT
                item_id,
                SUM(
                    CASE WHEN transaction_type = 'IN'  THEN quantity
                         WHEN transaction_type = 'OUT' THEN -quantity
                         ELSE 0 END
                ) AS current_stock,
                SUM(
                    CASE WHEN transaction_type = 'OUT' THEN quantity ELSE 0 END
                ) AS total_sold
            FROM inventory_transactions
            GROUP BY item_id
        ) AS inv ON i.id = inv.item_id
        LEFT JOIN (
            SELECT
                item_id,
                SUM(COALESCE(final_unit_price, 0) * quantity) AS total_revenue
            FROM sales_items
            GROUP BY item_id
        ) AS sale_totals ON i.id = sale_totals.item_id
        ORDER BY i.name ASC
    """).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Item ID", "Item Name", "Category",
        "Selling Price (A4S)", "Current Stock", "Total Units Sold (All-Time)", "Revenue"
    ])

    for row in rows:
        writer.writerow([
            row["id"],
            row["name"],
            row["category"] or "",
            row["a4s_selling_price"] or 0,
            row["current_stock"],
            row["total_sold"],
            round(row["total_revenue"] or 0, 2),
        ])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"inventory_snapshot_{timestamp}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@reports_bp.route("/export/items")
@login_required
def export_items_csv():
    """
    Exports the current item catalog shown in the inventory page.
    Includes stored item fields plus computed current stock.
    """
    rows = _get_items_export_rows()

    output = io.StringIO()
    writer = csv.writer(output)
    is_admin = session.get("role") == "admin"

    headers = [
        "Item ID",
        "Name",
        "Description",
        "Category",
        "Pack Size",
        "Vendor Price",
        "Cost Per Piece",
        "Selling Price",
        "Reorder Level",
        "Current Stock",
        "Vendor",
    ]
    if is_admin:
        headers.insert(8, "Markup (%)")
    writer.writerow(headers)

    for row in rows:
        markup_value = row["markup"]
        markup_percent = round(float(markup_value or 0) * 100, 2)
        csv_row = [
            row["id"],
            row["name"] or "",
            row["description"] or "",
            row["category"] or "",
            row["pack_size"] or "",
            row["vendor_price"] or 0,
            row["cost_per_piece"] or 0,
            row["a4s_selling_price"] or 0,
            row["reorder_level"] or 0,
            row["current_stock"] or 0,
            row["vendor_name"] or "",
        ]
        if is_admin:
            csv_row.insert(8, markup_percent)
        writer.writerow(csv_row)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"items_export_{timestamp}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@reports_bp.route("/export/items-sold-today")
@login_required
def export_items_sold_today():
    today = date.today()
    today_iso = today.isoformat()
    today_display = today.strftime("%B %d, %Y").replace(" 0", " ")
    conn = get_db()
    sales_rows = conn.execute("""
        SELECT
            x.sale_id,
            x.sales_number,
            x.status,
            x.total_amount,
            x.service_total,
            x.total_paid,
            x.service_paid,
            x.payment_method_name
        FROM (
            SELECT
                s.id                AS sale_id,
                s.sales_number,
                s.status,
                COALESCE(s.total_amount, 0) AS total_amount,
                COALESCE((SELECT SUM(ss.price) FROM sales_services ss WHERE ss.sale_id = s.id), 0) AS service_total,
                COALESCE((SELECT SUM(dp.amount_paid) FROM debt_payments dp WHERE dp.sale_id = s.id), 0) AS total_paid,
                COALESCE((SELECT SUM(dp.service_portion) FROM debt_payments dp WHERE dp.sale_id = s.id), 0) AS service_paid,
                COALESCE(pm.name, 'N/A') AS payment_method_name
            FROM sales s
            LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
            WHERE DATE(s.transaction_date) = %s
        ) x
        WHERE
            x.status = 'Paid'
            OR (
                x.status = 'Partial'
                AND x.service_paid >= x.service_total
            )
    """, (today_iso,)).fetchall()

    sale_map = {
        row["sale_id"]: dict(row)
        for row in sales_rows
    }

    rows = []
    if sales_rows:
        sale_ids = [row["sale_id"] for row in sales_rows]
        rows = conn.execute("""
            SELECT
                si.sale_id,
                COALESCE(i.name, '') AS item_name,
                COALESCE(si.quantity, 0) AS quantity,
                COALESCE(si.final_unit_price, 0) AS final_unit_price
            FROM sales_items si
            LEFT JOIN items i ON i.id = si.item_id
            WHERE si.sale_id = ANY(%s)
            ORDER BY si.sale_id ASC
        """, (sale_ids,)).fetchall()
    conn.close()

    output = []
    output.append(f"Date,{today_display}")
    output.append("quantity,item,OR No,Payment Mod,amount")

    for row in rows:
        sale = sale_map.get(row["sale_id"], {})
        item = row["item_name"].replace('"', '""') if row["item_name"] else ""
        sales_number = sale.get("sales_number", "") or ""
        sales_number = sales_number.replace('"', '""')
        quantity = int(row["quantity"] or 0)
        final_unit_price = float(row["final_unit_price"] or 0)
        line_total = final_unit_price * quantity

        paid_amount = line_total
        if sale.get("status") == "Partial":
            item_total = float(sale.get("total_amount", 0) or 0) - float(sale.get("service_total", 0) or 0)
            item_paid = max(0.0, float(sale.get("total_paid", 0) or 0) - float(sale.get("service_paid", 0) or 0))
            if item_total > 0:
                ratio = min(1.0, item_paid / item_total)
                paid_amount = round(line_total * ratio, 2)
            else:
                paid_amount = 0.0

        paid_amount = round(paid_amount, 2)
        if paid_amount <= 0:
            continue

        payment_method = (sale.get("payment_method_name", "N/A") or "N/A").replace('"', '""')
        output.append(
            f'{quantity},"{item}","{sales_number}","{payment_method}",'
            f'{paid_amount:.2f}'
        )

    return Response(
        "\n".join(output) + "\n",
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=items_sold_{today_iso}.csv"},
    )


@reports_bp.route("/export/services-sold-today")
@login_required
def export_services_sold_today():
    today = date.today()
    today_iso = today.isoformat()

    conn = get_db()
    sale_rows = conn.execute("""
        SELECT
            x.sale_id,
            x.sales_number,
            x.customer_name,
            COALESCE(x.vehicle_name, '') AS vehicle_name,
            COALESCE(x.mechanic_name, 'N/A') AS mechanic_name,
            COALESCE(x.commission_rate, 0.0) AS commission_rate
        FROM (
            SELECT
                s.id                           AS sale_id,
                s.sales_number,
                COALESCE(c.customer_name, s.customer_name, 'Walk-in') AS customer_name,
                v.vehicle_name,
                m.name                         AS mechanic_name,
                m.commission_rate,
                COALESCE(ss.service_total, 0)  AS service_total,
                COALESCE(dp.service_paid, 0)   AS service_paid,
                s.status
            FROM sales s
            LEFT JOIN customers c ON c.id = s.customer_id
            LEFT JOIN vehicles v ON v.id = s.vehicle_id
            LEFT JOIN mechanics m ON m.id = s.mechanic_id
            LEFT JOIN (
                SELECT
                    sale_id,
                    SUM(price) AS service_total
                FROM sales_services
                GROUP BY sale_id
            ) ss ON ss.sale_id = s.id
            LEFT JOIN (
                SELECT
                    dp.sale_id,
                    SUM(COALESCE(dp.service_portion, 0)) AS service_paid
                FROM debt_payments dp
                GROUP BY dp.sale_id
            ) dp ON dp.sale_id = s.id
            WHERE DATE(s.transaction_date) = %s
        ) x
        WHERE
            x.status = 'Paid'
            OR (
                x.status = 'Partial'
                AND x.service_paid >= x.service_total
            )
    """, (today_iso,)).fetchall()

    sales_map = {row["sale_id"]: dict(row) for row in sale_rows}

    rows = []
    if sale_rows:
        sale_ids = [row["sale_id"] for row in sale_rows]
        rows = conn.execute("""
            SELECT
                ss.sale_id,
                sv.name AS service_name,
                ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id = ANY(%s)
            ORDER BY ss.sale_id ASC, sv.name ASC
        """, (sale_ids,)).fetchall()

    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Customer Name", "Vehicle", "Service Name", "Mechanic Name",
        "OR No.", "Amount (Shop cut)", "Amount (Mechanic Cut)", "Total"
    ])

    total_shop_cut = 0.0
    total_mechanic_cut = 0.0
    total_amount = 0.0
    mechanic_totals = {}

    for row in rows:
        sale = sales_map.get(row["sale_id"], {})
        customer_name = sale.get("customer_name", "Walk-in")
        vehicle_name = sale.get("vehicle_name", "N/A")
        service_name = row["service_name"] or ""
        mechanic_name = sale.get("mechanic_name", "N/A")
        sales_number = sale.get("sales_number", "")

        total = round(float(row["price"] or 0), 2)
        commission_rate = round(float(sale.get("commission_rate", 0.0) or 0.0), 2)
        mechanic_cut = round(total * commission_rate, 2)
        shop_cut = round(total - mechanic_cut, 2)

        writer.writerow([
            customer_name,
            vehicle_name,
            service_name,
            mechanic_name,
            sales_number,
            f"{shop_cut:.2f}",
            f"{mechanic_cut:.2f}",
            f"{total:.2f}",
        ])

        total_shop_cut += shop_cut
        total_mechanic_cut += mechanic_cut
        total_amount += total

        mech = mechanic_name or "N/A"
        if mech not in mechanic_totals:
            mechanic_totals[mech] = {
                "mechanic_cut": 0.0,
                "shop_cut": 0.0,
                "total": 0.0,
            }
        mechanic_totals[mech]["mechanic_cut"] += mechanic_cut
        mechanic_totals[mech]["shop_cut"] += shop_cut
        mechanic_totals[mech]["total"] += total

    writer.writerow([
        "TOTAL", "", "", "", "",
        f"{round(total_shop_cut, 2):.2f}",
        f"{round(total_mechanic_cut, 2):.2f}",
        f"{round(total_amount, 2):.2f}",
    ])
    writer.writerow([])
    writer.writerow(["Mechanic Name", "Amount (Mechanic Cut)", "Amount (Shop Cut)", "Total"])
    for mechanic_name, values in sorted(mechanic_totals.items(), key=lambda item: item[0].lower()):
        writer.writerow([
            mechanic_name,
            f"{round(values['mechanic_cut'], 2):.2f}",
            f"{round(values['shop_cut'], 2):.2f}",
            f"{round(values['total'], 2):.2f}",
        ])

    writer.writerow([
        "TOTAL",
        f"{round(total_mechanic_cut, 2):.2f}",
        f"{round(total_shop_cut, 2):.2f}",
        f"{round(total_amount, 2):.2f}",
    ])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=services_sold_{today_iso}.csv"},
    )

