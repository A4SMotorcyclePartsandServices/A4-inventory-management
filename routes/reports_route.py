from flask import Blueprint, request, render_template, redirect, url_for, flash
from services.reports_service import (
    get_sales_by_date,
    get_sales_by_range,
    get_sales_report_by_date,
    get_sales_report_by_range,
)
from utils.formatters import format_date

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/reports/daily")
def daily_report():
    report_date = request.args.get("date")

    if not report_date:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))

    sales = get_sales_by_date(report_date)

    return render_template(
        "reports/daily.html",
        report_date=report_date,
        sales=sales
    )


@reports_bp.route("/reports/range")
def range_report():
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        flash("Please select a date range.", "warning")
        return redirect(url_for("index"))

    sales = get_sales_by_range(start, end)

    return render_template(
        "reports/range.html",
        start=start,
        end=end,
        sales=sales
    )


@reports_bp.route("/reports/sales-summary")
def sales_summary_report():
    report_date = request.args.get("report_date")   # daily report button
    start_date  = request.args.get("start_date")    # range modal
    end_date    = request.args.get("end_date")      # range modal

    # Single-date path (Generate Daily Report button)
    if report_date:
        data = get_sales_report_by_date(report_date)
        date_label = format_date(report_date)
        is_range   = False

    # Range path (Generate Sales Report modal)
    elif start_date and end_date:
        if end_date < start_date:
            flash("End date cannot be before start date.", "warning")
            return redirect(url_for("index"))
        data = get_sales_report_by_range(start_date, end_date)
        date_label = f"{format_date(start_date)} to {format_date(end_date)}"
        is_range   = True

    else:
        flash("Please select a date.", "warning")
        return redirect(url_for("index"))

    if not data:
        data = {
            "sales":            [],
            "unresolved":       [],
            "mechanic_summary": [],
            "items_summary":    [],
            "total_gross":      0.0,
            "total_mech_cut":   0.0,
            "total_shop_topup": 0.0,
            "net_revenue":      0.0,
            "debt_collected":   [],
            "total_debt_collected": 0.0, 
        }

    return render_template(
        "reports/sales_report_pdf.html",
        report_date=date_label,
        data=data,
        is_range=is_range,
    )