from flask import Blueprint, render_template, request, jsonify, session
from datetime import date as date_today, timedelta
from utils.formatters import format_date
from services.cash_service import (
    get_cash_summary,
    get_cash_entries,
    get_cash_entry_count,
    get_already_paid_mechanic_identifiers_for_dates,
    add_cash_entry,
    delete_cash_entry,
    CASH_IN_CATEGORIES,
    CASH_OUT_CATEGORIES,
)
from services.reports_service import get_mechanic_payouts_for_dates

cash_bp = Blueprint('cash', __name__)
LEDGER_PAGE_SIZE = 20
REMINDER_DAYS_DEFAULT = 7
REMINDER_DAYS_MAX = 30


# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def _get_branch_id():
    """
    Central branch resolution.
    Today: always returns 1 (single branch).
    Future: return session.get('branch_id') once multi-branch is live.
    """
    return 1


# ─────────────────────────────────────────────
# PAGE ROUTE
# ─────────────────────────────────────────────

@cash_bp.route("/cash-ledger")
def cash_ledger():
    branch_id  = _get_branch_id()
    entry_type = request.args.get("type") or None
    start_date = request.args.get("start_date") or None
    end_date   = request.args.get("end_date") or None

    if entry_type not in {"CASH_IN", "CASH_OUT", None}:
        entry_type = None

    total_entries = get_cash_entry_count(
        branch_id=branch_id,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
    )
    total_pages = max(1, (total_entries + LEDGER_PAGE_SIZE - 1) // LEDGER_PAGE_SIZE)

    page   = request.args.get("page", default=1, type=int) or 1
    page   = max(1, min(page, total_pages))
    offset = (page - 1) * LEDGER_PAGE_SIZE

    summary = get_cash_summary(branch_id=branch_id)
    entries = get_cash_entries(
        branch_id=branch_id,
        limit=LEDGER_PAGE_SIZE,
        offset=offset,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
    )

    start_entry = offset + 1 if total_entries else 0
    end_entry   = offset + len(entries)

    # --- Mechanic Payout Panel ---
    today = date_today.today().isoformat()

    # --- Missed mechanic payouts for the past N days (quick reminder) ---
    reminder_days = request.args.get("reminder_days", default=REMINDER_DAYS_DEFAULT, type=int) or REMINDER_DAYS_DEFAULT
    reminder_days = max(1, min(REMINDER_DAYS_MAX, reminder_days))

    reminder_dates = [
        (date_today.today() - timedelta(days=days_ago)).isoformat()
        for days_ago in range(1, reminder_days + 1)
    ]
    payout_dates = [today] + reminder_dates
    payouts_by_date = get_mechanic_payouts_for_dates(payout_dates)
    paid_by_date = get_already_paid_mechanic_identifiers_for_dates(payout_dates, branch_id=branch_id)

    mechanic_payouts = payouts_by_date.get(today, [])
    paid_today = paid_by_date.get(today, {"mechanic_ids": set(), "mechanic_names": set()})
    already_paid_ids    = paid_today.get("mechanic_ids", set())
    already_paid_names  = paid_today.get("mechanic_names", set())

    # Filter out mechanics already paid today
    pending_payouts = [
        m for m in mechanic_payouts
        if not (
            (m.get('mechanic_id') and m['mechanic_id'] in already_paid_ids)
            or (m.get('mechanic_name') in already_paid_names)
        )
    ]

    overdue_payout_groups = []
    for payout_date in reminder_dates:
        mechanic_payouts_for_date = payouts_by_date.get(payout_date, [])
        paid_for_date = paid_by_date.get(
            payout_date,
            {"mechanic_ids": set(), "mechanic_names": set()},
        )
        paid_ids            = paid_for_date.get("mechanic_ids", set())
        paid_names          = paid_for_date.get("mechanic_names", set())

        unpaid_for_date = [
            m for m in mechanic_payouts_for_date
            if not (
                (m.get('mechanic_id') and m['mechanic_id'] in paid_ids)
                or (m.get('mechanic_name') in paid_names)
            )
        ]

        overdue_payout_groups.append({
            "date": payout_date,
            "date_display": format_date(payout_date),
            "overdue_payouts": unpaid_for_date,
            "count": len(unpaid_for_date),
        })

    total_overdue_payouts = sum(group["count"] for group in overdue_payout_groups)

    return render_template(
        "cash/cash_ledger.html",
        summary=summary,
        entries=entries,
        page=page,
        total_entries=total_entries,
        total_pages=total_pages,
        start_entry=start_entry,
        end_entry=end_entry,
        selected_type=entry_type,
        selected_start_date=start_date,
        selected_end_date=end_date,
        cash_in_categories=CASH_IN_CATEGORIES,
        cash_out_categories=CASH_OUT_CATEGORIES,
        pending_payouts=pending_payouts,
        today_display=format_date(today),
        today=today,
        overdue_payout_groups=overdue_payout_groups,
        overdue_payout_total=total_overdue_payouts,
        reminder_days=reminder_days,
        overdue_payouts=[],
        overdue_date=None,
    )


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

@cash_bp.route("/api/cash/summary")
def cash_summary_api():
    branch_id = _get_branch_id()
    summary   = get_cash_summary(branch_id=branch_id)
    return jsonify(summary)


@cash_bp.route("/api/cash/entries")
def cash_entries_api():
    branch_id  = _get_branch_id()
    limit      = request.args.get("limit", type=int)
    offset     = request.args.get("offset", type=int)
    entry_type = request.args.get("type") or None
    start_date = request.args.get("start_date") or None
    end_date   = request.args.get("end_date") or None

    entries = get_cash_entries(
        branch_id=branch_id,
        limit=limit,
        offset=offset,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
    )
    return jsonify({"entries": entries})


@cash_bp.route("/api/cash/add", methods=["POST"])
def cash_add_api():
    data = request.get_json()
    reference_id = data.get("reference_id")
    if reference_id in ("", None):
        reference_id = None
    payout_for_date = data.get("payout_for_date")

    try:
        add_cash_entry(
            entry_type=data.get("entry_type"),
            amount=data.get("amount"),
            category=data.get("category"),
            description=data.get("description", ""),
            reference_id=reference_id,
            payout_for_date=payout_for_date,
            user_id=session.get("user_id"),
            branch_id=_get_branch_id(),
        )
        return jsonify({"status": "success"}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500


@cash_bp.route("/api/cash/delete/<int:entry_id>", methods=["DELETE"])
def cash_delete_api(entry_id):
    if session.get("role") != "admin":
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    try:
        delete_cash_entry(entry_id=entry_id, branch_id=_get_branch_id())
        return jsonify({"status": "success"}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500

