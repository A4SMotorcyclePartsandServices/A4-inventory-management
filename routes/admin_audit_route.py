from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, flash, url_for

from auth.utils import admin_required, ensure_authenticated_user
from services.admin_audit_service import (
    _to_bool,
    get_audit_dashboard_context,
    get_audit_sales_page,
    get_audit_trail_page,
    get_item_edit_trail_page,
    get_payables_audit_page,
    toggle_user_active_status,
)
from services.password_reset_service import (
    complete_password_reset_request,
    reject_password_reset_request,
)
from services.stocktake_access_service import (
    approve_stocktake_access_request,
    reject_stocktake_access_request,
    revoke_stocktake_access,
)
from services.transactions_service import get_sale_refund_context
from services.users_panel_service import get_item_details_payload, get_manual_in_details

admin_audit_bp = Blueprint("admin_audit", __name__)


USERS_PANEL_TABS = {
    "mechanics-tab",
    "manage-services-tab",
    "bundles-tab",
    "payment-methods-tab",
    "loyalty-tab",
}

AUDIT_TABS = {
    "users-tab",
    "password-resets-tab",
    "stocktake-access-tab",
    "sales-tab",
    "debt-audit-tab",
    "audit-tab",
    "item-edit-trail-tab",
    "payables-audit-tab",
}


@admin_audit_bp.before_request
def protect_admin_audit_routes():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = ensure_authenticated_user()
    if not user:
        return redirect(url_for("auth.login"))

    if user["role"] != "admin":
        abort(403)


@admin_audit_bp.route("/users/audit", methods=["GET"])
def audit_dashboard():
    active_tab = request.args.get("tab", "users-tab")
    if active_tab in USERS_PANEL_TABS:
        return redirect(url_for("users_panel.users_panel", tab=active_tab))
    if active_tab not in AUDIT_TABS:
        active_tab = "users-tab"

    context = get_audit_dashboard_context(active_tab=active_tab)
    return render_template("users/audit.html", **context)


@admin_audit_bp.route("/users/audit/create-user", methods=["POST"])
@admin_required
def create_user():
    from services.users_panel_service import create_staff_user

    username = request.form["username"]
    password = request.form["password"]
    phone_no = request.form["phone_no"]
    current_admin_id = session.get("user_id")
    try:
        create_staff_user(username, password, phone_no, current_admin_id)
        flash(f"Account for {username} created successfully!", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error creating user: {str(exc)}", "danger")
    return redirect(url_for("admin_audit.audit_dashboard", tab="users-tab"))


@admin_audit_bp.route("/users/toggle/<int:user_id>", methods=["POST"])
def toggle_user(user_id):
    result = toggle_user_active_status(user_id)
    if result["status"] == "missing":
        flash("User not found.", "danger")
        return redirect(url_for("admin_audit.audit_dashboard", tab="users-tab"))
    if result["status"] == "forbidden_admin":
        flash("Administrator accounts cannot be disabled.", "danger")
        return redirect(url_for("admin_audit.audit_dashboard", tab="users-tab"))

    if result["new_status"] == 0:
        flash(f"User {result['username']} has been disabled.", "danger")
    elif result["was_active"] == 0 and result["new_status"] == 1:
        flash(f"User {result['username']} has been re-enabled.", "warning")
    else:
        flash(f"User {result['username']} has been activated.", "success")

    return redirect(url_for("admin_audit.audit_dashboard", tab="users-tab"))


@admin_audit_bp.route("/password-resets/<int:request_id>/complete", methods=["POST"])
@admin_required
def complete_password_reset(request_id):
    temporary_password = request.form.get("temporary_password", "")
    admin_note = request.form.get("admin_note", "")
    try:
        result = complete_password_reset_request(
            request_id=request_id,
            temporary_password=temporary_password,
            admin_user_id=session.get("user_id"),
            admin_note=admin_note,
        )
        flash(
            f"Temporary password set for {result['username']}. Ask the user to sign in and change it immediately.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error completing password reset: {str(exc)}", "danger")

    return redirect(url_for("admin_audit.audit_dashboard", tab="password-resets-tab"))


@admin_audit_bp.route("/password-resets/<int:request_id>/reject", methods=["POST"])
@admin_required
def reject_password_reset(request_id):
    admin_note = request.form.get("admin_note", "")
    try:
        reject_password_reset_request(
            request_id=request_id,
            admin_user_id=session.get("user_id"),
            admin_note=admin_note,
        )
        flash("Password reset request rejected.", "warning")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error rejecting password reset request: {str(exc)}", "danger")

    return redirect(url_for("admin_audit.audit_dashboard", tab="password-resets-tab"))


@admin_audit_bp.route("/stocktake-access/<int:approval_request_id>/approve", methods=["POST"])
@admin_required
def approve_stocktake_access(approval_request_id):
    expires_on = request.form.get("expires_on")
    admin_note = request.form.get("admin_note", "")
    try:
        approve_stocktake_access_request(
            approval_request_id=approval_request_id,
            admin_user_id=session.get("user_id"),
            expires_on=expires_on,
            notes=admin_note,
        )
        flash("Stocktake access granted.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error approving stocktake access: {str(exc)}", "danger")

    return redirect(url_for("admin_audit.audit_dashboard", tab="stocktake-access-tab"))


@admin_audit_bp.route("/stocktake-access/<int:approval_request_id>/reject", methods=["POST"])
@admin_required
def reject_stocktake_access(approval_request_id):
    admin_note = request.form.get("admin_note", "")
    try:
        reject_stocktake_access_request(
            approval_request_id=approval_request_id,
            admin_user_id=session.get("user_id"),
            notes=admin_note,
        )
        flash("Stocktake access request rejected.", "warning")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error rejecting stocktake access request: {str(exc)}", "danger")

    return redirect(url_for("admin_audit.audit_dashboard", tab="stocktake-access-tab"))


@admin_audit_bp.route("/stocktake-access/<int:approval_request_id>/revoke", methods=["POST"])
@admin_required
def revoke_stocktake_access_route(approval_request_id):
    admin_note = request.form.get("admin_note", "")
    try:
        revoke_stocktake_access(
            approval_request_id=approval_request_id,
            admin_user_id=session.get("user_id"),
            notes=admin_note,
        )
        flash("Stocktake access revoked.", "warning")
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error revoking stocktake access: {str(exc)}", "danger")

    return redirect(url_for("admin_audit.audit_dashboard", tab="stocktake-access-tab"))


@admin_audit_bp.route("/api/audit/trail")
def audit_trail_api():
    try:
        data = get_audit_trail_page(
            page=int(request.args.get("page", 1)),
            start_date=request.args.get("start_date") or None,
            end_date=request.args.get("end_date") or None,
            movement_type=request.args.get("type") or None,
            has_discount=_to_bool(request.args.get("has_discount")),
        )
        return jsonify(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_audit_bp.route("/api/audit/item-edits")
def item_edit_trail_api():
    try:
        data = get_item_edit_trail_page(
            page=int(request.args.get("page", 1)),
            start_date=request.args.get("start_date") or None,
            end_date=request.args.get("end_date") or None,
            search=(request.args.get("search") or "").strip() or None,
        )
        return jsonify(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_audit_bp.route("/api/admin/sales")
def admin_sales_api():
    try:
        data = get_audit_sales_page(
            page=int(request.args.get("page", 1)),
            start_date=request.args.get("start_date") or None,
            end_date=request.args.get("end_date") or None,
            search=request.args.get("search", "").strip() or None,
            payment_status=request.args.get("payment_status") or None,
            has_discount=_to_bool(request.args.get("has_discount")),
        )
        return jsonify(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_audit_bp.route("/api/payables/audit")
def payables_audit_api():
    try:
        data = get_payables_audit_page(
            page=int(request.args.get("page", 1)),
            start_date=request.args.get("start_date") or None,
            end_date=request.args.get("end_date") or None,
            event_type=request.args.get("event_type") or None,
            source_type=request.args.get("source_type") or None,
            payee_search=(request.args.get("payee_search") or "").strip() or None,
            cheque_no_search=(request.args.get("cheque_no_search") or "").strip() or None,
        )
        return jsonify(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_audit_bp.route("/sales/details/<reference_id>")
def sale_details(reference_id):
    try:
        return get_sale_refund_context(int(reference_id))
    except ValueError as exc:
        return {"error": str(exc)}, 404
    except Exception as exc:
        return {"error": str(exc)}, 500


@admin_audit_bp.route("/audit/manual-in/<int:audit_group_id>")
def manual_in_details(audit_group_id):
    payload, status_code = get_manual_in_details(audit_group_id)
    return jsonify(payload), status_code


@admin_audit_bp.route("/api/item/<int:item_id>")
def get_item_details(item_id):
    try:
        item = get_item_details_payload(item_id)
        if not item:
            return jsonify({"error": "Item not found"}), 404
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
