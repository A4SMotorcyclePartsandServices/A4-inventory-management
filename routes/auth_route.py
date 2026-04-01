from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from auth.utils import (
    clear_failed_login_attempts,
    ensure_authenticated_user,
    is_login_rate_limited,
    login_required,
    purge_old_login_attempts,
    register_failed_login_attempt,
)
from services.auth_service import authenticate_user

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET" and "user_id" in session:
        user = ensure_authenticated_user()
        if user:
            if int(user.get("must_change_password") or 0) == 1:
                return redirect(url_for("password_reset.change_password"))
            if user["role"] == "admin":
                return redirect(url_for("admin_users.manage_users"))
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

        clear_failed_login_attempts(username)
        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["role"] = user["role"]
        session["must_change_password"] = int(user.get("must_change_password") or 0)

        if int(user.get("must_change_password") or 0) == 1:
            return redirect(url_for("password_reset.change_password"))
        if user["role"] == "admin":
            return redirect(url_for("admin_users.manage_users"))
        return redirect(url_for("index"))

    return render_template("users/login.html")


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
