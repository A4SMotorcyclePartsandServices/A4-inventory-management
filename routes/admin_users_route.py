import json

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, flash, url_for

from auth.utils import ensure_authenticated_user
from services.admin_users_service import (
    _to_bool,
    create_bundle_record,
    add_mechanic_record,
    add_payment_method_record,
    add_service_record,
    create_staff_user,
    delete_mechanic_quota_topup_override,
    get_admin_sales_page,
    get_audit_trail_page,
    get_bundle_edit_payload,
    get_item_edit_trail_page,
    get_item_details_payload,
    get_manage_users_context,
    get_manual_in_details,
    get_payables_audit_page,
    get_sale_refund_context,
    save_mechanic_quota_topup_override,
    toggle_bundle_active_status,
    toggle_mechanic_active_status,
    toggle_payment_method_active_status,
    toggle_service_active_status,
    toggle_user_active_status,
    update_bundle_record,
)
from services.password_reset_service import (
    complete_password_reset_request,
    reject_password_reset_request,
)

admin_users_bp = Blueprint("admin_users", __name__)


MANAGE_USERS_TABS = {
    "mechanics-tab",
    "manage-services-tab",
    "bundles-tab",
    "payment-methods-tab",
    "loyalty-tab",
}

AUDIT_TABS = {
    "users-tab",
    "password-resets-tab",
    "sales-tab",
    "debt-audit-tab",
    "audit-tab",
    "item-edit-trail-tab",
    "payables-audit-tab",
}

USER_PANEL_ENDPOINTS = {
    "admin_users.manage_users",
    "admin_users.add_mechanic",
    "admin_users.toggle_mechanic",
    "admin_users.save_mechanic_quota_topup",
    "admin_users.delete_mechanic_quota_topup",
    "admin_users.add_service",
    "admin_users.toggle_service",
    "admin_users.add_bundle",
    "admin_users.edit_bundle",
    "admin_users.toggle_bundle",
    "admin_users.bundle_details_api",
    "admin_users.add_payment_method",
    "admin_users.toggle_payment_method",
    "admin_users.get_item_details",
}


@admin_users_bp.before_request
def protect_admin_routes():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = ensure_authenticated_user()
    if not user:
        return redirect(url_for("auth.login"))

    if request.endpoint in USER_PANEL_ENDPOINTS:
        return None

    if user["role"] != "admin":
        abort(403)


@admin_users_bp.route("/users", methods=["GET", "POST"])
def manage_users():
    if request.method == "POST":
        if session.get("role") != "admin":
            abort(403)
        username = request.form["username"]
        password = request.form["password"]
        phone_no = request.form["phone_no"]
        current_admin_id = session.get("user_id")
        try:
            create_staff_user(username, password, phone_no, current_admin_id)
            flash(f"Account for {username} created successfully!", "success")
            return redirect(url_for("admin_users.audit_dashboard", tab="users-tab"))
        except ValueError as exc:
            flash(str(exc), "danger")
        except Exception as exc:
            flash(f"Error creating user: {str(exc)}", "danger")
        return redirect(url_for("admin_users.audit_dashboard", tab="users-tab"))

    active_tab = request.args.get("tab", "mechanics-tab")
    if request.method == "GET" and active_tab in AUDIT_TABS:
        if session.get("role") != "admin":
            active_tab = "mechanics-tab"
        else:
            return redirect(url_for("admin_users.audit_dashboard", tab=active_tab))
    if active_tab not in MANAGE_USERS_TABS:
        active_tab = "mechanics-tab"
    context = get_manage_users_context(active_tab=active_tab, include_audit_data=False)
    return render_template("users/users.html", **context)


@admin_users_bp.route("/users/audit", methods=["GET"])
def audit_dashboard():
    active_tab = request.args.get("tab", "users-tab")
    if active_tab in MANAGE_USERS_TABS:
        return redirect(url_for("admin_users.manage_users", tab=active_tab))
    if active_tab not in AUDIT_TABS:
        active_tab = "users-tab"

    context = get_manage_users_context(active_tab=active_tab, include_audit_data=True)
    return render_template("users/audit.html", **context)


@admin_users_bp.route("/users/toggle/<int:user_id>", methods=["POST"])
def toggle_user(user_id):
    result = toggle_user_active_status(user_id)
    if result["status"] == "missing":
        flash("User not found.", "danger")
        return redirect(url_for("admin_users.audit_dashboard", tab="users-tab"))
    if result["status"] == "forbidden_admin":
        flash("Administrator accounts cannot be disabled.", "danger")
        return redirect(url_for("admin_users.audit_dashboard", tab="users-tab"))

    if result["new_status"] == 0:
        flash(f"User {result['username']} has been disabled.", "danger")
    elif result["was_active"] == 0 and result["new_status"] == 1:
        flash(f"User {result['username']} has been re-enabled.", "warning")
    else:
        flash(f"User {result['username']} has been activated.", "success")

    return redirect(url_for("admin_users.audit_dashboard", tab="users-tab"))


@admin_users_bp.route("/password-resets/<int:request_id>/complete", methods=["POST"])
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

    return redirect(url_for("admin_users.audit_dashboard", tab="password-resets-tab"))


@admin_users_bp.route("/password-resets/<int:request_id>/reject", methods=["POST"])
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

    return redirect(url_for("admin_users.audit_dashboard", tab="password-resets-tab"))


@admin_users_bp.route("/mechanics/add", methods=["POST"])
def add_mechanic():
    name = request.form.get("name")
    commission = request.form.get("commission")
    phone = request.form.get("phone")

    try:
        add_mechanic_record(name, commission, phone)
        flash(f"Mechanic {name} added successfully!", "success")
    except Exception as exc:
        flash(f"Error adding mechanic: {str(exc)}", "danger")

    return redirect(url_for("admin_users.manage_users", tab="mechanics-tab"))


@admin_users_bp.route("/mechanics/toggle/<int:mechanic_id>", methods=["POST"])
def toggle_mechanic(mechanic_id):
    result = toggle_mechanic_active_status(mechanic_id)
    if result["status"] == "missing":
        flash("Mechanic not found.", "danger")
        return redirect(url_for("admin_users.manage_users", tab="mechanics-tab"))

    if result["new_status"] == 0:
        flash(f"Mechanic {result['name']} has been disabled.", "danger")
    elif result["was_active"] == 0 and result["new_status"] == 1:
        flash(f"Mechanic {result['name']} has been re-enabled.", "warning")
    else:
        flash(f"Mechanic {result['name']} has been activated.", "success")

    return redirect(url_for("admin_users.manage_users", tab="mechanics-tab"))


@admin_users_bp.route("/mechanics/quota-topup", methods=["POST"])
def save_mechanic_quota_topup():
    try:
        result = save_mechanic_quota_topup_override(
            mechanic_id=request.form.get("mechanic_id"),
            quota_date=request.form.get("quota_date"),
            applies_quota_topup=request.form.get("applies_quota_topup"),
        )
        action_text = "will apply" if result["applies_quota_topup"] == 1 else "will be skipped"
        flash(
            f"Quota top-up for {result['mechanic_name']} on {result['quota_date']} {action_text}.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "danger")
    except Exception as exc:
        flash(f"Error saving quota top-up override: {str(exc)}", "danger")

    return redirect(url_for("admin_users.manage_users", tab="mechanics-tab"))


@admin_users_bp.route("/mechanics/quota-topup/<int:override_id>/delete", methods=["POST"])
def delete_mechanic_quota_topup(override_id):
    try:
        result = delete_mechanic_quota_topup_override(override_id)
        if result["status"] == "missing":
            flash("Quota top-up override not found.", "danger")
        else:
            flash(
                f"Removed quota top-up override for {result['mechanic_name']} on {result['quota_date']}.",
                "warning",
            )
    except Exception as exc:
        flash(f"Error deleting quota top-up override: {str(exc)}", "danger")

    return redirect(url_for("admin_users.manage_users", tab="mechanics-tab"))


@admin_users_bp.route("/sales/details/<reference_id>")
def sale_details(reference_id):
    try:
        return get_sale_refund_context(int(reference_id))
    except ValueError as exc:
        return {"error": str(exc)}, 404
    except Exception as exc:
        return {"error": str(exc)}, 500


@admin_users_bp.route("/audit/manual-in/<int:audit_group_id>")
def manual_in_details(audit_group_id):
    payload, status_code = get_manual_in_details(audit_group_id)
    return jsonify(payload), status_code


@admin_users_bp.route("/services/add", methods=["POST"])
def add_service():
    result = add_service_record(
        name=request.form.get("name", ""),
        existing_category=request.form.get("existing_category"),
        new_category=request.form.get("new_category", ""),
    )
    if result["status"] == "duplicate":
        flash(f"Service '{result['name']}' already exists!", "warning")
        return redirect(url_for("admin_users.manage_users", tab="manage-services-tab"))

    if result["status"] == "ok":
        flash(f"Success: '{result['name']}' added to '{result['category']}'.", "success")
    else:
        flash("Error adding service.", "danger")

    return redirect(url_for("admin_users.manage_users", tab="manage-services-tab"))


@admin_users_bp.route("/services/toggle/<int:service_id>", methods=["POST"])
def toggle_service(service_id):
    result = toggle_service_active_status(service_id)
    if result["status"] == "ok":
        flash(f"Service '{result['name']}' status updated.", "info")
    return redirect(url_for("admin_users.manage_users", tab="manage-services-tab"))


@admin_users_bp.route("/bundles/add", methods=["POST"])
def add_bundle():
    try:
        variants = json.loads(request.form.get("variants_json") or "[]")
        service_ids = json.loads(request.form.get("services_json") or "[]")
        items = json.loads(request.form.get("items_json") or "[]")
    except json.JSONDecodeError:
        flash("Bundle form data could not be read. Please try again.", "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))

    try:
        result = create_bundle_record(
            name=request.form.get("name", ""),
            vehicle_category=request.form.get("vehicle_category", ""),
            variants=variants,
            service_ids=service_ids,
            items=items,
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))
    except Exception as exc:
        flash(f"Error creating bundle: {str(exc)}", "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))

    if result["status"] == "duplicate":
        flash(
            f"Bundle '{result['name']}' for '{result['vehicle_category']}' already exists.",
            "warning",
        )
    elif result["status"] == "ok":
        flash(f"Bundle '{result['name']}' created successfully.", "success")
    else:
        flash("Error creating bundle.", "danger")

    return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))


@admin_users_bp.route("/bundles/<int:bundle_id>/edit", methods=["POST"])
def edit_bundle(bundle_id):
    try:
        variants = json.loads(request.form.get("variants_json") or "[]")
        service_ids = json.loads(request.form.get("services_json") or "[]")
        items = json.loads(request.form.get("items_json") or "[]")
    except json.JSONDecodeError:
        flash("Bundle form data could not be read. Please try again.", "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))

    try:
        result = update_bundle_record(
            bundle_id=bundle_id,
            name=request.form.get("name", ""),
            vehicle_category=request.form.get("vehicle_category", ""),
            variants=variants,
            service_ids=service_ids,
            items=items,
            changed_by=session.get("user_id"),
            changed_by_username=session.get("username"),
            change_notes=request.form.get("change_notes", ""),
        )
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))
    except Exception as exc:
        flash(f"Error updating bundle: {str(exc)}", "danger")
        return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))

    if result["status"] == "missing":
        flash("Bundle not found.", "danger")
    elif result["status"] == "duplicate":
        flash(
            f"Bundle '{result['name']}' for '{result['vehicle_category']}' already exists.",
            "warning",
        )
    elif result["status"] == "ok":
        flash(
            f"Bundle '{result['name']}' updated. New version: {result['version_no']}.",
            "success",
        )
    else:
        flash("Error updating bundle.", "danger")

    return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))


@admin_users_bp.route("/bundles/toggle/<int:bundle_id>", methods=["POST"])
def toggle_bundle(bundle_id):
    result = toggle_bundle_active_status(bundle_id)
    if result["status"] == "missing":
        flash("Bundle not found.", "danger")
    elif result["new_status"] == 0:
        flash(f"Bundle '{result['name']}' disabled.", "warning")
    else:
        flash(f"Bundle '{result['name']}' activated.", "success")
    return redirect(url_for("admin_users.manage_users", tab="bundles-tab"))


@admin_users_bp.route("/api/bundles/<int:bundle_id>")
def bundle_details_api(bundle_id):
    try:
        return jsonify(get_bundle_edit_payload(bundle_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@admin_users_bp.route("/payment-methods/add", methods=["POST"])
def add_payment_method():
    result = add_payment_method_record(
        name=request.form.get("name"),
        category=request.form.get("category"),
    )
    if result["status"] == "missing_fields":
        flash("Payment method name and category are required.", "danger")
    elif result["status"] == "invalid_category":
        flash("Invalid payment method category.", "danger")
    elif result["status"] == "duplicate":
        flash(f"Payment method '{result['name']}' already exists.", "warning")
    elif result["status"] == "ok":
        flash(f"Payment method '{result['name']}' added successfully.", "success")
    else:
        flash("Error adding payment method.", "danger")

    return redirect(url_for("admin_users.manage_users", tab="payment-methods-tab"))


@admin_users_bp.route("/payment-methods/toggle/<int:pm_id>", methods=["POST"])
def toggle_payment_method(pm_id):
    result = toggle_payment_method_active_status(pm_id)
    if result["status"] == "missing":
        flash("Payment method not found.", "danger")
        return redirect(url_for("admin_users.manage_users", tab="payment-methods-tab"))

    if result["new_status"] == 0:
        flash(f"Payment method '{result['name']}' disabled.", "warning")
    else:
        flash(f"Payment method '{result['name']}' activated.", "success")

    return redirect(url_for("admin_users.manage_users", tab="payment-methods-tab"))


@admin_users_bp.route("/api/audit/trail")
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


@admin_users_bp.route("/api/audit/item-edits")
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


@admin_users_bp.route("/api/admin/sales")
def admin_sales_api():
    try:
        data = get_admin_sales_page(
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


@admin_users_bp.route("/api/payables/audit")
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


@admin_users_bp.route("/api/item/<int:item_id>")
def get_item_details(item_id):
    try:
        item = get_item_details_payload(item_id)
        if not item:
            return jsonify({"error": "Item not found"}), 404
        return jsonify(item)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
