import os
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOCAL_STAFF_USERNAME = "User1"
LOCAL_STAFF_PASSWORD = "12345"


def _load_env_file():
    env_file = os.environ.get("LOCAL_REFRESH_ENV_FILE")
    if env_file:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv()


def _random_phone_no():
    return "9" + "".join(str(secrets.randbelow(10)) for _ in range(10))


def main():
    _load_env_file()

    from db.database import get_db

    conn = get_db()
    try:
        row = conn.execute(
            """
            INSERT INTO users (
                username,
                password_hash,
                phone_no,
                role,
                is_active,
                must_change_password
            )
            VALUES (%s, %s, %s, 'staff', 1, 0)
            ON CONFLICT (username) DO UPDATE
            SET
                password_hash = EXCLUDED.password_hash,
                phone_no = EXCLUDED.phone_no,
                role = 'staff',
                is_active = 1,
                must_change_password = 0
            RETURNING id, username
            """,
            (
                LOCAL_STAFF_USERNAME,
                generate_password_hash(LOCAL_STAFF_PASSWORD),
                _random_phone_no(),
            ),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO staff_access_schedules (user_id)
            VALUES (%s)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (row["id"],),
        )
        conn.commit()
    finally:
        conn.close()

    print(f"Local staff account ready: {LOCAL_STAFF_USERNAME}")


if __name__ == "__main__":
    main()
