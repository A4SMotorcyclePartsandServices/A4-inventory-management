from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

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

    return render_template("users/login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
