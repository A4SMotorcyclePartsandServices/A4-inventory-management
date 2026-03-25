from werkzeug.security import check_password_hash

from db.database import get_db


def get_user_by_username(username):
    conn = get_db()
    try:
        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE username = %s
            """,
            (username,),
        ).fetchone()
    finally:
        conn.close()

    return dict(user) if user else None


def authenticate_user(username, password):
    user = get_user_by_username(username)
    if not user:
        return None

    if not check_password_hash(user["password_hash"], password):
        return None

    return user
