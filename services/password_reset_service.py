from werkzeug.security import check_password_hash, generate_password_hash

from db.database import get_db
from services.notification_service import (
    archive_notifications,
    create_notifications_for_users,
    list_active_user_ids,
)
from utils.formatters import format_date
from utils.timezone import now_local_str

PASSWORD_RESET_NOTIFICATION_TYPES = {"PASSWORD_RESET_REQUEST"}


def _now():
    return now_local_str()


def _admin_password_reset_url():
    return "/users/audit?tab=password-resets-tab"


def get_eligible_staff_user_by_username(username, external_conn=None):
    normalized_username = str(username or "").strip()
    if not normalized_username:
        return None

    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT id, username, role, is_active
            FROM users
            WHERE username = %s
            """,
            (normalized_username,),
        ).fetchone()
        if not row:
            return None

        user = dict(row)
        if user["role"] != "staff" or int(user["is_active"] or 0) != 1:
            return None
        return user
    finally:
        if not external_conn:
            conn.close()


def create_password_reset_request(username, request_note=None, requested_by_ip=None):
    eligible_user = get_eligible_staff_user_by_username(username)
    if not eligible_user:
        return {"status": "accepted", "created": False}

    conn = get_db()
    try:
        conn.execute("BEGIN")

        existing = conn.execute(
            """
            SELECT id, status, COALESCE(repeat_request_count, 0) AS repeat_request_count
            FROM password_reset_requests
            WHERE user_id = %s
              AND status = 'PENDING'
            ORDER BY requested_at DESC, id DESC
            LIMIT 1
            """,
            (eligible_user["id"],),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE password_reset_requests
                SET repeat_request_count = COALESCE(repeat_request_count, 0) + 1,
                    last_requested_at = %s
                WHERE id = %s
                """,
                (_now(), int(existing["id"])),
            )
            conn.commit()
            return {
                "status": "accepted",
                "created": False,
                "request_id": int(existing["id"]),
                "repeat_request_count": int(existing["repeat_request_count"] or 0) + 1,
            }

        request_row = conn.execute(
            """
            INSERT INTO password_reset_requests (
                username_submitted,
                user_id,
                status,
                request_note,
                requested_by_ip,
                requested_at,
                repeat_request_count,
                last_requested_at,
                handled_by,
                handled_at,
                admin_note
            )
            VALUES (%s, %s, 'PENDING', %s, %s, %s, 0, %s, NULL, NULL, NULL)
            RETURNING id
            """,
            (
                str(username or "").strip(),
                eligible_user["id"],
                str(request_note or "").strip() or None,
                str(requested_by_ip or "").strip() or None,
                _now(),
                _now(),
            ),
        ).fetchone()

        request_id = int(request_row["id"])
        admin_ids = list_active_user_ids(role="admin", external_conn=conn)
        if admin_ids:
            create_notifications_for_users(
                admin_ids,
                "PASSWORD_RESET_REQUEST",
                "Password reset requested",
                f"Staff user '{eligible_user['username']}' requested a password reset.",
                category="security",
                entity_type="password_reset_request",
                entity_id=request_id,
                action_url=_admin_password_reset_url(),
                created_by=eligible_user["id"],
                metadata={
                    "password_reset_request_id": request_id,
                    "username": eligible_user["username"],
                },
                external_conn=conn,
            )

        conn.commit()
        return {"status": "accepted", "created": True, "request_id": request_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_password_reset_requests(limit=50):
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                pr.id,
                pr.username_submitted,
                pr.user_id,
                pr.status,
                pr.request_note,
                pr.requested_by_ip,
                pr.requested_at,
                pr.repeat_request_count,
                pr.last_requested_at,
                pr.handled_by,
                pr.handled_at,
                pr.admin_note,
                u.username AS matched_username,
                u.is_active AS matched_user_is_active,
                handler.username AS handled_by_username
            FROM password_reset_requests pr
            LEFT JOIN users u ON u.id = pr.user_id
            LEFT JOIN users handler ON handler.id = pr.handled_by
            ORDER BY
                CASE WHEN pr.status = 'PENDING' THEN 0 ELSE 1 END,
                pr.requested_at DESC,
                pr.id DESC
            LIMIT %s
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()

    serialized = []
    for row in rows:
        item = dict(row)
        item["requested_at"] = format_date(item.get("requested_at"), show_time=True)
        item["last_requested_at"] = format_date(item.get("last_requested_at"), show_time=True)
        item["handled_at"] = format_date(item.get("handled_at"), show_time=True)
        item["repeat_request_count"] = int(item.get("repeat_request_count") or 0)
        item["total_request_count"] = item["repeat_request_count"] + 1
        serialized.append(item)
    return serialized


def complete_password_reset_request(request_id, temporary_password, admin_user_id, admin_note=None):
    normalized_password = str(temporary_password or "").strip()
    if len(normalized_password) < 8:
        raise ValueError("Temporary password must be at least 8 characters.")

    conn = get_db()
    try:
        conn.execute("BEGIN")

        request_row = conn.execute(
            """
            SELECT pr.id, pr.user_id, pr.status, u.username
            FROM password_reset_requests pr
            JOIN users u ON u.id = pr.user_id
            WHERE pr.id = %s
            """,
            (int(request_id),),
        ).fetchone()

        if not request_row:
            raise ValueError("Password reset request not found.")

        if request_row["status"] != "PENDING":
            raise ValueError("Only pending password reset requests can be completed.")

        conn.execute(
            """
            UPDATE users
            SET password_hash = %s,
                must_change_password = 1
            WHERE id = %s
            """,
            (generate_password_hash(normalized_password), int(request_row["user_id"])),
        )

        conn.execute(
            """
            UPDATE password_reset_requests
            SET status = 'COMPLETED',
                handled_by = %s,
                handled_at = %s,
                admin_note = %s
            WHERE id = %s
            """,
            (
                int(admin_user_id),
                _now(),
                str(admin_note or "").strip() or None,
                int(request_id),
            ),
        )

        archive_notifications(
            entity_type="password_reset_request",
            entity_id=int(request_id),
            notification_types=PASSWORD_RESET_NOTIFICATION_TYPES,
            external_conn=conn,
        )

        conn.commit()
        return {"username": request_row["username"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_password_reset_request(request_id, admin_user_id, admin_note=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")

        request_row = conn.execute(
            """
            SELECT id, status
            FROM password_reset_requests
            WHERE id = %s
            """,
            (int(request_id),),
        ).fetchone()

        if not request_row:
            raise ValueError("Password reset request not found.")

        if request_row["status"] != "PENDING":
            raise ValueError("Only pending password reset requests can be rejected.")

        conn.execute(
            """
            UPDATE password_reset_requests
            SET status = 'REJECTED',
                handled_by = %s,
                handled_at = %s,
                admin_note = %s
            WHERE id = %s
            """,
            (
                int(admin_user_id),
                _now(),
                str(admin_note or "").strip() or None,
                int(request_id),
            ),
        )

        archive_notifications(
            entity_type="password_reset_request",
            entity_id=int(request_id),
            notification_types=PASSWORD_RESET_NOTIFICATION_TYPES,
            external_conn=conn,
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def user_must_change_password(user_id):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT must_change_password
            FROM users
            WHERE id = %s
            """,
            (int(user_id),),
        ).fetchone()
        return bool(row and int(row["must_change_password"] or 0) == 1)
    finally:
        conn.close()


def change_password_for_user(user_id, current_password, new_password):
    normalized_new_password = str(new_password or "").strip()
    if len(normalized_new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        user = conn.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE id = %s
            """,
            (int(user_id),),
        ).fetchone()
        if not user:
            raise ValueError("User not found.")

        if not check_password_hash(user["password_hash"], current_password):
            raise ValueError("Current password is incorrect.")

        conn.execute(
            """
            UPDATE users
            SET password_hash = %s,
                must_change_password = 0
            WHERE id = %s
            """,
            (generate_password_hash(normalized_new_password), int(user_id)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
