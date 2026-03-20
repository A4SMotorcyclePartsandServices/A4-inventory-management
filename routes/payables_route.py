from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from auth.utils import login_required
from services.payables_service import (
    build_payables_report_context,
    create_manual_payable,
    get_payable_cheque_history,
    get_payables_history_by_month,
    get_payables_history_month_summaries,
    get_payables_page_context,
    issue_payable_cheque,
    update_payable_cheque_status,
)


payables_bp = Blueprint("payables", __name__)


@payables_bp.route("/transaction/payables")
@login_required
def payables_page():
    context = get_payables_page_context(search_query=request.args.get("q"))
    return render_template("transactions/payables.html", **context)


@payables_bp.route("/api/payables/<int:payable_id>/cheques")
@login_required
def payable_cheque_history_api(payable_id):
    try:
        return jsonify(get_payable_cheque_history(payable_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


@payables_bp.route("/api/payables/history/summary")
@login_required
def payables_history_summary_api():
    try:
        return jsonify(get_payables_history_month_summaries(search_query=request.args.get("q")))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@payables_bp.route("/api/payables/history/month")
@login_required
def payables_history_month_api():
    try:
        return jsonify(
            get_payables_history_by_month(
                month_key=request.args.get("month"),
                search_query=request.args.get("q"),
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@payables_bp.route("/transaction/payables/manual", methods=["POST"])
@login_required
def create_manual_payable_action():
    try:
        create_manual_payable(
            payee_name=request.form.get("payee_name"),
            description=request.form.get("description"),
            amount_due=request.form.get("amount_due"),
            reference_no=request.form.get("reference_no"),
            created_by=session.get("user_id"),
            created_by_username=session.get("username"),
        )
        flash("Manual payable created successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Failed to create manual payable: {exc}", "danger")
    return redirect(url_for("payables.payables_page"))


@payables_bp.route("/transaction/payables/<int:payable_id>/cheques", methods=["POST"])
@login_required
def issue_payable_cheque_action(payable_id):
    try:
        issue_payable_cheque(
            payable_id,
            cheque_no=request.form.get("cheque_no"),
            cheque_date=request.form.get("cheque_date"),
            cheque_amount=request.form.get("cheque_amount"),
            notes=request.form.get("notes"),
            created_by=session.get("user_id"),
            created_by_username=session.get("username"),
        )
        flash("Cheque issued successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Failed to issue cheque: {exc}", "danger")
    return redirect(url_for("payables.payables_page"))


@payables_bp.route("/transaction/payables/cheques/<int:cheque_id>/status", methods=["POST"])
@login_required
def update_cheque_status_action(cheque_id):
    try:
        update_payable_cheque_status(
            cheque_id,
            request.form.get("status"),
            created_by=session.get("user_id"),
            created_by_username=session.get("username"),
        )
        flash("Cheque status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Failed to update cheque status: {exc}", "danger")
    return redirect(url_for("payables.payables_page"))


@payables_bp.route("/reports/payables")
@login_required
def payables_report():
    try:
        context = build_payables_report_context(
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
        )
        return render_template("reports/payables_pdf.html", report=context)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("payables.payables_page"))
