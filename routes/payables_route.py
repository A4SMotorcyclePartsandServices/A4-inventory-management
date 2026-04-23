from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from auth.utils import login_required
from services.payables_service import (
    build_payables_report_context,
    create_manual_payable,
    get_payable_cheque_history,
    get_payables_history_by_month,
    get_payables_history_month_summaries,
    get_payables_page_context,
    issue_payable_cash_payment,
    issue_payable_cheque,
    update_payable_cash_payment_status,
    update_payable_cheque_status,
)
from services.idempotency_service import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)


payables_bp = Blueprint("payables", __name__)


def _redirect_from_idempotency_replay(payload):
    flash(payload.get("flash_message", "Request already processed."), payload.get("flash_category", "info"))
    return redirect(payload.get("redirect_to") or url_for("payables.payables_page"))


@payables_bp.route("/transaction/payables")
@login_required
def payables_page():
    context = get_payables_page_context(
        search_query=request.args.get("q"),
        statuses=request.args.getlist("status"),
    )
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
        return jsonify(
            get_payables_history_month_summaries(
                search_query=request.args.get("q"),
                statuses=request.args.getlist("status"),
            )
        )
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
                statuses=request.args.getlist("status"),
            )
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@payables_bp.route("/transaction/payables/manual", methods=["POST"])
@login_required
def create_manual_payable_action():
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    request_payload = request.form.to_dict(flat=False)
    redirect_to = url_for("payables.payables_page")
    try:
        request_state = begin_idempotent_request(
            scope="payable.manual.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(redirect_to)

    if request_state["state"] == "replay":
        return _redirect_from_idempotency_replay(request_state["response_body"])
    if request_state["state"] in {"processing", "mismatch"}:
        flash(request_state["message"], "warning")
        return redirect(redirect_to)

    try:
        payable_id = create_manual_payable(
            payee_name=request.form.get("payee_name"),
            description=request.form.get("description"),
            amount_due=request.form.get("amount_due"),
            reference_no=request.form.get("reference_no"),
            created_by=user_id,
            created_by_username=session.get("username"),
        )
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": "Manual payable created successfully.",
            "flash_category": "success",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="payable.manual.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=302,
            response_body=response_body,
            resource_type="payable",
            resource_id=payable_id,
        )
    except ValueError as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": str(exc),
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="payable.manual.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    except Exception as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": f"Failed to create manual payable: {exc}",
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="payable.manual.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    return redirect(redirect_to)


@payables_bp.route("/transaction/payables/<int:payable_id>/cheques", methods=["POST"])
@login_required
def issue_payable_cheque_action(payable_id):
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    request_payload = request.form.to_dict(flat=False)
    redirect_to = url_for("payables.payables_page")
    scope = f"payable.cheque.issue:{payable_id}"
    try:
        request_state = begin_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(redirect_to)

    if request_state["state"] == "replay":
        return _redirect_from_idempotency_replay(request_state["response_body"])
    if request_state["state"] in {"processing", "mismatch"}:
        flash(request_state["message"], "warning")
        return redirect(redirect_to)

    try:
        cheque_id = issue_payable_cheque(
            payable_id,
            cheque_no=request.form.get("cheque_no"),
            cheque_date=request.form.get("cheque_date"),
            cheque_amount=request.form.get("cheque_amount"),
            notes=request.form.get("notes"),
            created_by=user_id,
            created_by_username=session.get("username"),
        )
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": "Cheque issued successfully.",
            "flash_category": "success",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=302,
            response_body=response_body,
            resource_type="payable_cheque",
            resource_id=cheque_id,
        )
    except ValueError as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": str(exc),
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    except Exception as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": f"Failed to issue cheque: {exc}",
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    return redirect(redirect_to)


@payables_bp.route("/transaction/payables/cheques/<int:cheque_id>/status", methods=["POST"])
@login_required
def update_cheque_status_action(cheque_id):
    try:
        update_payable_cheque_status(
            cheque_id,
            request.form.get("status"),
            notes=request.form.get("cancellation_note"),
            created_by=session.get("user_id"),
            created_by_username=session.get("username"),
        )
        flash("Cheque status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Failed to update cheque status: {exc}", "danger")
    return redirect(url_for("payables.payables_page"))


@payables_bp.route("/transaction/payables/<int:payable_id>/cash-payments", methods=["POST"])
@login_required
def issue_cash_payment_action(payable_id):
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    request_payload = request.form.to_dict(flat=False)
    redirect_to = url_for("payables.payables_page")
    scope = f"payable.cash_payment.issue:{payable_id}"
    try:
        request_state = begin_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(redirect_to)

    if request_state["state"] == "replay":
        return _redirect_from_idempotency_replay(request_state["response_body"])
    if request_state["state"] in {"processing", "mismatch"}:
        flash(request_state["message"], "warning")
        return redirect(redirect_to)

    try:
        payment_id = issue_payable_cash_payment(
            payable_id,
            payment_due_date=request.form.get("payment_due_date"),
            amount=request.form.get("amount"),
            notes=request.form.get("notes"),
            created_by=user_id,
            created_by_username=session.get("username"),
        )
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": "Cash payment issued successfully.",
            "flash_category": "success",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=302,
            response_body=response_body,
            resource_type="payable_cash_payment",
            resource_id=payment_id,
        )
    except ValueError as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": str(exc),
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    except Exception as exc:
        response_body = {
            "redirect_to": redirect_to,
            "flash_message": f"Failed to issue cash payment: {exc}",
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
    return redirect(redirect_to)


@payables_bp.route("/transaction/payables/cash-payments/<int:payment_id>/status", methods=["POST"])
@login_required
def update_cash_payment_status_action(payment_id):
    try:
        update_payable_cash_payment_status(
            payment_id,
            request.form.get("status"),
            created_by=session.get("user_id"),
            created_by_username=session.get("username"),
        )
        flash("Cash payment status updated.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Failed to update cash payment status: {exc}", "danger")
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
