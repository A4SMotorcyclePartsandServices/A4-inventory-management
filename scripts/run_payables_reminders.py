import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db.schema import init_db
from services.payables_service import run_payable_cheque_due_reminders


def _utc_now():
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _required_env_status():
    required_keys = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [key for key in required_keys if not os.environ.get(key)]
    return missing


def main():
    print(f"[payables-reminders] Starting at {_utc_now()}")
    print(f"[payables-reminders] Repo root: {REPO_ROOT}")

    missing_env = _required_env_status()
    if missing_env:
        print(
            "[payables-reminders] Missing required database env vars: "
            + ", ".join(missing_env)
        )
        raise SystemExit(1)

    try:
        init_db()
        result = run_payable_cheque_due_reminders()
        print(
            "[payables-reminders] Completed successfully. "
            f"Due in 7 days: {result.get('due_in_7_days', 0)} | "
            f"Due today: {result.get('due_today', 0)}"
        )
    except Exception:
        print(f"[payables-reminders] Failed at {_utc_now()}")
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
