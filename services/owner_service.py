from werkzeug.security import generate_password_hash

from db.database import get_db


def list_admin_reset_targets():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, username, phone_no, is_active, must_change_password, created_at
            FROM users
            WHERE role = 'admin'
            ORDER BY username ASC
            """
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


def reset_admin_password(target_user_id, temporary_password, owner_user_id):
    normalized_password = str(temporary_password or "").strip()
    if len(normalized_password) < 8:
        raise ValueError("Temporary password must be at least 8 characters.")

    try:
        target_user_id = int(target_user_id)
    except (TypeError, ValueError):
        raise ValueError("Select a valid admin account.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        target_user = conn.execute(
            """
            SELECT id, username, role, is_active
            FROM users
            WHERE id = %s
            """,
            (target_user_id,),
        ).fetchone()

        if not target_user:
            raise ValueError("Admin account not found.")
        if target_user["role"] != "admin":
            raise ValueError("Only admin accounts can be reset here.")
        if int(target_user["is_active"] or 0) != 1:
            raise ValueError("This admin account is inactive.")

        conn.execute(
            """
            UPDATE users
            SET password_hash = %s,
                must_change_password = 1
            WHERE id = %s
            """,
            (generate_password_hash(normalized_password), target_user_id),
        )
        conn.commit()
        return {
            "target_user_id": int(target_user["id"]),
            "username": target_user["username"],
            "owner_user_id": int(owner_user_id),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
