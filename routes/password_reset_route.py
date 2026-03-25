from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from auth.utils import login_required
from services.password_reset_service import (
    change_password_for_user,
    create_password_reset_request,
    user_must_change_password,
)

password_reset_bp = Blueprint("password_reset", __name__)


def _client_ip():
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


@password_reset_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        request_note = request.form.get("request_note", "").strip()
        create_password_reset_request(
            username=username,
            request_note=request_note,
            requested_by_ip=_client_ip(),
        )
        flash(
            "If this account is eligible for password recovery, an administrator has been notified.",
            "info",
        )
        return redirect(url_for("password_reset.forgot_password"))

    return render_template("users/forgot_password.html")


@password_reset_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if new_password != confirm_password:
            flash("New password and confirmation do not match.", "danger")
            return redirect(url_for("password_reset.change_password"))

        try:
            change_password_for_user(
                user_id=session.get("user_id"),
                current_password=current_password,
                new_password=new_password,
            )
            session["must_change_password"] = 0
            flash("Password updated successfully.", "success")
            return redirect(url_for("index"))
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("password_reset.change_password"))

    return render_template(
        "users/change_password.html",
        force_change=user_must_change_password(session.get("user_id")),
    )
