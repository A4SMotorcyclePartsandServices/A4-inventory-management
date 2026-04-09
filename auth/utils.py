import time
from functools import wraps

from flask import abort, current_app, flash, g, request, session, redirect, url_for, jsonify

from db.database import get_db

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_RETENTION_SECONDS = 5 * 24 * 60 * 60


def _auth_log(event, **details):
    app = current_app._get_current_object() if current_app else None
    if not app:
        return

    payload = {
        "event": event,
        "endpoint": request.endpoint,
        "method": request.method,
        "path": request.path,
        "user_id": session.get("user_id"),
        "session_role": session.get("role"),
        "remote_addr": request.remote_addr,
    }
    payload.update(details)
    app.logger.warning("AUTH_TRACE %s", payload)


def _client_ip():
    return request.remote_addr or "unknown"


def _login_key(username):
    return f"{_client_ip()}::{(username or '').strip().lower()}"


def _normalized_username(username):
    return (username or "").strip().lower()


def purge_old_login_attempts():
    conn = get_db()
    try:
        conn.execute(
            """
            DELETE FROM login_attempts
            WHERE attempted_at < (NOW() - (%s * INTERVAL '1 second'))
            """,
            (_LOGIN_RETENTION_SECONDS,),
        )
        conn.commit()
    finally:
        conn.close()


def is_login_rate_limited(username):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS attempt_count,
                MIN(attempted_at) AS oldest_attempt
            FROM login_attempts
            WHERE username_normalized = %s
              AND ip_address = %s
              AND attempted_at >= (NOW() - (%s * INTERVAL '1 second'))
            """,
            (_normalized_username(username), _client_ip(), _LOGIN_WINDOW_SECONDS),
        ).fetchone()
    finally:
        conn.close()

    attempt_count = int(row["attempt_count"] or 0)
    if attempt_count < _LOGIN_MAX_ATTEMPTS:
        return False, 0

    oldest_attempt = row["oldest_attempt"]
    if not oldest_attempt:
        return True, _LOGIN_WINDOW_SECONDS

    retry_after = max(
        1,
        int(_LOGIN_WINDOW_SECONDS - (time.time() - oldest_attempt.timestamp())),
    )
    return True, retry_after


def register_failed_login_attempt(username):
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO login_attempts (username_normalized, ip_address)
            VALUES (%s, %s)
            """,
            (_normalized_username(username), _client_ip()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_failed_login_attempts(username):
    conn = get_db()
    try:
        conn.execute(
            """
            DELETE FROM login_attempts
            WHERE username_normalized = %s
              AND ip_address = %s
            """,
            (_normalized_username(username), _client_ip()),
        )
        conn.commit()
    finally:
        conn.close()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db()
    try:
        user = conn.execute(
            """
            SELECT id, username, role, is_active, COALESCE(must_change_password, 0) AS must_change_password
            FROM users
            WHERE id = %s
            """,
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    return dict(user) if user else None


def ensure_authenticated_user():
    user = get_current_user()
    if not user or user["is_active"] == 0:
        _auth_log(
            "ensure_authenticated_user_failed",
            db_user_found=bool(user),
            db_user_active=bool(user and user["is_active"] != 0),
        )
        session.clear()
        flash("Your account has been deactivated.", "danger")
        return None

    previous_role = session.get("role")
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["must_change_password"] = int(user.get("must_change_password") or 0)
    g.current_user = user
    if previous_role and previous_role != user["role"]:
        _auth_log(
            "session_role_refreshed",
            previous_role=previous_role,
            db_role=user["role"],
        )
    return user

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            _auth_log("login_required_missing_session")
            return redirect(url_for("auth.login"))

        user = getattr(g, "current_user", None) or ensure_authenticated_user()
        if not user:
            _auth_log("login_required_user_resolution_failed")
            return redirect(url_for("auth.login"))

        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            _auth_log("admin_required_missing_session")
            return redirect(url_for("auth.login"))

        user = getattr(g, "current_user", None) or ensure_authenticated_user()
        if not user:
            _auth_log("admin_required_user_resolution_failed")
            return redirect(url_for("auth.login"))

        if user["role"] != "admin":
            _auth_log(
                "admin_required_forbidden",
                resolved_user_id=user.get("id"),
                resolved_role=user.get("role"),
            )
            abort(403)

        return f(*args, **kwargs)
    return wrapper


def _is_api_request():
    path = request.path or ""
    if path.startswith("/api/"):
        return True
    accept = request.accept_mimetypes.best or ""
    return accept == "application/json" or request.is_json


def stocktake_access_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))

        user = getattr(g, "current_user", None) or ensure_authenticated_user()
        if not user:
            return redirect(url_for("auth.login"))

        if user["role"] == "admin":
            return f(*args, **kwargs)

        from services.stocktake_access_service import user_has_active_stocktake_access

        if user_has_active_stocktake_access(user["id"], user_role=user["role"]):
            return f(*args, **kwargs)

        message = "Stocktake access requires admin approval. Use the Inventory menu to submit a request."
        if _is_api_request():
            return jsonify({"status": "error", "message": message}), 403

        flash(message, "warning")
        return redirect(url_for("index"))

    return wrapper
