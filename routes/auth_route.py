import secrets

from flask import Blueprint, current_app, flash, make_response, redirect, render_template, request, session, url_for

from auth.utils import (
    clear_failed_login_attempts,
    ensure_authenticated_user,
    is_login_rate_limited,
    login_required,
    purge_old_login_attempts,
    register_failed_login_attempt,
)
from services.idempotency_service import (
    COMPLETED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)
from services.auth_service import authenticate_user

auth_bp = Blueprint("auth", __name__)


def _auth_trace(event, **details):
    payload = {
        "event": event,
        "endpoint": request.endpoint,
        "method": request.method,
        "path": request.path,
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "session_role": session.get("role"),
        "remote_addr": request.remote_addr,
        "referer": request.headers.get("Referer"),
        "user_agent": request.user_agent.string[:200] if request.user_agent and request.user_agent.string else None,
    }
    payload.update(details)
    current_app.logger.warning("AUTH_TRACE %s", payload)


def _start_authenticated_session(user):
    csrf_token = session.get("csrf_token")
    session.clear()
    session.permanent = True
    if csrf_token:
        # Preserve the form token so a duplicate in-flight login POST
        # from the same page does not fail with a missing CSRF session token.
        session["csrf_token"] = csrf_token
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["must_change_password"] = int(user.get("must_change_password") or 0)


def _login_redirect_target(user):
    return (
        url_for("password_reset.change_password")
        if int(user.get("must_change_password") or 0) == 1
        else (url_for("users_panel.users_panel") if user["role"] == "admin" else url_for("index"))
    )


def _login_replay_redirect(response_body, user):
    _start_authenticated_session(user)
    redirect_to = response_body.get("redirect_to") or _login_redirect_target(user)
    return redirect(redirect_to)


def _begin_login_idempotency(user, username):
    idempotency_key = extract_idempotency_key(request)
    if not idempotency_key:
        return "", None

    request_payload = {
        "username": (username or "").strip().lower(),
        "target": _login_redirect_target(user),
    }
    request_state = begin_idempotent_request(
        scope="auth.login",
        actor_user_id=user["id"],
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    return idempotency_key, request_state


def _render_login_page():
    response = make_response(render_template("users/login.html"))
    # Mobile browsers can reuse a backgrounded login page for a long time.
    # Mark it non-cacheable so returning to /login fetches a fresh CSRF token.
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and "user_id" in session:
        user = ensure_authenticated_user()
        if user:
            if int(user.get("must_change_password") or 0) == 1:
                return redirect(url_for("password_reset.change_password"))
            if user["role"] == "admin":
                return redirect(url_for("users_panel.users_panel"))
            return redirect(url_for("index"))

    if request.method == "POST":
        purge_old_login_attempts()
        username = request.form["username"]
        password = request.form["password"]

        is_limited, retry_after = is_login_rate_limited(username)
        if is_limited:
            flash(
                f"Too many failed login attempts. Try again in about {retry_after // 60 + 1} minute(s).",
                "danger",
            )
            return redirect(url_for("auth.login"))

        user = authenticate_user(username, password)
        if not user:
            register_failed_login_attempt(username)
            flash("Invalid username or password", "danger")
            return redirect(url_for("auth.login"))

        if user["is_active"] == 0:
            flash("Your account has been disabled. Please contact an administrator.", "warning")
            return redirect(url_for("auth.login"))

        try:
            idempotency_key, request_state = _begin_login_idempotency(user, username)
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("auth.login"))

        if request_state:
            if request_state["state"] == "replay":
                return _login_replay_redirect(request_state["response_body"], user)
            if request_state["state"] in {"processing", "mismatch"}:
                flash(request_state["message"], "warning")
                return redirect(url_for("auth.login"))

        clear_failed_login_attempts(username)
        _start_authenticated_session(user)

        login_target = _login_redirect_target(user)
        current_app.logger.warning(
            "AUTH_TRACE %s",
            {
                "event": "login_success",
                "endpoint": request.endpoint,
                "method": request.method,
                "path": request.path,
                "user_id": user["id"],
                "username": user["username"],
                "role": user["role"],
                "must_change_password": int(user.get("must_change_password") or 0),
                "redirect_target": login_target,
                "remote_addr": request.remote_addr,
                "referer": request.headers.get("Referer"),
            },
        )

        if request_state:
            finalize_idempotent_request(
                scope="auth.login",
                actor_user_id=user["id"],
                idempotency_key=idempotency_key,
                status=COMPLETED_STATUS,
                response_code=302,
                response_body={"redirect_to": login_target},
                resource_type="user",
                resource_id=user["id"],
            )

        if int(user.get("must_change_password") or 0) == 1:
            return redirect(url_for("password_reset.change_password"))
        if user["role"] == "admin":
            return redirect(url_for("users_panel.users_panel"))
        return redirect(url_for("index"))

    return _render_login_page()


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    request_id = request.headers.get("X-Request-ID") or secrets.token_hex(4)
    user_id = session.get("user_id")
    username = session.get("username")
    role = session.get("role")
    redirect_target = url_for("auth.login")

    _auth_trace(
        "logout_attempt",
        request_id=request_id,
        redirect_target=redirect_target,
    )
    _auth_trace(
        "logout_success",
        request_id=request_id,
        cleared_user_id=user_id,
        cleared_username=username,
        cleared_role=role,
        redirect_target=redirect_target,
    )
    session.clear()
    response = redirect(redirect_target)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@auth_bp.route("/auth/client-signal", methods=["GET"])
@login_required
def client_signal():
    event = (request.args.get("event") or "").strip().lower()
    path = (request.args.get("path") or "").strip()[:160]
    nav_type = (request.args.get("nav_type") or "").strip().lower()[:32]
    visibility_state = (request.args.get("visibility") or "").strip().lower()[:16]
    persisted = (request.args.get("persisted") or "").strip().lower() in {"1", "true", "yes"}
    hidden_ms_raw = (request.args.get("hidden_ms") or "").strip()
    cache_hint = (request.args.get("cache_hint") or "").strip().lower()[:32]

    try:
        hidden_ms = max(0, min(int(hidden_ms_raw), 86_400_000)) if hidden_ms_raw else None
    except ValueError:
        hidden_ms = None

    _auth_trace(
        "client_restore_signal",
        signal_event=event,
        signal_path=path or request.headers.get("Referer"),
        nav_type=nav_type or None,
        persisted=persisted,
        visibility_state=visibility_state or None,
        hidden_ms=hidden_ms,
        cache_hint=cache_hint or None,
    )

    response = make_response("", 204)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response
