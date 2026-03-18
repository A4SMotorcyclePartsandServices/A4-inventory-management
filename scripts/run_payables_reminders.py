from app import app
from services.payables_service import run_payable_cheque_due_reminders


def main():
    with app.app_context():
        result = run_payable_cheque_due_reminders()
        print(
            "Payable cheque reminders complete. "
            f"Due in 7 days: {result.get('due_in_7_days', 0)} | "
            f"Due today: {result.get('due_today', 0)}"
        )


if __name__ == "__main__":
    main()
