import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db.database import get_db
from db.schema import init_db


load_dotenv()

ADMIN_SEED_ENV_VAR = "ADMIN_SEED_JSON"


def _load_admin_seed_entries():
    raw_value = os.environ.get(ADMIN_SEED_ENV_VAR, "").strip()
    if not raw_value:
        raise ValueError(
            f"{ADMIN_SEED_ENV_VAR} is not set. "
            "Provide a JSON array like "
            '[{"username":"owner","password":"change-me-now"}].'
        )

    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ADMIN_SEED_ENV_VAR} must be valid JSON.") from exc

    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{ADMIN_SEED_ENV_VAR} must be a non-empty JSON array.")

    normalized_entries = []
    seen_usernames = set()

    for index, entry in enumerate(payload, start=1):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{ADMIN_SEED_ENV_VAR}[{index}] must be an object with username and password."
            )

        username = str(entry.get("username", "")).strip()
        password = str(entry.get("password", ""))

        if not username:
            raise ValueError(f"{ADMIN_SEED_ENV_VAR}[{index}].username is required.")
        if len(username) < 3:
            raise ValueError(f"{ADMIN_SEED_ENV_VAR}[{index}].username must be at least 3 characters.")
        if len(username) > 64:
            raise ValueError(f"{ADMIN_SEED_ENV_VAR}[{index}].username must be 64 characters or fewer.")

        if not password:
            raise ValueError(f"{ADMIN_SEED_ENV_VAR}[{index}].password is required.")
        if len(password) < 12:
            raise ValueError(f"{ADMIN_SEED_ENV_VAR}[{index}].password must be at least 12 characters.")

        username_key = username.lower()
        if username_key in seen_usernames:
            raise ValueError(f"Duplicate username in {ADMIN_SEED_ENV_VAR}: {username}")
        seen_usernames.add(username_key)

        normalized_entries.append({
            "username": username,
            "password": password,
        })

    return normalized_entries


def main():
    admin_entries = _load_admin_seed_entries()

    # Ensure the users table exists on a fresh database.
    init_db()

    conn = get_db()
    seeded_usernames = []

    try:
        for entry in admin_entries:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (%s, %s, 'admin')
                ON CONFLICT (username) DO NOTHING
                RETURNING username
                """,
                (
                    entry["username"],
                    generate_password_hash(entry["password"]),
                ),
            )
            inserted_row = cursor.fetchone()
            cursor.close()

            if inserted_row:
                seeded_usernames.append(inserted_row["username"])

        conn.commit()
    finally:
        conn.close()

    if seeded_usernames:
        print(f"Admin accounts seeded: {', '.join(seeded_usernames)}")
    else:
        print("No admin accounts were created. Usernames may already exist.")


if __name__ == "__main__":
    main()
