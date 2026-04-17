import csv
import io
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, session, url_for, flash, jsonify, Response
from db.database import get_db
from auth.utils import admin_required, login_required
from services.inventory_service import get_unique_categories, get_vendor_recommended_items
from utils.formatters import format_date
from services.transactions_service import (
    add_item_to_db,
    normalize_item_category,
    get_item_edit_context,
    get_active_bundles_for_sale,
    get_bundle_sale_config,
    get_transaction_out_context,
    process_manual_stock_in,
    record_sale,
    record_sale_refund,
    search_sales_for_refund,
    create_purchase_order,
    get_sale_refund_context,
    get_active_purchase_orders,
    get_purchase_order_with_items,
    get_purchase_order_details,
    get_po_for_receive_page,
    approve_purchase_order,
    cancel_purchase_order,
    receive_purchase_order,
    get_po_details_for_api,
    get_purchase_order_export_data,
    get_purchase_order_archive_month_summaries,
    get_purchase_orders_by_archive_month,
    request_po_revisions,
    search_purchase_orders,
    update_item_record,
    update_purchase_order,
    get_purchase_order_review_context,
    get_item_edit_history_page,
)
from services.idempotency_service import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)
from utils.timezone import now_local

transaction_bp = Blueprint('transaction', __name__)


def _begin_json_idempotent_request(scope, data):
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    request_state = begin_idempotent_request(
        scope=scope,
        actor_user_id=user_id,
        idempotency_key=idempotency_key,
        request_payload=data,
    )
    return user_id, idempotency_key, request_state


def _idempotency_error_response(request_state):
    if request_state["state"] == "replay":
        return jsonify(request_state["response_body"]), request_state["response_code"]
    if request_state["state"] in {"processing", "mismatch"}:
        return jsonify({"status": "error", "message": request_state["message"]}), 409
    return None


def _redirect_from_idempotency_replay(payload, default_endpoint):
    flash(payload.get("flash_message", "Request already processed."), payload.get("flash_category", "info"))
    return redirect(payload.get("redirect_to") or url_for(default_endpoint))


def _get_active_vendors():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, vendor_name, address, contact_person, contact_no, email
            FROM vendors
            WHERE is_active = 1
            ORDER BY vendor_name ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@transaction_bp.route("/transaction/out")
@login_required
def transaction_out():
    context = get_transaction_out_context()
    return render_template("transactions/out.html", **context)


@transaction_bp.route("/api/bundles/sale-options")
@login_required
def bundle_sale_options_api():
    try:
        return jsonify({"bundles": get_active_bundles_for_sale()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@transaction_bp.route("/api/bundles/<int:bundle_id>/sale-config")
@login_required
def bundle_sale_config_api(bundle_id):
    try:
        return jsonify(get_bundle_sale_config(bundle_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@transaction_bp.route("/transaction/refund")
@login_required
def transaction_refund():
    return render_template("transactions/refund.html")


@transaction_bp.route("/transaction/in")
@login_required
def transaction_in():
    prefilled_id = request.args.get('selected_id')
    return render_template("transactions/in.html", prefilled_id=prefilled_id)


@transaction_bp.route("/transaction/items")
@login_required
def manage_items():
    categories = get_unique_categories()
    vendors = _get_active_vendors()
    return_to = request.args.get('return_to', 'in')
    prefill_name = (request.args.get('prefill_name') or '').strip()
    return render_template(
        "transactions/items.html",
        categories=categories,
        vendors=vendors,
        return_to=return_to,
        prefill_name=prefill_name
    )


@transaction_bp.route("/transaction/items/edit/<int:item_id>")
@login_required
def edit_item_page(item_id):
    categories = get_unique_categories()
    vendors = _get_active_vendors()
    context = get_item_edit_context(item_id)
    if not context:
        flash("Item not found.", "danger")
        return redirect(url_for("index"))

    return render_template(
        "transactions/edit_items.html",
        categories=categories,
        vendors=vendors,
        item=context["item"],
        preview_history=context["preview_history"],
        history_total_count=context["history_total_count"],
    )


@transaction_bp.route("/api/items/<int:item_id>/edit-history")
@login_required
def item_edit_history_api(item_id):
    try:
        context = get_item_edit_context(item_id, preview_limit=0)
        if not context:
            return jsonify({"error": "Item not found."}), 404

        offset = request.args.get("offset", 0)
        limit = request.args.get("limit", 10)
        return jsonify(get_item_edit_history_page(item_id, offset=offset, limit=limit))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@transaction_bp.route("/items/add", methods=["POST"])
@login_required
def add_item():
    existing_cat = request.form.get("existing_category", "").strip()
    new_cat = request.form.get("new_category", "").strip()
    category = normalize_item_category(existing_cat, new_cat)

    name = (request.form.get("name") or "").strip()
    vendor_price = request.form.get("vendor_price", "").strip()
    cost_per_piece = request.form.get("cost_per_piece", "").strip()
    selling_price = request.form.get("a4s_selling_price", "").strip()
    return_to = request.form.get("return_to", "in")

    if not name or not category or not vendor_price or not cost_per_piece or not selling_price:
        flash("Item name, category, and all pricing fields are required.", "danger")
        return redirect(url_for('transaction.manage_items', return_to=return_to))

    form_data = {
        'name': name,
        'category': category,
        'description': request.form.get("description"),
        'pack_size': request.form.get("pack_size"),
        'vendor_price': vendor_price or 0,
        'cost_per_piece': cost_per_piece or 0,
        'selling_price': selling_price or 0,
        'markup': request.form.get("markup") or 0,
        'vendor_id': request.form.get("vendor_id") or None,
        'mechanic': request.form.get("mechanic")
    }

    try:
        new_item_id = add_item_to_db(form_data, user_id=session.get('user_id'), username=session.get('username'))
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for(
            'transaction.manage_items',
            return_to=return_to,
            prefill_name=name,
        ))

    # Redirect back to wherever the user came from
    if return_to == 'po':
        return redirect(url_for('transaction.create_order_page', prefilled_id=new_item_id))
    else:
        return redirect(url_for('transaction.transaction_in', selected_id=new_item_id))


@transaction_bp.route("/items/<int:item_id>/edit", methods=["POST"])
@login_required
def update_item(item_id):
    try:
        update_item_record(
            item_id=item_id,
            data=request.form,
            user_id=session.get("user_id"),
            username=session.get("username"),
        )
        flash("Item updated successfully.", "success")
        return redirect(url_for("index"))
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("transaction.edit_item_page", item_id=item_id))
    except Exception as e:
        flash(f"System Error: {str(e)}", "danger")
        return redirect(url_for("transaction.edit_item_page", item_id=item_id))


@transaction_bp.route("/inventory/in", methods=["POST"])
@login_required
def process_transaction_in():
    item_id = request.form.get("item_id")
    quantity = request.form.get("quantity")
    unit_price_raw = request.form.get("unit_price")
    notes = (request.form.get("notes") or "").strip()
    payload = {
        "item_id": item_id,
        "quantity": quantity,
        "unit_price": unit_price_raw,
        "notes": notes,
    }
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)

    if not notes:
        flash("Notes are required for manual stock inserts (audit trail).", "danger")
        return redirect(url_for('transaction.transaction_in'))

    if not (item_id and quantity and unit_price_raw is not None):
        flash("Missing item selection, quantity, unit cost, or notes.", "danger")
        return redirect(url_for('transaction.list_orders'))

    try:
        request_state = begin_idempotent_request(
            scope="inventory.manual_in",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=payload,
        )
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("transaction.transaction_in"))

    if request_state["state"] == "replay":
        return _redirect_from_idempotency_replay(request_state["response_body"], "transaction.list_orders")
    if request_state["state"] in {"processing", "mismatch"}:
        flash(request_state["message"], "warning")
        return redirect(url_for("transaction.transaction_in"))

    try:
        process_manual_stock_in(
            item_id=item_id,
            qty_int=int(quantity),
            unit_price=float(unit_price_raw),
            notes=notes,
            user_id=user_id,
            username=session.get("username")
        )
        response_body = {
            "redirect_to": url_for("transaction.list_orders"),
            "flash_message": f"Stock updated! Received {quantity} unit(s).",
            "flash_category": "success",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="inventory.manual_in",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=302,
            response_body=response_body,
            resource_type="inventory_manual_in",
            resource_id=int(item_id),
        )
    except ValueError as e:
        response_body = {
            "redirect_to": url_for("transaction.transaction_in"),
            "flash_message": str(e),
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="inventory.manual_in",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
        return redirect(url_for('transaction.transaction_in'))
    except Exception as e:
        response_body = {
            "redirect_to": url_for("transaction.transaction_in"),
            "flash_message": f"System Error: {str(e)}",
            "flash_category": "danger",
        }
        flash(response_body["flash_message"], response_body["flash_category"])
        finalize_idempotent_request(
            scope="inventory.manual_in",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=302,
            response_body=response_body,
        )
        return redirect(url_for('transaction.transaction_in'))

    return redirect(url_for('transaction.list_orders'))


@transaction_bp.route("/transaction/out/save", methods=["POST"])
@login_required
def save_transaction_out():
    data = request.get_json(silent=True) or {}
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request("sale.create", data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        sales_number, sale_id = record_sale(          # <── unpack tuple
            data=data,
            user_id=user_id,
            username=session.get('username')
        )
        transaction_class = str((data or {}).get("transaction_class") or "").strip().upper()
        if transaction_class == "MECHANIC_SUPPLY":
            flash("Mechanic supply transaction recorded successfully!", "success")
        elif sales_number:
            flash(f"Sale #{sales_number} recorded successfully!", "success")
        else:
            flash("Sale recorded successfully!", "success")
        response_body = {"status": "success", "sale_id": sale_id}
        finalize_idempotent_request(
            scope="sale.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="sale",
            resource_id=sale_id,
        )
        return jsonify(response_body), 200   # <── add sale_id
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope="sale.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        print(f"DATABASE ERROR: {str(e)}")
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope="sale.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/api/sales/<int:sale_id>/refund-context")
@login_required
def sale_refund_context_api(sale_id):
    try:
        return jsonify(get_sale_refund_context(sale_id))
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@transaction_bp.route("/api/sales/<int:sale_id>/refund", methods=["POST"])
@login_required
def refund_sale_api(sale_id):
    data = request.get_json(silent=True) or {}
    scope = f"sale.refund:{sale_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        result = record_sale_refund(
            sale_id=sale_id,
            data=data,
            user_id=user_id,
            username=session.get('username'),
        )
        response_body = {"status": "success", **result}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="sale_refund",
            resource_id=result.get("refund_id"),
        )
        return jsonify(response_body), 200
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/api/sales/refund-search")
@login_required
def refund_sale_search_api():
    try:
        query = (request.args.get("q") or "").strip()
        days = request.args.get("days")
        has_refundable = str(request.args.get("has_refundable") or "").strip().lower() in {"1", "true", "yes", "on"}
        limit = request.args.get("limit", 50)
        rows = search_sales_for_refund(
            query=query,
            days=days,
            has_refundable=has_refundable,
            limit=limit,
        )
        return jsonify({"rows": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# PURCHASE ORDERS
# ─────────────────────────────────────────────

@transaction_bp.route("/transaction/order")
@login_required
def create_order_page():
    return render_template("transactions/order.html", vendors=_get_active_vendors())


@transaction_bp.route("/api/vendors/<int:vendor_id>/recommended-items")
@login_required
def vendor_recommended_items_api(vendor_id):
    try:
        limit = request.args.get("limit", 5)
        rows = get_vendor_recommended_items(vendor_id=vendor_id, limit=limit)
        return jsonify({"items": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@transaction_bp.route("/transaction/order/save", methods=["POST"])
@login_required
def save_purchase_order():
    data = request.get_json(silent=True) or {}
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request("purchase_order.create", data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        po_number, po_id = create_purchase_order(
            data=data,
            user_id=user_id,
            username=session.get('username'),
            user_role=session.get('role'),
        )
        flash(f"Purchase Order {po_number} saved and logged!", "success")
        response_body = {"status": "success", "po_id": po_id}
        finalize_idempotent_request(
            scope="purchase_order.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body), 200
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope="purchase_order.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope="purchase_order.create",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/transaction/orders/list")
@login_required
def list_orders():
    orders = get_active_purchase_orders()
    completed_month_groups = get_purchase_order_archive_month_summaries("COMPLETED")
    cancelled_month_groups = get_purchase_order_archive_month_summaries("CANCELLED")

    return render_template(
        "transactions/order_overview.html",
        orders=orders,
        completed_month_groups=completed_month_groups,
        cancelled_month_groups=cancelled_month_groups,
        completed_total_count=sum(group["order_count"] for group in completed_month_groups),
        cancelled_total_count=sum(group["order_count"] for group in cancelled_month_groups),
    )


@transaction_bp.route("/transaction/order/<int:po_id>/review")
@admin_required
def review_purchase_order(po_id):
    context = get_purchase_order_review_context(
        po_id,
        current_user_id=session.get("user_id"),
        current_role=session.get("role"),
    )
    if not context:
        flash("Purchase order not found.", "danger")
        return redirect(url_for("transaction.list_orders"))
    return render_template("order/review.html", **context)


@transaction_bp.route("/api/order/<int:po_id>")
@login_required
def get_order_details(po_id):
    details = get_purchase_order_details(
        po_id,
        current_user_id=session.get("user_id"),
        current_role=session.get("role"),
    )
    if not details:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(details)


@transaction_bp.route("/api/orders/search")
@login_required
def search_orders():
    query = (request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"orders": []})

    limit_raw = request.args.get("limit", 20)
    try:
        limit = int(limit_raw)
    except (TypeError, ValueError):
        limit = 20

    limit = max(1, min(limit, 20))
    return jsonify({"orders": search_purchase_orders(query, limit=limit)})


@transaction_bp.route("/api/orders/archive-month")
@login_required
def get_archive_month_orders():
    status = (request.args.get("status") or "").strip().upper()
    month_key = (request.args.get("month") or "").strip()
    try:
        orders = get_purchase_orders_by_archive_month(status, month_key)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"orders": orders})


@transaction_bp.route("/api/order/<int:po_id>/update", methods=["POST"])
@login_required
def update_order(po_id):
    data = request.get_json(silent=True) or {}
    scope = f"purchase_order.update:{po_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        details = update_purchase_order(
            po_id=po_id,
            data=data,
            user_id=user_id,
            username=session.get("username"),
            user_role=session.get("role"),
        )
        flash("Purchase order updated and resubmitted.", "success")
        response_body = {"status": "success", "details": details}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/api/order/<int:po_id>/cancel", methods=["POST"])
@login_required
def cancel_order(po_id):
    data = request.get_json(silent=True) or {}
    scope = f"purchase_order.cancel:{po_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        details = cancel_purchase_order(
            po_id=po_id,
            user_id=user_id,
            user_role=session.get("role"),
            notes=(data.get("notes") or "").strip() or None,
        )
        flash("Purchase order cancelled.", "success")
        response_body = {"status": "success", "details": details}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/api/order/<int:po_id>/approval/approve", methods=["POST"])
@admin_required
def approve_order(po_id):
    data = request.get_json(silent=True) or {}
    scope = f"purchase_order.approve:{po_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        details = approve_purchase_order(
            po_id=po_id,
            admin_user_id=user_id,
            notes=(data.get("notes") or "").strip() or None,
        )
        flash("Purchase order approved.", "success")
        response_body = {"status": "success", "details": details}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/api/order/<int:po_id>/approval/revisions", methods=["POST"])
@admin_required
def revise_order(po_id):
    data = request.get_json(silent=True) or {}
    scope = f"purchase_order.revisions:{po_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        details = request_po_revisions(
            po_id=po_id,
            admin_user_id=user_id,
            notes=(data.get("notes") or "").strip(),
            revision_items=data.get("revision_items") or [],
        )
        flash("Purchase order returned for revisions.", "success")
        response_body = {"status": "success", "details": details}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/export/purchase-order/<int:po_id>/csv")
@login_required
def export_purchase_order_csv(po_id):
    po, items = get_purchase_order_export_data(po_id)
    if not po:
        return jsonify({"error": "Order not found"}), 404

    po_data = dict(po)
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["PO Number", po_data.get("po_number") or ""])
    writer.writerow(["Vendor", po_data.get("vendor_name") or ""])
    writer.writerow(["Status", po_data.get("status") or ""])
    writer.writerow(["Created At", format_date(po_data.get("created_at"), show_time=True)])
    writer.writerow(["Received At", format_date(po_data.get("received_at"), show_time=True)])
    writer.writerow(["Total Amount", f"{float(po_data.get('total_amount') or 0):.2f}"])
    writer.writerow([])
    writer.writerow(["Item", "Purchase Mode", "Qty Ordered", "Qty Received", "Unit Cost", "Subtotal"])

    total_qty_ordered = 0
    total_qty_received = 0
    grand_total = 0.0

    for row in items:
        item = dict(row)
        qty_ordered = int(item.get("quantity_ordered") or 0)
        qty_received = int(item.get("quantity_received") or 0)
        unit_cost = float(item.get("unit_cost") or 0)
        purchase_mode = str(item.get("purchase_mode") or "PIECE").strip().upper()
        subtotal = qty_ordered * unit_cost
        total_qty_ordered += qty_ordered
        total_qty_received += qty_received
        grand_total += subtotal

        ordered_label = f"{qty_ordered} box(es)" if purchase_mode == "BOX" else f"{qty_ordered} pcs"
        received_label = f"{qty_received} box(es)" if purchase_mode == "BOX" else f"{qty_received} pcs"

        writer.writerow([
            item.get("name") or "",
            "Box-based" if purchase_mode == "BOX" else "Piece-based",
            ordered_label,
            received_label,
            f"{unit_cost:.2f}",
            f"{subtotal:.2f}",
        ])

    writer.writerow([])
    writer.writerow([
        "TOTAL",
        "",
        "",
        "",
        "",
        f"{grand_total:.2f}",
    ])

    safe_po = "".join(
        ch if ch.isalnum() or ch in ("-", "_") else "_"
        for ch in (po_data.get("po_number") or f"po_{po_id}")
    )
    filename = f"{safe_po}_{now_local().strftime('%Y%m%d')}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@transaction_bp.route("/transaction/receive/<int:po_id>")
@login_required
def receive_order_page(po_id):
    po, items = get_po_for_receive_page(po_id)

    if not po:
        flash("Purchase order not found.", "danger")
        return redirect(url_for('transaction.list_orders'))

    if po['status'] == 'COMPLETED':
        flash("This order is already completed.", "info")
        return redirect(url_for('transaction.list_orders'))
    if po['status'] not in {'PENDING', 'PARTIAL'}:
        flash("This order is not approved for receiving yet.", "warning")
        return redirect(url_for('transaction.list_orders'))

    return render_template("transactions/receive.html", po=po, items=items)


@transaction_bp.route("/transaction/receive/confirm", methods=["POST"])
@login_required
def confirm_reception():
    data = request.get_json(silent=True) or {}
    po_id = data.get('po_id')
    scope = f"purchase_order.receive:{po_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, data)
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        receive_purchase_order(
            po_id=po_id,
            received_items=data.get('items'),
            user_id=user_id,
            username=session.get('username')
        )
        flash("Stock received and added successfully!", "success")
        response_body = {"status": "success"}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="purchase_order",
            resource_id=po_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@transaction_bp.route("/purchase-order/details/<int:po_id>")
@login_required
def get_po_details(po_id):
    details = get_po_details_for_api(
        po_id,
        snapshot_at=(request.args.get("snapshot_at") or "").strip() or None,
        change_reason=(request.args.get("change_reason") or "").strip() or None,
        transaction_type=(request.args.get("transaction_type") or "").strip() or None,
    )
    if not details:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(details)
