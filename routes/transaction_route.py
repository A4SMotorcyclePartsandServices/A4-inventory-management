from flask import Blueprint, render_template, request, redirect, session, url_for, flash, jsonify
from services.inventory_service import get_unique_categories
from services.transactions_service import (
    add_item_to_db,
    normalize_item_category,
    get_transaction_out_context,
    process_manual_stock_in,
    record_sale,
    create_purchase_order,
    get_all_purchase_orders,
    get_purchase_order_with_items,
    get_po_for_receive_page,
    receive_purchase_order,
    get_po_details_for_api,
)

transaction_bp = Blueprint('transaction', __name__)


@transaction_bp.route("/transaction/out")
def transaction_out():
    context = get_transaction_out_context()
    return render_template("transactions/out.html", **context)


@transaction_bp.route("/transaction/in")
def transaction_in():
    prefilled_id = request.args.get('selected_id')
    return render_template("transactions/in.html", prefilled_id=prefilled_id)


@transaction_bp.route("/transaction/items")
def manage_items():
    categories = get_unique_categories()
    return_to = request.args.get('return_to', 'in')
    return render_template("transactions/items.html", categories=categories, return_to=return_to)


@transaction_bp.route("/items/add", methods=["POST"])
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
        'reorder_level': request.form.get("reorder_level") or 0,
        'vendor': request.form.get("vendor"),
        'mechanic': request.form.get("mechanic")
    }

    new_item_id = add_item_to_db(form_data, user_id=session.get('user_id'), username=session.get('username'))

    # Redirect back to wherever the user came from
    if return_to == 'po':
        return redirect(url_for('transaction.create_order_page', prefilled_id=new_item_id))
    else:
        return redirect(url_for('transaction.transaction_in', selected_id=new_item_id))


@transaction_bp.route("/inventory/in", methods=["POST"])
def process_transaction_in():
    item_id = request.form.get("item_id")
    quantity = request.form.get("quantity")
    unit_price_raw = request.form.get("unit_price")
    notes = (request.form.get("notes") or "").strip()

    if not notes:
        flash("Notes are required for manual stock inserts (audit trail).", "danger")
        return redirect(url_for('transaction.transaction_in'))

    if not (item_id and quantity and unit_price_raw is not None):
        flash("Missing item selection, quantity, unit cost, or notes.", "danger")
        return redirect(url_for('transaction.list_orders'))

    try:
        process_manual_stock_in(
            item_id=item_id,
            qty_int=int(quantity),
            unit_price=float(unit_price_raw),
            notes=notes,
            user_id=session.get("user_id"),
            username=session.get("username")
        )
        flash(f"Stock updated! Received {quantity} unit(s).", "success")
    except ValueError as e:
        flash(str(e), "danger")
    except Exception as e:
        flash(f"System Error: {str(e)}", "danger")

    return redirect(url_for('transaction.list_orders'))


@transaction_bp.route("/transaction/out/save", methods=["POST"])
def save_transaction_out():
    data = request.get_json()
    try:
        sales_number, sale_id = record_sale(          # <── unpack tuple
            data=data,
            user_id=session.get('user_id'),
            username=session.get('username')
        )
        flash(f"Sale #{sales_number} recorded successfully!", "success")
        return jsonify({"status": "success", "sale_id": sale_id}), 200   # <── add sale_id
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        print(f"DATABASE ERROR: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ─────────────────────────────────────────────
# PURCHASE ORDERS
# ─────────────────────────────────────────────

@transaction_bp.route("/transaction/order")
def create_order_page():
    return render_template("transactions/order.html")


@transaction_bp.route("/transaction/order/save", methods=["POST"])
def save_purchase_order():
    data = request.get_json()
    try:
        po_number, po_id = create_purchase_order(
            data=data,
            user_id=session.get('user_id'),
            username=session.get('username')
        )
        flash(f"Purchase Order {po_number} saved and logged!", "success")
        return jsonify({"status": "success", "po_id": po_id}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@transaction_bp.route("/transaction/orders/list")
def list_orders():
    orders = get_all_purchase_orders()
    return render_template("transactions/order_overview.html", orders=orders)


@transaction_bp.route("/api/order/<int:po_id>")
def get_order_details(po_id):
    po, items = get_purchase_order_with_items(po_id)
    return jsonify({
        "po": dict(po),
        "items": [dict(ix) for ix in items]
    })


@transaction_bp.route("/transaction/receive/<int:po_id>")
def receive_order_page(po_id):
    po, items = get_po_for_receive_page(po_id)

    if not po:
        flash("Purchase order not found.", "danger")
        return redirect(url_for('transaction.list_orders'))

    if po['status'] == 'COMPLETED':
        flash("This order is already completed.", "info")
        return redirect(url_for('transaction.list_orders'))

    return render_template("transactions/receive.html", po=po, items=items)


@transaction_bp.route("/transaction/receive/confirm", methods=["POST"])
def confirm_reception():
    data = request.get_json()
    try:
        receive_purchase_order(
            po_id=data.get('po_id'),
            received_items=data.get('items'),
            user_id=session.get('user_id'),
            username=session.get('username')
        )
        flash("Stock received and added successfully!", "success")
        return jsonify({"status": "success"})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@transaction_bp.route("/purchase-order/details/<int:po_id>")
def get_po_details(po_id):
    details = get_po_details_for_api(po_id)
    if not details:
        return jsonify({"error": "Order not found"}), 404
    return jsonify(details)
