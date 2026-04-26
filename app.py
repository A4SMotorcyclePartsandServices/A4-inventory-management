# ============================================================
# Flask app entry point
# This file should ONLY contain:
# - app creation
# - route definitions
# - wiring to services / importers
# ============================================================

import csv
import io
import os
import secrets
import time
from datetime import date, timedelta

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
import webbrowser
import threading
from utils.timezone import configure_process_timezone, today_local

configure_process_timezone()

# ------------------------
# Database & initialization
# ------------------------
from db.database import get_db
from db.schema import init_db

# ------------------------
# Services (business logic)
# ------------------------
from routes.auth_route import auth_bp
from routes.admin_audit_route import admin_audit_bp
from routes.users_panel_route import users_panel_bp
from routes.password_reset_route import password_reset_bp
from auth.utils import ensure_authenticated_user, admin_required, login_required
from services.inventory_service import attach_restock_recommendation, get_items_with_stock, search_items_with_stock
from services.transactions_service import add_transaction
from services.analytics_service import (
    get_dashboard_stats,
    get_hot_items,
    get_dead_stock,
    get_dead_stock_page,
    get_low_stock_items,
    get_low_stock_page,
    get_low_stock_page_for_item,
    get_low_stock_summary,
    get_restock_debug_items,
)
from services.sales_analytics_service import get_sales_analytics_snapshot
from services.stocktake_access_service import get_stocktake_access_state
from services.auth_session_service import AUTH_SESSION_TOKEN_KEY, revoke_auth_session

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
from routes.void_sales_route import void_sales_bp


# ============================================================
# App setup
# ============================================================
def _env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_csv_cell(value):
    """
    Neutralize spreadsheet formula execution in downloaded CSV files.
    """
    if value is None:
        return ""

    text = str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return f"'{text}"
    return text


def _is_production_environment():
    explicit_environment = (
        os.environ.get("APP_ENV")
        or os.environ.get("FLASK_ENV")
        or os.environ.get("ENVIRONMENT")
        or ""
    ).strip().lower()
    if explicit_environment in {"prod", "production"}:
        return True

    # Railway injects environment metadata in production deployments.
    return bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )


def _log_access_denied_event(event_name, error=None):
    app.logger.warning(
        "AUTH_TRACE %s",
        {
            "event": event_name,
            "endpoint": request.endpoint,
            "method": request.method,
            "path": request.path,
            "query_string": request.query_string.decode("utf-8", errors="ignore"),
            "user_id": session.get("user_id"),
            "session_role": session.get("role"),
            "remote_addr": request.remote_addr,
            "referer": request.headers.get("Referer"),
            "user_agent": request.headers.get("User-Agent"),
            "error_type": type(error).__name__ if error else None,
            "error_message": str(error) if error else None,
        },
    )


def _is_html_response(response):
    content_type = (response.content_type or "").lower()
    return content_type.startswith("text/html")


def _apply_no_store(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _should_trace_request(path):
    if not path:
        return False
    return (
        path == "/api/search"
        or path == "/transaction/out/save"
        or path.startswith("/api/sales/")
        or path.startswith("/api/stocktake/")
        or path.startswith("/reports/")
    )


def _should_trace_auth_request(path):
    if not path:
        return False
    return path in {"/login", "/logout", "/users"} or path.startswith("/users/")


app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=int(os.environ.get("TRUSTED_PROXY_FOR_COUNT", 1)),
    x_proto=int(os.environ.get("TRUSTED_PROXY_PROTO_COUNT", 1)),
    x_host=int(os.environ.get("TRUSTED_PROXY_HOST_COUNT", 1)),
)
app.config["SECRET_KEY"] = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("SECRET_KEY")
    or secrets.token_hex(32)
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = _env_flag(
    "SESSION_COOKIE_SECURE",
    default=_is_production_environment(),
)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    hours=int(os.environ.get("SESSION_LIFETIME_HOURS", 12))
)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH_MB", 16)) * 1024 * 1024
app.config["PREFERRED_URL_SCHEME"] = "https" if _is_production_environment() else "http"

csrf = CSRFProtect(app)


@app.before_request
def restrict_access():
    g.request_started_at = time.perf_counter()
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

    if session.get("user_id") and _is_html_response(response):
        _apply_no_store(response)

    started_at = getattr(g, "request_started_at", None)
    if started_at is not None and _should_trace_auth_request(request.path):
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        if request.path == "/logout" or response.status_code >= 400 or duration_ms >= 800:
            app.logger.warning(
                "AUTH_REQUEST_TRACE path=%s method=%s status=%s duration_ms=%s user_id=%s role=%s request_id=%s ua=%s referer=%s",
                request.path,
                request.method,
                response.status_code,
                duration_ms,
                session.get("user_id"),
                session.get("role"),
                request.form.get("request_id") or request.headers.get("X-Request-ID") or "",
                request.headers.get("User-Agent") or "",
                request.headers.get("Referer") or "",
            )

    if started_at is not None and _should_trace_request(request.path):
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        search_query = (request.args.get("q") or "").strip()
        item_id = (request.args.get("id") or "").strip()
        if (
            request.path == "/api/search"
            or duration_ms >= 800
            or response.status_code >= 500
        ):
            app.logger.warning(
                "REQUEST_TRACE path=%s method=%s status=%s duration_ms=%s query_len=%s item_id=%s user_id=%s",
                request.path,
                request.method,
                response.status_code,
                duration_ms,
                len(search_query),
                item_id or "",
                session.get("user_id"),
            )
    return response


@app.context_processor
def inject_globals():
    current_user = getattr(g, "current_user", None)
    return {
        "current_date": today_local().isoformat(),
        "current_user": current_user,
        "stocktake_access_state": get_stocktake_access_state(
            current_user.get("id") if current_user else None,
            user_role=current_user.get("role") if current_user else None,
        ),
    }
init_db()  # Safe to call on startup (creates tables if missing)

# Register API routes (kept separate from UI routes)
app.register_blueprint(dashboard_api)
app.register_blueprint(approval_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_audit_bp)
app.register_blueprint(users_panel_bp)
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
app.register_blueprint(void_sales_bp)


# ============================================================
# Core inventory UI
# ============================================================
@app.route("/", methods=["GET", "POST"])
@login_required
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

    conn = get_db()
    attach_restock_recommendation(conn, items_merged, item_id_key="id", category_key="category", current_stock_key="current_stock", snapshot_date="2026-03-26")
    conn.close()

    return render_template("index.html", items=items_merged)

@app.route("/api/search")
@login_required
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
@login_required
def analytics():
    """
    Fast-moving items (last 30 days).
    """
    selected_category = (request.args.get("top_items_category") or "").strip()
    try:
        selected_limit = int((request.args.get("top_items_limit") or "10").strip())
    except ValueError:
        selected_limit = 10

    hot_items_data = get_hot_items(limit=selected_limit, category=selected_category)
    return render_template(
        "fastmoving.html",
        hot_items=hot_items_data["items"],
        top_items_category=hot_items_data["selected_category"],
        top_items_limit=hot_items_data["selected_limit"],
        top_item_categories=hot_items_data["categories"],
    )


@app.route("/sales-analytics")
@admin_required
def sales_analytics():
    """
    Sales-focused analytics deep dive.
    """
    today = today_local()
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
    top_items_category = (request.args.get("top_items_category") or "").strip()

    try:
        top_items_limit = int((request.args.get("top_items_limit") or "10").strip())
    except ValueError:
        top_items_limit = 10

    analytics_data = get_sales_analytics_snapshot(
        start_date,
        end_date,
        top_items_limit=top_items_limit,
        top_items_category=top_items_category,
    )

    return render_template(
        "sales_analytics.html",
        analytics=analytics_data,
        start_date=start_date,
        end_date=end_date,
        top_items_limit=analytics_data["filters"]["top_items_limit"],
        top_items_category=analytics_data["filters"]["top_items_category"],
    )


@app.route("/dead-stock")
@login_required
def dead_stock():
    """
    Items with no sales for a long time (or never sold).
    """
    page_raw = (request.args.get("page") or "").strip()
    try:
        page = max(1, int(page_raw or 1))
    except ValueError:
        page = 1

    dead_stock_page = get_dead_stock_page(page=page, per_page=30)
    return render_template(
        "dead_stock.html",
        dead_items=dead_stock_page["items"],
        dead_stock_page=dead_stock_page,
    )


@app.route("/low-stock")
@login_required
def low_stock():
    """
    Items at or below reorder level.
    """
    page_raw = (request.args.get("page") or "").strip()
    item_id_raw = (request.args.get("item_id") or "").strip()
    highlight_item_id = None
    if item_id_raw:
        try:
            highlight_item_id = int(item_id_raw)
        except (TypeError, ValueError):
            highlight_item_id = None

    low_stock_rows = get_low_stock_items(include_watchlist=True)

    if highlight_item_id and not page_raw:
        resolved_page = get_low_stock_page_for_item(
            highlight_item_id,
            per_page=75,
            include_watchlist=True,
            rows=low_stock_rows,
        )
        if resolved_page:
            page_raw = str(resolved_page)

    if not page_raw:
        page_raw = "1"

    low_stock_page = get_low_stock_page(
        page=page_raw,
        per_page=75,
        include_watchlist=True,
        rows=low_stock_rows,
    )
    debug_mode = False
    debug_total_count = 0
    if debug_mode:
        debug_result = get_restock_debug_items(offset=0, limit=100)
        debug_total_count = debug_result["total_count"]
    else:
        debug_result = {"items": [], "total_count": 0}
    return render_template(
        "low_stock.html",
        low_stock_items=low_stock_page["items"],
        low_stock_page=low_stock_page,
        highlight_item_id=highlight_item_id,
        debug_mode=debug_mode,
        debug_items=debug_result["items"],
        debug_total_count=debug_total_count,
    )


@app.route("/api/low-stock/summary")
@login_required
def low_stock_summary_api():
    limit_raw = (request.args.get("limit") or "8").strip()
    return jsonify(get_low_stock_summary(limit=limit_raw))


@app.route("/api/restock-debug")
@login_required
def restock_debug_api():
    abort(404)
    offset_raw = (request.args.get("offset") or "0").strip()
    limit_raw = (request.args.get("limit") or "100").strip()
    try:
        offset = max(0, int(offset_raw))
    except ValueError:
        offset = 0
    try:
        limit = max(1, min(250, int(limit_raw)))
    except ValueError:
        limit = 100

    result = get_restock_debug_items(offset=offset, limit=limit)
    serialized_items = []
    for item in result["items"]:
        row = dict(item)
        for key, value in list(row.items()):
            if hasattr(value, "isoformat"):
                row[key] = value.isoformat()
        serialized_items.append(row)
    return jsonify({"items": serialized_items, "total_count": result["total_count"]})


# ============================================================
# Item & transaction utilities
# ============================================================
@app.route("/export/transactions")
@login_required
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

    summary = (
        f"Inventory import complete. "
        f"Imported: {result['imported']}. "
        f"Skipped: {result['skipped']}. "
        f"Missing fields: {result['skip_reasons']['missing_fields']}. "
        f"Bad quantity: {result['skip_reasons']['bad_quantity']}. "
        f"Item not found: {result['skip_reasons']['item_not_found']}. "
        f"Zero or negative quantity: {result['skip_reasons']['zero_quantity']}."
    )

    skipped_rows = result.get("skipped_rows") or []
    if skipped_rows:
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=skipped_rows[0].keys())
        writer.writeheader()
        writer.writerows([
            {key: _safe_csv_cell(value) for key, value in row.items()}
            for row in skipped_rows
        ])

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=skipped_inventory_rows.csv",
                "X-Content-Type-Options": "nosniff",
                "X-Import-Message": summary,
            },
        )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"message": summary})

    return summary.replace(". ", ".<br>")


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
                    WHEN inventory_transactions.transaction_type = 'OUT'
                    THEN -inventory_transactions.quantity
                    ELSE 0
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
                    WHEN inventory_transactions.transaction_type = 'OUT'
                    THEN -inventory_transactions.quantity
                    ELSE 0
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
                WHEN inventory_transactions.transaction_type = 'OUT'
                THEN -inventory_transactions.quantity
                ELSE 0
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
    _log_access_denied_event("http_403", e)
    return render_template('errors/403.html'), 403

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.path == "/logout" and request.method == "POST":
        _log_access_denied_event("logout_csrf_error", e)
        _log_access_denied_event("csrf_error", e)
        revoke_auth_session(
            user_id=session.get("user_id"),
            token=session.get(AUTH_SESSION_TOKEN_KEY),
            reason="logout_csrf_error",
        )
        session.clear()
        flash("You have been logged out. Please sign in again.", "info")
        return _apply_no_store(redirect(url_for("auth.login")))
    _log_access_denied_event("csrf_error", e)
    if request.path == "/login" and request.method == "POST":
        flash("Your login page expired. Please sign in again.", "warning")
        return _apply_no_store(redirect(url_for("auth.login")))
    return render_template('errors/403.html'), 400

@app.errorhandler(400)
def bad_request(e):
    _log_access_denied_event("http_400", e)
    return render_template('errors/403.html'), 400

@app.errorhandler(409)
def conflict_error(e):
    description = "The record already exists."
    if isinstance(e, HTTPException) and e.description:
        description = e.description

    if request.path.startswith("/api/"):
        return jsonify({"status": "error", "message": description}), 409

    return render_template("errors/409.html", error_message=description), 409

@app.errorhandler(500)
def server_error(e):
    _log_access_denied_event("http_500", e)
    return render_template('errors/500.html'), 500

# ============================================================
# App runner
# ============================================================
def open_browser():
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()
    app.run(port=5000)

