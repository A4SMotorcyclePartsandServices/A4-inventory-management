import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, flash, g, request, session, redirect, url_for

from db.database import get_db

_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 5
_login_attempt_lock = threading.Lock()
_failed_login_attempts = defaultdict(deque)


def _client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _login_key(username):
    return f"{_client_ip()}::{(username or '').strip().lower()}"


def _prune_attempts(attempts, now_ts):
    while attempts and (now_ts - attempts[0]) > _LOGIN_WINDOW_SECONDS:
        attempts.popleft()


def is_login_rate_limited(username):
    now_ts = time.time()
    key = _login_key(username)
    with _login_attempt_lock:
        attempts = _failed_login_attempts[key]
        _prune_attempts(attempts, now_ts)
        if len(attempts) < _LOGIN_MAX_ATTEMPTS:
            return False, 0
        retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now_ts - attempts[0])))
        return True, retry_after


def register_failed_login_attempt(username):
    now_ts = time.time()
    key = _login_key(username)
    with _login_attempt_lock:
        attempts = _failed_login_attempts[key]
        _prune_attempts(attempts, now_ts)
        attempts.append(now_ts)


def clear_failed_login_attempts(username):
    key = _login_key(username)
    with _login_attempt_lock:
        _failed_login_attempts.pop(key, None)


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
        session.clear()
        flash("Your account has been deactivated.", "danger")
        return None

    session["username"] = user["username"]
    session["role"] = user["role"]
    session["must_change_password"] = int(user.get("must_change_password") or 0)
    g.current_user = user
    return user

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))

        user = getattr(g, "current_user", None) or ensure_authenticated_user()
        if not user:
            return redirect(url_for("auth.login"))

        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))

        user = getattr(g, "current_user", None) or ensure_authenticated_user()
        if not user:
            return redirect(url_for("auth.login"))

        if user["role"] != "admin":
            abort(403)

        return f(*args, **kwargs)
    return wrapper
