import secrets
import string

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from auth.utils import owner_required
from services.owner_service import list_admin_reset_targets, reset_admin_password

owner_bp = Blueprint("owner", __name__, url_prefix="/owner")


def _generate_temporary_password():
    alphabet = string.ascii_letters + string.digits
    return "A4-" + "".join(secrets.choice(alphabet) for _ in range(12))


@owner_bp.route("/admin-password-resets", methods=["GET", "POST"])
@owner_required
def admin_password_resets():
    generated_password = None

    if request.method == "POST":
        target_user_id = request.form.get("target_user_id")
        temporary_password = request.form.get("temporary_password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        admin_note = request.form.get("admin_note", "").strip()
        show_temporary_password = request.form.get("show_temporary_password") == "1"

        if not temporary_password:
            temporary_password = _generate_temporary_password()
            confirm_password = temporary_password
            generated_password = temporary_password

        if temporary_password != confirm_password:
            flash("Temporary password and confirmation do not match.", "danger")
            return redirect(url_for("owner.admin_password_resets"))

        try:
            result = reset_admin_password(
                target_user_id=target_user_id,
                temporary_password=temporary_password,
                owner_user_id=session.get("user_id"),
            )
            current_app.logger.warning(
                "OWNER_ADMIN_PASSWORD_RESET target_user_id=%s target_username=%s owner_user_id=%s note=%s",
                result["target_user_id"],
                result["username"],
                result["owner_user_id"],
                admin_note[:200],
            )
            if generated_password or show_temporary_password:
                flash(
                    f"Temporary password for {result['username']}: {temporary_password}",
                    "success",
                )
            else:
                flash(
                    f"Temporary password set for {result['username']}. They must change it after signing in.",
                    "success",
                )
        except ValueError as exc:
            flash(str(exc), "danger")
        except Exception as exc:
            flash(f"Error resetting admin password: {str(exc)}", "danger")

        return redirect(url_for("owner.admin_password_resets"))

    return render_template(
        "owner/admin_password_resets.html",
        admin_users=list_admin_reset_targets(),
    )
