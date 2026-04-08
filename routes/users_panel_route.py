import json

from flask import Blueprint, jsonify, redirect, render_template, request, session, flash, url_for

from auth.utils import admin_required, ensure_authenticated_user, login_required
from services.users_panel_service import (
    add_mechanic_record,
    add_payment_method_record,
    add_service_record,
    create_bundle_record,
    delete_mechanic_quota_topup_override,
    get_bundle_edit_payload,
    get_users_page_context,
    save_mechanic_quota_topup_override,
    toggle_bundle_active_status,
    toggle_mechanic_active_status,
    toggle_payment_method_active_status,
    toggle_service_active_status,
    update_bundle_record,
)
from services.vendor_service import add_vendor_record, toggle_vendor_active_status

users_panel_bp = Blueprint("users_panel", __name__)


USERS_PANEL_TABS = {
    "mechanics-tab",
    "manage-services-tab",
    "bundles-tab",
    "payment-methods-tab",
    "vendors-tab",
    "loyalty-tab",
}

ADMIN_AUDIT_TABS = {
    "users-tab",
    "password-resets-tab",
    "stocktake-access-tab",
    "cash-categories-tab",
    "sales-tab",
    "debt-audit-tab",
    "audit-tab",
    "item-edit-trail-tab",
    "payables-audit-tab",
}

@users_panel_bp.before_request
def protect_users_panel_routes():
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = ensure_authenticated_user()
    if not user:
        return redirect(url_for("auth.login"))

@users_panel_bp.route("/users", methods=["GET"])
def users_panel():
    active_tab = request.args.get("tab", "mechanics-tab")
    if active_tab in ADMIN_AUDIT_TABS:
        if session.get("role") == "admin":
            return redirect(url_for("admin_audit.audit_dashboard", tab=active_tab))
        active_tab = "mechanics-tab"
    if active_tab not in USERS_PANEL_TABS:
        active_tab = "mechanics-tab"
    context = get_users_page_context(active_tab=active_tab)
    return render_template("users/users.html", **context)


@users_panel_bp.route("/mechanics/add", methods=["POST"])
@login_required
def add_mechanic():
    name = request.form.get("name")
    commission = request.form.get("commission")
    phone = request.form.get("phone")

    try:
        add_mechanic_record(name, commission, phone)
        flash(f"Mechanic {name} added successfully!", "success")
    except Exception as exc:
        flash(f"Error adding mechanic: {str(exc)}", "danger")

    return redirect(url_for("users_panel.users_panel", tab="mechanics-tab"))


@users_panel_bp.route("/mechanics/toggle/<int:mechanic_id>", methods=["POST"])
@login_required
def toggle_mechanic(mechanic_id):
    result = toggle_mechanic_active_status(mechanic_id)
    if result["status"] == "missing":
        flash("Mechanic not found.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="mechanics-tab"))

    if result["new_status"] == 0:
        flash(f"Mechanic {result['name']} has been disabled.", "danger")
    elif result["was_active"] == 0 and result["new_status"] == 1:
        flash(f"Mechanic {result['name']} has been re-enabled.", "warning")
    else:
        flash(f"Mechanic {result['name']} has been activated.", "success")

    return redirect(url_for("users_panel.users_panel", tab="mechanics-tab"))


@users_panel_bp.route("/mechanics/quota-topup", methods=["POST"])
@login_required
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

    return redirect(url_for("users_panel.users_panel", tab="mechanics-tab"))


@users_panel_bp.route("/mechanics/quota-topup/<int:override_id>/delete", methods=["POST"])
@login_required
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

    return redirect(url_for("users_panel.users_panel", tab="mechanics-tab"))


@users_panel_bp.route("/services/add", methods=["POST"])
@login_required
def add_service():
    result = add_service_record(
        name=request.form.get("name", ""),
        existing_category=request.form.get("existing_category"),
        new_category=request.form.get("new_category", ""),
    )
    if result["status"] == "missing_fields":
        flash("Service name is required.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="manage-services-tab"))

    if result["status"] == "duplicate":
        flash(f"Service '{result['name']}' already exists!", "warning")
        return redirect(url_for("users_panel.users_panel", tab="manage-services-tab"))

    if result["status"] == "ok":
        flash(f"Success: '{result['name']}' added to '{result['category']}'.", "success")
    else:
        flash("Error adding service.", "danger")

    return redirect(url_for("users_panel.users_panel", tab="manage-services-tab"))


@users_panel_bp.route("/services/toggle/<int:service_id>", methods=["POST"])
@login_required
def toggle_service(service_id):
    result = toggle_service_active_status(service_id)
    if result["status"] == "ok":
        flash(f"Service '{result['name']}' status updated.", "info")
    return redirect(url_for("users_panel.users_panel", tab="manage-services-tab"))


@users_panel_bp.route("/bundles/add", methods=["POST"])
@login_required
def add_bundle():
    try:
        variants = json.loads(request.form.get("variants_json") or "[]")
        service_ids = json.loads(request.form.get("services_json") or "[]")
        items = json.loads(request.form.get("items_json") or "[]")
    except json.JSONDecodeError:
        flash("Bundle form data could not be read. Please try again.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))

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
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))
    except Exception as exc:
        flash(f"Error creating bundle: {str(exc)}", "danger")
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))

    if result["status"] == "duplicate":
        flash(
            f"Bundle '{result['name']}' for '{result['vehicle_category']}' already exists.",
            "warning",
        )
    elif result["status"] == "ok":
        flash(f"Bundle '{result['name']}' created successfully.", "success")
    else:
        flash("Error creating bundle.", "danger")

    return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))


@users_panel_bp.route("/bundles/<int:bundle_id>/edit", methods=["POST"])
@login_required
def edit_bundle(bundle_id):
    try:
        variants = json.loads(request.form.get("variants_json") or "[]")
        service_ids = json.loads(request.form.get("services_json") or "[]")
        items = json.loads(request.form.get("items_json") or "[]")
    except json.JSONDecodeError:
        flash("Bundle form data could not be read. Please try again.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))

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
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))
    except Exception as exc:
        flash(f"Error updating bundle: {str(exc)}", "danger")
        return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))

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

    return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))


@users_panel_bp.route("/bundles/toggle/<int:bundle_id>", methods=["POST"])
@login_required
def toggle_bundle(bundle_id):
    result = toggle_bundle_active_status(bundle_id)
    if result["status"] == "missing":
        flash("Bundle not found.", "danger")
    elif result["new_status"] == 0:
        flash(f"Bundle '{result['name']}' disabled.", "warning")
    else:
        flash(f"Bundle '{result['name']}' activated.", "success")
    return redirect(url_for("users_panel.users_panel", tab="bundles-tab"))


@users_panel_bp.route("/api/bundles/<int:bundle_id>")
@login_required
def bundle_details_api(bundle_id):
    try:
        return jsonify(get_bundle_edit_payload(bundle_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@users_panel_bp.route("/payment-methods/add", methods=["POST"])
@login_required
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

    return redirect(url_for("users_panel.users_panel", tab="payment-methods-tab"))


@users_panel_bp.route("/payment-methods/toggle/<int:pm_id>", methods=["POST"])
@login_required
def toggle_payment_method(pm_id):
    result = toggle_payment_method_active_status(pm_id)
    if result["status"] == "missing":
        flash("Payment method not found.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="payment-methods-tab"))

    if result["new_status"] == 0:
        flash(f"Payment method '{result['name']}' disabled.", "warning")
    else:
        flash(f"Payment method '{result['name']}' activated.", "success")

    return redirect(url_for("users_panel.users_panel", tab="payment-methods-tab"))


@users_panel_bp.route("/vendors/add", methods=["POST"])
@login_required
def add_vendor():
    result = add_vendor_record(
        vendor_name=request.form.get("vendor_name"),
        address=request.form.get("address"),
        contact_person=request.form.get("contact_person"),
        contact_no=request.form.get("contact_no"),
        email=request.form.get("email"),
    )
    if result["status"] == "missing_fields":
        flash(result["message"], "danger")
    elif result["status"] == "duplicate":
        flash(f"Vendor '{result['name']}' already exists.", "warning")
    elif result["status"] == "ok":
        flash(f"Vendor '{result['vendor']['vendor_name']}' added successfully.", "success")
    else:
        flash("Error adding vendor.", "danger")

    return redirect(url_for("users_panel.users_panel", tab="vendors-tab"))


@users_panel_bp.route("/vendors/toggle/<int:vendor_id>", methods=["POST"])
@admin_required
def toggle_vendor(vendor_id):
    result = toggle_vendor_active_status(vendor_id)
    if result["status"] == "missing":
        flash("Vendor not found.", "danger")
        return redirect(url_for("users_panel.users_panel", tab="vendors-tab"))

    if result["new_status"] == 0:
        flash(f"Vendor '{result['name']}' disabled.", "warning")
    else:
        flash(f"Vendor '{result['name']}' activated.", "success")

    return redirect(url_for("users_panel.users_panel", tab="vendors-tab"))
