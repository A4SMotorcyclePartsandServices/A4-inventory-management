from pathlib import Path
import sys

from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.database import get_db
from db.schema import init_db


ADMINS = [
    ("admin1", "adminpass1"),
    ("admin2", "adminpass2"),
    ("admin3", "adminpass3"),
]


def main():
    # Ensure the users table exists on a fresh database.
    init_db()

    conn = get_db()
    try:
        for username, password in ADMINS:
            conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT (username) DO NOTHING
                """,
                (
                    username,
                    generate_password_hash(password),
                ),
            )

        conn.commit()
    finally:
        conn.close()

    print("Admin accounts seeded")


if __name__ == "__main__":
    main()
