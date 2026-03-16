# Add to imports at the top
import csv
import io
from datetime import datetime, date
from flask import Response
from db.database import get_db
from flask import Blueprint, request, render_template, redirect, url_for, flash
from services.reports_service import (
    get_sales_by_date,
    get_sales_by_range,
    get_sales_report_by_date,
    get_sales_report_by_range,
)
from services.transactions_service import get_purchase_order_export_data
from services.cash_service import get_cash_entries_for_report
from utils.formatters import format_date

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/reports/purchase-order/<int:po_id>")
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


@reports_bp.route("/reports/daily")
def daily_report():
    report_date = request.args.get("date")

    if not report_date:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))
    return redirect(url_for("reports.sales_summary_report", report_date=report_date))


@reports_bp.route("/reports/range")
def range_report():
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        flash("Please select a date range.", "warning")
        return redirect(url_for("index"))
    return redirect(url_for("reports.sales_summary_report", start_date=start, end_date=end))


@reports_bp.route("/reports/sales-summary")
def sales_summary_report():
    report_date = request.args.get("report_date")   # daily report button
    start_date  = request.args.get("start_date")    # range modal
    end_date    = request.args.get("end_date")      # range modal

    # Single-date path (Generate Daily Report button)
    if report_date:
        data        = get_sales_report_by_date(report_date)
        date_label  = format_date(report_date)
        is_range    = False
        # Daily: both bounds are the same date
        cash_data   = get_cash_entries_for_report(report_date, report_date)

    # Range path (Generate Sales Report modal)
    elif start_date and end_date:
        if end_date < start_date:
            flash("End date cannot be before start date.", "warning")
            return redirect(url_for("index"))
        data        = get_sales_report_by_range(start_date, end_date)
        date_label  = f"{format_date(start_date)} to {format_date(end_date)}"
        is_range    = True
        cash_data   = get_cash_entries_for_report(start_date, end_date)

    else:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))

    if not data:
        data = {
            "sales":                [],
            "unresolved":           [],
            "mechanic_summary":     [],
            "quota_failures":       [],
            "items_summary":        [],
            "total_gross":          0.0,
            "total_mech_cut":       0.0,
            "total_shop_topup":     0.0,
            "net_revenue":          0.0,
            "debt_collected":       [],
            "total_debt_collected": 0.0,
        }

    return render_template(
        "reports/sales_report_pdf.html",
        report_date=date_label,
        data=data,
        is_range=is_range,
        cash_data=cash_data,
    )


@reports_bp.route("/export/inventory-snapshot")
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


@reports_bp.route("/export/items-sold-today")
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
        placeholders = ",".join(["%s"] * len(sale_ids))
        rows = conn.execute(f"""
            SELECT
                si.sale_id,
                COALESCE(i.name, '') AS item_name,
                COALESCE(si.quantity, 0) AS quantity,
                COALESCE(si.final_unit_price, 0) AS final_unit_price
            FROM sales_items si
            LEFT JOIN items i ON i.id = si.item_id
            WHERE si.sale_id IN ({placeholders})
            ORDER BY si.sale_id ASC
        """, sale_ids).fetchall()
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
        placeholders = ",".join(["%s"] * len(sale_ids))
        rows = conn.execute(f"""
            SELECT
                ss.sale_id,
                sv.name AS service_name,
                ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id IN ({placeholders})
            ORDER BY ss.sale_id ASC, sv.name ASC
        """, sale_ids).fetchall()

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

