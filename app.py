# ============================================================
# Flask app entry point
# This file should ONLY contain:
# - app creation
# - route definitions
# - wiring to services / importers
# ============================================================

import os
import secrets
from datetime import date, timedelta

from flask import Flask, Response, g, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
import webbrowser
import threading

# ------------------------
# Database & initialization
# ------------------------
from db.database import get_db
from db.schema import init_db

# ------------------------
# Services (business logic)
# ------------------------
from routes.auth_route import auth_bp
from routes.admin_users_route import admin_users_bp
from routes.password_reset_route import password_reset_bp
from auth.utils import ensure_authenticated_user, admin_required
from services.inventory_service import get_items_with_stock, search_items_with_stock
from services.transactions_service import add_transaction
from services.analytics_service import (
    get_dashboard_stats,
    get_hot_items,
    get_dead_stock,
    get_low_stock_items
)
from services.sales_analytics_service import get_sales_analytics_snapshot

# ------------------------
# Importers (CSV handling)
# ------------------------
from importers.items_importer import import_items_csv
from importers.sales_importer import import_sales_csv
from importers.inventory_importer import import_inventory_csv

# ------------------------
# API / blueprints
# ------------------------
from routes.routes_api import dashboard_api
from routes.approval_route import approval_bp
from routes.transaction_route import transaction_bp
from routes.reports_route import reports_bp
from routes.debt_route import debt_bp
from routes.cash_route import cash_bp
from routes.customer_route import customer_bp
from routes.loyalty_route import loyalty_bp
from routes.notification_route import notification_bp
from routes.vendor_route import vendor_bp
from routes.payables_route import payables_bp
from routes.stocktake_route import stocktake_bp


# ============================================================
# App setup
# ============================================================
def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


app = Flask(__name__)
app.config["SECRET_KEY"] = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("SECRET_KEY")
    or secrets.token_hex(32)
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = _env_flag("SESSION_COOKIE_SECURE", default=False)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    hours=int(os.environ.get("SESSION_LIFETIME_HOURS", 12))
)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH_MB", 16)) * 1024 * 1024

csrf = CSRFProtect(app)


@app.before_request
def restrict_access():
    public_routes = {"auth.login", "password_reset.forgot_password", "static"}

    if not request.endpoint or request.endpoint in public_routes:
        return

    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user = ensure_authenticated_user()
    if not user:
        return redirect(url_for("auth.login"))

    must_change_password = int(user.get("must_change_password") or 0) == 1
    allowed_password_reset_endpoints = {
        "password_reset.change_password",
        "auth.logout",
        "notification.notification_summary",
        "notification.notification_list",
        "notification.notification_mark_read",
        "notification.notification_mark_all_read",
        "static",
    }
    if must_change_password and request.endpoint not in allowed_password_reset_endpoints:
        return redirect(url_for("password_reset.change_password"))


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if request.is_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.context_processor
def inject_globals():
    return {
        "current_date": date.today().isoformat(),
        "current_user": getattr(g, "current_user", None),
    }
init_db()  # Safe to call on startup (creates tables if missing)

# Register API routes (kept separate from UI routes)
app.register_blueprint(dashboard_api)
app.register_blueprint(approval_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_users_bp)
app.register_blueprint(password_reset_bp)
app.register_blueprint(transaction_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(debt_bp)
app.register_blueprint(cash_bp)
app.register_blueprint(customer_bp)
app.register_blueprint(loyalty_bp)
app.register_blueprint(notification_bp)
app.register_blueprint(vendor_bp)
app.register_blueprint(payables_bp)
app.register_blueprint(stocktake_bp)


# ============================================================
# Core inventory UI
# ============================================================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        action = request.form["action"]
        item_id = request.form["item_id"]
        quantity = int(request.form["quantity"])
        
        # --- NEW AUDIT TRAIL LOGIC ---
        user_id = session.get("user_id")
        user_name = session.get("username")

        add_transaction(item_id, quantity, action, user_id=user_id, user_name=user_name)
        return redirect("/")

    conn = get_db()

    # 1️⃣ We only get the first 50 items for the initial page load
    # This keeps the "Home" page fast even with 5,000 items in the DB
    extras = conn.execute("""
        SELECT *
        FROM items
        ORDER BY id DESC
        LIMIT 75
    """).fetchall()

    # 2️⃣ Get the stock for JUST these 50 items
    # (We'll adjust your service later, but for now let's just get the list of IDs)
    item_ids = [e["id"] for e in extras]
    
    # We still use your stock service, but we'll need to pass the IDs 
    # to avoid calculating stock for 5,000 items we aren't showing.
    items_stock = get_items_with_stock(snapshot_date="2026-03-26")
    stock_dict = {s["id"]: s["current_stock"] for s in items_stock}

    conn.close()

    # 3️⃣ Merge safely
    items_merged = []
    for row in extras:
        item_data = dict(row)
        item_data["current_stock"] = stock_dict.get(row["id"], 0)
        items_merged.append(item_data)

    return render_template("index.html", items=items_merged)

@app.route("/api/search")
def search_items_api():
    query = request.args.get("q", "").strip()
    item_id = request.args.get("id") # Get the ID if it exists

    # If the browser sent an ID, use it!
    if item_id:
        results = search_items_with_stock(item_id=item_id)
    # Otherwise, do the normal text search
    elif len(query) >= 2:
        results = search_items_with_stock(search_query=query)
    else:
        results = []
    
    return {"items": results}

# ============================================================
# Analytics / reporting views
# ============================================================
@app.route("/items-analytics")
@admin_required
def items_analytics():
    """
    Inventory and item analytics overview.
    """
    (
        total_items,
        total_stock,
        low_stock_count,
        top_item,
        items
    ) = get_dashboard_stats()

    return render_template(
        "items_analytics.html",
        total_items=total_items,
        total_stock=total_stock,
        low_stock_count=low_stock_count,
        top_item=top_item,
        items=items
    )


@app.route("/analytics")
def analytics():
    """
    Fast-moving items (last 30 days).
    """
    hot_items = get_hot_items()
    return render_template("fastmoving.html", hot_items=hot_items)


@app.route("/sales-analytics")
@admin_required
def sales_analytics():
    """
    Sales-focused analytics deep dive.
    """
    today = date.today()
    default_start = today - timedelta(days=29)

    start_date = (request.args.get("start_date") or default_start.isoformat()).strip()
    end_date = (request.args.get("end_date") or today.isoformat()).strip()

    try:
        start_obj = date.fromisoformat(start_date)
        end_obj = date.fromisoformat(end_date)
    except ValueError:
        start_obj = default_start
        end_obj = today

    if end_obj < start_obj:
        start_obj, end_obj = end_obj, start_obj

    start_date = start_obj.isoformat()
    end_date = end_obj.isoformat()
    analytics_data = get_sales_analytics_snapshot(start_date, end_date)

    return render_template(
        "sales_analytics.html",
        analytics=analytics_data,
        start_date=start_date,
        end_date=end_date,
    )


@app.route("/dead-stock")
def dead_stock():
    """
    Items with no sales for a long time (or never sold).
    """
    dead_items = get_dead_stock()
    return render_template("dead_stock.html", dead_items=dead_items)


@app.route("/low-stock")
def low_stock():
    """
    Items at or below reorder level.
    """
    low_stock_items = get_low_stock_items()
    return render_template("low_stock.html", low_stock_items=low_stock_items)


# ============================================================
# Item & transaction utilities
# ============================================================
@app.route("/export/transactions")
def export_transactions():
    conn = get_db()
    rows = conn.execute("""
        SELECT 
            items.name AS item,
            inventory_transactions.transaction_type,
            inventory_transactions.quantity,
            inventory_transactions.transaction_date,
            inventory_transactions.user_name
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        ORDER BY inventory_transactions.transaction_date DESC
    """).fetchall()
    conn.close()

    def generate():
        yield "Item,Type,Quantity,Date,User\n" # Only one header!
        for row in rows:
            yield f"{row['item']},{row['transaction_type']},{row['quantity']},{row['transaction_date']},{row['user_name'] or 'System'}\n"

    return Response(generate(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=inventory_transactions.csv"})


# ============================================================
# CSV import endpoints
# ============================================================
@app.route("/import/items", methods=["POST"])
@admin_required
def import_items():
    """
    Import item master list.
    """
    success = import_items_csv(request.files.get("file"))
    if not success:
        return "Invalid file", 400
    return redirect("/")


@app.route("/import/sales", methods=["POST"])
@admin_required
def import_sales():
    """
    Import historical sales (OUT transactions).
    """
    success, result = import_sales_csv(request.files.get("file"))
    if not success:
        return result, 400

    return (
        f"Sales import complete. "
        f"Imported: {result['imported']}, "
        f"Skipped: {result['skipped']}"
    )


@app.route("/import/inventory", methods=["POST"])
@admin_required
def import_inventory():
    """
    Import physical inventory count as baseline IN transactions.
    """
    success, result = import_inventory_csv(request.files.get("file"))
    if not success:
        return result, 400

    return (
        f"Inventory import complete.<br>"
        f"Imported: {result['imported']}<br>"
        f"Skipped: {result['skipped']}<br>"
        f"Missing fields: {result['skip_reasons']['missing_fields']}<br>"
        f"Bad quantity: {result['skip_reasons']['bad_quantity']}<br>"
        f"Item not found: {result['skip_reasons']['item_not_found']}"
    )


# ============================================================
# Experimental / alternate UI
# ============================================================
@app.route("/index2", methods=["GET", "POST"])
@admin_required
def index2():
    """
    Alternate inventory UI (design experiment).
    Logic intentionally duplicated to keep risk isolated.
    """
    conn = get_db()

    if request.method == "POST":
        action = request.form["action"]
        item_id = request.form["item_id"]
        quantity = int(request.form["quantity"])
        add_transaction(item_id, quantity, action)
        conn.commit()
        return redirect("/index2")

    items = conn.execute("""
        SELECT 
            items.id,
            items.name,
            COALESCE(SUM(
                CASE 
                    WHEN inventory_transactions.transaction_type = 'IN' 
                    THEN inventory_transactions.quantity
                    ELSE -inventory_transactions.quantity
                END
            ), 0) AS current_stock
        FROM items
        LEFT JOIN inventory_transactions
            ON items.id = inventory_transactions.item_id
        GROUP BY items.id
    """).fetchall()

    conn.close()
    return render_template("index2.html", items=items)


# ============================================================
# Debug / integrity checks (temporary but intentional)
# ============================================================
@app.route("/debug-integrity")
@admin_required
def debug_integrity():
    """
    Data sanity checks during historical reconciliation.
    NOT meant for production use.
    """
    conn = get_db()

    totals = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN transaction_type = 'IN' THEN quantity ELSE 0 END), 0) AS total_in,
            COALESCE(SUM(CASE WHEN transaction_type = 'OUT' THEN quantity ELSE 0 END), 0) AS total_out
        FROM inventory_transactions
    """).fetchone()

    negative_items = conn.execute("""
        SELECT 
            items.name,
            COALESCE(SUM(
                CASE 
                    WHEN inventory_transactions.transaction_type = 'IN'
                    THEN inventory_transactions.quantity
                    ELSE -inventory_transactions.quantity
                END
            ), 0) AS current_stock
        FROM items
        LEFT JOIN inventory_transactions
            ON items.id = inventory_transactions.item_id
        GROUP BY items.id
        HAVING COALESCE(SUM(
            CASE 
                WHEN inventory_transactions.transaction_type = 'IN'
                THEN inventory_transactions.quantity
                ELSE -inventory_transactions.quantity
            END
        ), 0) < 0
    """).fetchall()

    snapshot_date = "2026-03-26"

    snapshot_check = conn.execute("""
        SELECT
            items.name,
            SUM(CASE 
                WHEN inventory_transactions.transaction_type = 'IN'
                     AND inventory_transactions.transaction_date = %s
                THEN inventory_transactions.quantity
                ELSE 0
            END) AS snapshot_qty,
            SUM(CASE
                WHEN inventory_transactions.transaction_type = 'OUT'
                     AND inventory_transactions.transaction_date >= %s
                THEN inventory_transactions.quantity
                ELSE 0
            END) AS recent_sales
        FROM items
        LEFT JOIN inventory_transactions
            ON items.id = inventory_transactions.item_id
        GROUP BY items.id
        HAVING SUM(CASE 
            WHEN inventory_transactions.transaction_type = 'IN'
                 AND inventory_transactions.transaction_date = %s
            THEN inventory_transactions.quantity
            ELSE 0
        END) > 0
    """, (snapshot_date, snapshot_date, snapshot_date)).fetchall()

    date_ranges = conn.execute("""
        SELECT
            MIN(transaction_date) AS earliest,
            MAX(transaction_date) AS latest
        FROM inventory_transactions
    """).fetchone()

    conn.close()

    return render_template(
        "debug_integrity.html",
        totals=totals,
        negative_items=negative_items,
        snapshot_check=snapshot_check,
        date_ranges=date_ranges
    )

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return render_template('errors/403.html'), 400

@app.errorhandler(400)
def bad_request(e):
    return render_template('errors/403.html'), 400

@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500

# ============================================================
# App runner
# ============================================================
def open_browser():
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    app.run(port=5000)

