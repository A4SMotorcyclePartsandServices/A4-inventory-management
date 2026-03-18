from flask import Blueprint, render_template, request, redirect, session, flash, url_for, jsonify, abort
from werkzeug.security import check_password_hash, generate_password_hash
from db.database import get_db
from datetime import datetime
import re
from utils.formatters import format_date, norm_text
from services.audit_service import get_audit_trail
from services.payables_service import get_payables_audit_log
from services.sales_admin_service import get_sales_paginated
from services.transactions_service import get_sale_refund_context
from auth.utils import (
    clear_failed_login_attempts,
    ensure_authenticated_user,
    is_login_rate_limited,
    login_required,
    register_failed_login_attempt,
)

# 1. Initialize the Blueprint
auth_bp = Blueprint('auth', __name__)


def _to_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@auth_bp.before_request
def protect_admin_routes():
    if request.endpoint in {"auth.login", "auth.logout"}:
        return
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = ensure_authenticated_user()
    if not user:
        return redirect(url_for("auth.login"))

    if user["role"] != "admin":
        abort(403)

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        is_limited, retry_after = is_login_rate_limited(username)
        if is_limited:
            flash(f"Too many failed login attempts. Try again in about {retry_after // 60 + 1} minute(s).", "danger")
            return redirect(url_for("auth.login"))

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = %s",
            (username,)
        ).fetchone()
        conn.close()

        if not user or not check_password_hash(user["password_hash"], password):
            register_failed_login_attempt(username)
            flash("Invalid username or password", "danger")
            return redirect(url_for("auth.login"))
        
        if user["is_active"] == 0:
            flash("Your account has been disabled. Please contact an administrator.", "warning")
            return redirect(url_for("auth.login"))

        clear_failed_login_attempts(username)
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]

        if user["role"] == "admin":
            return redirect(url_for("auth.manage_users"))
        else:
            return redirect(url_for("index"))

    return render_template("users/login.html")

@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

@auth_bp.route("/users", methods=["GET", "POST"])
def manage_users():
    conn = get_db()
    active_tab = request.args.get("tab", "users-tab")

    # --- 1. HANDLE FORM SUBMISSION ---
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        current_admin_id = session.get("user_id") 
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            conn.execute("""
                INSERT INTO users (username, password_hash, role, created_at, created_by)
                VALUES (%s, %s, 'staff', %s, %s)
            """, (username, generate_password_hash(password), now, current_admin_id))
            conn.commit()
            flash(f"Account for {username} created successfully!", "success")
            return redirect(url_for('auth.manage_users'))
        except Exception as e:
            flash(f"Error creating user: {str(e)}", "danger")

    # --- 2. FETCH ALL USERS ---
    users = conn.execute("""
        SELECT u.id, u.username, u.role, u.created_at, u.is_active,
        creator.username AS creator_name
        FROM users u
        LEFT JOIN users creator ON u.created_by = creator.id
        ORDER BY u.created_at DESC
    """).fetchall()

    # --- 3. NEW: FETCH MECHANICS (This was the missing piece!) ---
    mechanics = conn.execute("SELECT * FROM mechanics ORDER BY name ASC").fetchall()

    services_list = conn.execute("SELECT * FROM services ORDER BY category ASC, name ASC LIMIT 20").fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM services WHERE category IS NOT NULL").fetchall()
    payment_methods = conn.execute("SELECT * FROM payment_methods ORDER BY category ASC, name ASC").fetchall()

    conn.close()

    # --- 4. FORMAT DATES before passing to template ---
    users = [
        {**dict(u), "created_at": format_date(u["created_at"], show_time=True)}
        for u in users
    ]

    # --- 5. SERVE THE PAGE ---
    return render_template("users/users.html", users=users, mechanics=mechanics, services_list=services_list, categories=categories, payment_methods=payment_methods, active_tab=active_tab)

@auth_bp.route("/users/toggle/<int:user_id>", methods=["POST"])
def toggle_user(user_id):
    conn = get_db()

    user = conn.execute(
        "SELECT role, is_active, username FROM users WHERE id = %s",
        (user_id,)
    ).fetchone()

    if not user:
        flash("User not found.", "danger")
        conn.close()
        return redirect(url_for('auth.manage_users'))

    if user['role'] == 'admin':
        flash("Administrator accounts cannot be disabled.", "danger")
        conn.close()
        return redirect(url_for('auth.manage_users'))

    was_active = user['is_active']
    new_status = 0 if was_active == 1 else 1

    conn.execute(
        "UPDATE users SET is_active = %s WHERE id = %s",
        (new_status, user_id)
    )
    conn.commit()

    # 🔔 Alerts
    if new_status == 0:
        flash(f"User {user['username']} has been disabled.", "danger")
    elif was_active == 0 and new_status == 1:
        flash(f"User {user['username']} has been re-enabled.", "warning")
    else:
        flash(f"User {user['username']} has been activated.", "success")

    conn.close()
    return redirect(url_for('auth.manage_users', tab='users-tab'))


@auth_bp.route("/mechanics/add", methods=["POST"])
def add_mechanic():
    name = request.form.get("name")
    commission = request.form.get("commission")
    phone = request.form.get("phone")
    
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO mechanics (name, commission_rate, phone, is_active) 
            VALUES (%s, %s, %s, 1)
        """, (name, commission, phone))
        conn.commit()
        flash(f"Mechanic {name} added successfully!", "success")
    except Exception as e:
        flash(f"Error adding mechanic: {str(e)}", "danger")
    finally:
        conn.close()
    
    return redirect(url_for('auth.manage_users', tab='mechanics-tab'))

@auth_bp.route("/mechanics/toggle/<int:mechanic_id>", methods=["POST"])
def toggle_mechanic(mechanic_id):
    conn = get_db()

    mechanic = conn.execute(
        "SELECT is_active, name FROM mechanics WHERE id = %s",
        (mechanic_id,)
    ).fetchone()

    if not mechanic:
        flash("Mechanic not found.", "danger")
        conn.close()
        return redirect(url_for('auth.manage_users', tab='mechanics-tab'))

    was_active = mechanic['is_active']

    # Toggle
    new_status = 0 if was_active == 1 else 1
    conn.execute(
        "UPDATE mechanics SET is_active = %s WHERE id = %s",
        (new_status, mechanic_id)
    )
    conn.commit()

    # 🔔 Alerts
    if new_status == 0:
        flash(f"Mechanic {mechanic['name']} has been disabled.", "danger")
    elif was_active == 0 and new_status == 1:
        flash(f"Mechanic {mechanic['name']} has been re-enabled.", "warning")
    else:
        flash(f"Mechanic {mechanic['name']} has been activated.", "success")

    conn.close()
    return redirect(url_for('auth.manage_users', tab='mechanics-tab'))

# --- NEW ROUTE: Get Sale Details for the Modal ---
@auth_bp.route("/sales/details/<reference_id>")
def sale_details(reference_id):
    try:
        return get_sale_refund_context(int(reference_id))
    except ValueError as e:
        return {"error": str(e)}, 404
    except Exception as e:
        return {"error": str(e)}, 500


@auth_bp.route("/audit/manual-in/<int:audit_group_id>")
def manual_in_details(audit_group_id):
    conn = get_db()
    try:
        anchor = conn.execute(
            """
            SELECT t.id, t.item_id, t.transaction_date, t.user_id, t.user_name, i.name AS item_name
            FROM inventory_transactions t
            JOIN items i ON i.id = t.item_id
            WHERE t.id = %s
              AND t.reference_type = 'MANUAL_ADJUSTMENT'
            """,
            (audit_group_id,),
        ).fetchone()

        if not anchor:
            return jsonify({"error": "Manual stock-in record not found."}), 404

        related_rows = conn.execute(
            """
            SELECT
                t.id,
                t.quantity,
                t.change_reason,
                t.unit_price,
                t.notes,
                t.transaction_date,
                t.user_name
            FROM inventory_transactions t
            WHERE t.reference_type = 'MANUAL_ADJUSTMENT'
              AND t.item_id = %s
              AND t.transaction_date = %s
              AND COALESCE(t.user_id, 0) = COALESCE(%s, 0)
            ORDER BY t.id ASC
            """,
            (anchor["item_id"], anchor["transaction_date"], anchor["user_id"]),
        ).fetchall()

        walkin_row = next((row for row in related_rows if row["change_reason"] == "WALKIN_PURCHASE"), None)
        cost_row = next((row for row in related_rows if row["change_reason"] == "COST_PER_PIECE_UPDATED"), None)

        previous_cost = None
        updated_cost = None
        if cost_row and cost_row["notes"]:
            match = re.search(r"Cost updated from ([0-9]+(?:\.[0-9]+)?) to ([0-9]+(?:\.[0-9]+)?)", str(cost_row["notes"]))
            if match:
                previous_cost = float(match.group(1))
                updated_cost = float(match.group(2))

        return jsonify({
            "item_name": anchor["item_name"],
            "transaction_date": format_date(anchor["transaction_date"], show_time=True),
            "user_name": anchor["user_name"] or "System",
            "walkin_purchase": {
                "quantity": int(walkin_row["quantity"] or 0) if walkin_row else 0,
                "unit_cost": float(walkin_row["unit_price"] or 0) if walkin_row else 0,
                "notes": walkin_row["notes"] if walkin_row else "",
            } if walkin_row else None,
            "cost_update": {
                "unit_cost": float(cost_row["unit_price"] or 0) if cost_row else 0,
                "previous_cost": previous_cost,
                "updated_cost": updated_cost,
                "notes": cost_row["notes"] if cost_row else "",
            } if cost_row else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@auth_bp.route("/services/add", methods=["POST"])
def add_service():
    name = request.form.get("name", "").strip()
    existing_cat = request.form.get("existing_category")
    new_cat = request.form.get("new_category", "").strip()

    # --- CATEGORY LOGIC ---
    if existing_cat == "__OTHER__" and new_cat:
        conn = get_db()
        # Normalization: Check if what they typed exists in another casing
        match = conn.execute(
            "SELECT category FROM services WHERE LOWER(TRIM(category)) = %s LIMIT 1",
            (new_cat.lower(),)
        ).fetchone()
        category = match['category'] if match else new_cat
    else:
        # Fallback sequence: Selected Dropdown -> "Labor" if empty/invalid
        category = existing_cat if existing_cat and existing_cat != "__OTHER__" else "Labor"

    # --- DUPLICATE SERVICE CHECK ---
    conn = get_db()
    existing_service = conn.execute(
        "SELECT name FROM services WHERE LOWER(TRIM(name)) = %s LIMIT 1",
        (name.lower(),)
    ).fetchone()

    if existing_service:
        flash(f"Service '{name}' already exists!", "warning")
        conn.close()
        return redirect(url_for('auth.manage_users', tab='manage-services-tab'))

    # --- SAVE ---
    try:
        conn.execute(
            "INSERT INTO services (name, category, is_active) VALUES (%s, %s, 1)",
            (name, category)
        )
        conn.commit()
        flash(f"Success: '{name}' added to '{category}'.", "success")
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('auth.manage_users', tab='manage-services-tab'))

# NEW ROUTE: Toggle Service Status
@auth_bp.route("/services/toggle/<int:service_id>", methods=["POST"])
def toggle_service(service_id):
    conn = get_db()
    service = conn.execute("SELECT is_active, name FROM services WHERE id = %s", (service_id,)).fetchone()
    if service:
        new_status = 0 if service['is_active'] == 1 else 1
        conn.execute("UPDATE services SET is_active = %s WHERE id = %s", (new_status, service_id))
        conn.commit()
        flash(f"Service '{service['name']}' status updated.", "info")
    conn.close()
    return redirect(url_for('auth.manage_users', tab='manage-services-tab'))

@auth_bp.route("/payment-methods/add", methods=["POST"])
def add_payment_method():
    name = norm_text(request.form.get("name"))
    category = norm_text(request.form.get("category"))

    # If you removed Others from the UI, keep it out here too.
    ALLOWED_PM_CATEGORIES = {"Bank", "Cash", "Debt", "Online"}

    if not name or not category:
        flash("Payment method name and category are required.", "danger")
        return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

    if category not in ALLOWED_PM_CATEGORIES:
        flash("Invalid payment method category.", "danger")
        return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

    conn = get_db()

    existing = conn.execute(
        "SELECT id FROM payment_methods WHERE LOWER(TRIM(name)) = %s",
        (name.lower(),)
    ).fetchone()

    if existing:
        flash(f"Payment method '{name}' already exists.", "warning")
        conn.close()
        return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

    try:
        conn.execute(
            "INSERT INTO payment_methods (name, category, is_active) VALUES (%s, %s, 1)",
            (name, category)
        )
        conn.commit()

        # ⚠ FUTURE NOTE:
        # When we add multi-branch support,
        # add branch_id INTEGER to payment_methods and filter by it.
        # No structural rewrite needed.

        flash(f"Payment method '{name}' added successfully.", "success")

    except Exception as e:
        flash(f"Error adding payment method: {str(e)}", "danger")
    finally:
        conn.close()

    return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

@auth_bp.route("/payment-methods/toggle/<int:pm_id>", methods=["POST"])
def toggle_payment_method(pm_id):
    conn = get_db()

    pm = conn.execute(
        "SELECT name, is_active FROM payment_methods WHERE id = %s",
        (pm_id,)
    ).fetchone()

    if not pm:
        flash("Payment method not found.", "danger")
        conn.close()
        return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

    new_status = 0 if pm['is_active'] == 1 else 1

    conn.execute(
        "UPDATE payment_methods SET is_active = %s WHERE id = %s",
        (new_status, pm_id)
    )
    conn.commit()

    if new_status == 0:
        flash(f"Payment method '{pm['name']}' disabled.", "warning")
    else:
        flash(f"Payment method '{pm['name']}' activated.", "success")

    conn.close()
    return redirect(url_for('auth.manage_users', tab='payment-methods-tab'))

@auth_bp.route("/api/audit/trail")
def audit_trail_api():
    """
    Paginated, filterable audit trail for the admin panel.
    Query params: page, start_date, end_date, type (IN/OUT/ORDER)
    """
    try:
        page          = int(request.args.get("page", 1))
        start_date    = request.args.get("start_date") or None
        end_date      = request.args.get("end_date") or None
        movement_type = request.args.get("type") or None

        # Validate type to prevent arbitrary SQL injection via the filter
        VALID_TYPES = {"IN", "OUT", "ORDER", None}
        if movement_type not in VALID_TYPES:
            return jsonify({"error": "Invalid movement type"}), 400

        has_discount = _to_bool(request.args.get("has_discount"))

        data = get_audit_trail(
            page=page,
            start_date=start_date,
            end_date=end_date,
            movement_type=movement_type,
            has_discount=has_discount,
        )
        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@auth_bp.route("/api/admin/sales")
def admin_sales_api():
    try:
        page       = int(request.args.get("page", 1))
        start_date = request.args.get("start_date") or None
        end_date   = request.args.get("end_date") or None
        search     = request.args.get("search", "").strip() or None
        payment_status = request.args.get("payment_status") or None

        valid_statuses = {"Paid", "Partial", "Unresolved", None}
        if payment_status not in valid_statuses:
            return jsonify({"error": "Invalid payment status"}), 400

        has_discount = _to_bool(request.args.get("has_discount"))

        data = get_sales_paginated(
            page=page,
            start_date=start_date,
            end_date=end_date,
            search=search,
            has_discount=has_discount,
            payment_status=payment_status,
        )
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route("/api/payables/audit")
def payables_audit_api():
    try:
        page = int(request.args.get("page", 1))
        start_date = request.args.get("start_date") or None
        end_date = request.args.get("end_date") or None
        event_type = request.args.get("event_type") or None
        source_type = request.args.get("source_type") or None
        payee_search = (request.args.get("payee_search") or "").strip() or None
        cheque_no_search = (request.args.get("cheque_no_search") or "").strip() or None

        data = get_payables_audit_log(
            page=page,
            start_date=start_date,
            end_date=end_date,
            event_type=event_type,
            source_type=source_type,
            payee_search=payee_search,
            cheque_no_search=cheque_no_search,
        )
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@auth_bp.route("/api/item/<int:item_id>")
def get_item_details(item_id):
    conn = get_db()
    try:
        item = conn.execute("""
            SELECT i.name, i.category, i.description, i.pack_size,
                vendor_price, cost_per_piece, a4s_selling_price,
                markup, reorder_level,
                COALESCE(v.vendor_name, i.vendor) AS vendor,
                i.vendor_id
            FROM items i
            LEFT JOIN vendors v ON v.id = i.vendor_id
            WHERE i.id = %s
        """, (item_id,)).fetchone()

        if not item:
            return jsonify({"error": "Item not found"}), 404

        return jsonify(dict(item))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

