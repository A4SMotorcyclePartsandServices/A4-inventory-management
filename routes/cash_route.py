from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for
from datetime import date as date_today, datetime, timedelta
from utils.formatters import format_date
from auth.utils import login_required
from auth.utils import admin_required
from services.cash_service import (
    get_cash_summary,
    get_cash_entries,
    get_cash_entry_count,
    get_cash_entries_for_report,
    get_cash_category_choices,
    get_pending_non_cash_collections,
    get_pending_non_cash_collection_count,
    get_already_paid_mechanic_identifiers_for_dates,
    add_cash_entry,
    delete_cash_entry,
    restore_cash_entry,
    purge_deleted_cash_entries,
)
from services.idempotency_service import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)
from services.reports_service import (
    get_mechanic_payouts_for_dates,
    get_mechanic_supply_expense_summary,
)
from utils.timezone import now_local, today_local

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


def _get_ledger_view():
    ledger_view = request.args.get("view") or "active"
    if ledger_view not in {"active", "deleted"}:
        ledger_view = "active"
    if ledger_view == "deleted" and session.get("role") != "admin":
        ledger_view = "active"
    return ledger_view


def _parse_iso_date(raw_value):
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Invalid report date.") from exc


def _resolve_report_date_range():
    start_raw = request.args.get("start_date") or None
    end_raw = request.args.get("end_date") or None

    start_date = _parse_iso_date(start_raw)
    end_date = _parse_iso_date(end_raw)

    if start_date and not end_date:
        end_date = start_date
    elif end_date and not start_date:
        start_date = end_date
    elif not start_date and not end_date:
        today = today_local()
        start_date = today.replace(day=1)
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

    if start_date > end_date:
        raise ValueError("Start date cannot be later than end date.")

    return start_date.isoformat(), end_date.isoformat(), start_date, end_date


def _resolve_ledger_date_range(default_to_current_month=False):
    start_raw = request.args.get("start_date") or None
    end_raw = request.args.get("end_date") or None

    if start_raw and not end_raw:
        end_raw = start_raw
    elif end_raw and not start_raw:
        start_raw = end_raw

    if not start_raw and not end_raw and default_to_current_month:
        today = today_local()
        month_start = today.replace(day=1)
        next_month_anchor = (month_start + timedelta(days=32)).replace(day=1)
        month_end = next_month_anchor - timedelta(days=1)
        return month_start.isoformat(), month_end.isoformat()

    return start_raw, end_raw


def _build_expense_report_groups(entries):
    grouped = {}

    for entry in entries:
        if entry.get("entry_type") != "CASH_OUT":
            continue

        category = (entry.get("category") or "Uncategorized").strip() or "Uncategorized"
        group = grouped.setdefault(
            category,
            {
                "category": category,
                "entry_count": 0,
                "total_amount": 0.0,
            },
        )
        group["entry_count"] += 1
        group["total_amount"] = round(group["total_amount"] + float(entry.get("amount") or 0), 2)

    expense_groups = sorted(grouped.values(), key=lambda group: group["category"].lower())
    expense_total = round(sum(group["total_amount"] for group in expense_groups), 2)

    return expense_groups, expense_total


def _build_cash_report_context(branch_id):
    ledger_view = _get_ledger_view()
    entry_type = request.args.get("type") or None

    if entry_type not in {"CASH_IN", "CASH_OUT", None}:
        entry_type = None

    start_date, end_date, start_date_obj, end_date_obj = _resolve_report_date_range()
    report_data = get_cash_entries_for_report(
        date_from=start_date,
        date_to=end_date,
        branch_id=branch_id,
        entry_type=entry_type,
        ledger_view=ledger_view,
    )
    summary = get_cash_summary(branch_id=branch_id)
    expense_groups, expense_total = _build_expense_report_groups(report_data["entries"])
    mechanic_supply_summary = {"transaction_count": 0, "total_amount": 0.0}
    if ledger_view != "deleted":
        mechanic_supply_summary = get_mechanic_supply_expense_summary(start_date, end_date)
        if mechanic_supply_summary["total_amount"] > 0:
            expense_groups.append({
                "category": "Mechanic Supply",
                "entry_count": mechanic_supply_summary["transaction_count"],
                "total_amount": mechanic_supply_summary["total_amount"],
            })
            expense_groups = sorted(expense_groups, key=lambda group: group["category"].lower())
            expense_total = round(expense_total + mechanic_supply_summary["total_amount"], 2)

    if start_date == end_date:
        date_label = format_date(start_date)
    else:
        date_label = f"{format_date(start_date)} to {format_date(end_date)}"

    if ledger_view == "deleted":
        report_title = "Deleted Cash Ledger Report"
        report_badge = "Deleted Entries Audit"
    else:
        report_title = "Cash Ledger Report"
        report_badge = "Cash Movement Report"

    if entry_type == "CASH_IN":
        filter_label = "Cash In only"
        filter_tone = "cash-in"
    elif entry_type == "CASH_OUT":
        filter_label = "Cash Out only"
        filter_tone = "cash-out"
    else:
        filter_label = "All entry types"
        filter_tone = "all"

    return {
        "report_title": report_title,
        "report_badge": report_badge,
        "date_label": date_label,
        "ending_balance_label": format_date(end_date),
        "generated_at": now_local().strftime("%b %d, %Y %I:%M %p"),
        "start_date": start_date,
        "end_date": end_date,
        "entry_type": entry_type,
        "ledger_view": ledger_view,
        "filter_label": filter_label,
        "filter_tone": filter_tone,
        "entries": report_data["entries"],
        "expense_groups": expense_groups,
        "expense_total": expense_total,
        "mechanic_supply_expense_total": mechanic_supply_summary["total_amount"],
        "total_in": report_data["total_in"],
        "total_out": report_data["total_out"],
        "floating_total": summary.get("floating_total", 0.0),
        "net_movement": report_data["cash_on_hand"],
        "ending_cash_on_hand": report_data["ending_cash_on_hand"],
    }


def _build_pending_payouts_payload(branch_id, target_date):
    payouts_by_date = get_mechanic_payouts_for_dates([target_date])
    paid_by_date = get_already_paid_mechanic_identifiers_for_dates([target_date], branch_id=branch_id)
    mechanic_payouts = payouts_by_date.get(target_date, [])
    paid_today = paid_by_date.get(target_date, {"mechanic_ids": set(), "mechanic_names": set()})
    already_paid_ids = paid_today.get("mechanic_ids", set())
    already_paid_names = paid_today.get("mechanic_names", set())

    pending_payouts = [
        m for m in mechanic_payouts
        if not (
            (m.get('mechanic_id') and m['mechanic_id'] in already_paid_ids)
            or (m.get('mechanic_name') in already_paid_names)
        )
    ]

    return {
        "date": target_date,
        "date_display": format_date(target_date),
        "pending_payouts": pending_payouts,
        "count": len(pending_payouts),
    }


def _build_overdue_payouts_payload(branch_id, reminder_days, today):
    reminder_dates = [
        (today_local() - timedelta(days=days_ago)).isoformat()
        for days_ago in range(1, reminder_days + 1)
    ]
    payouts_by_date = get_mechanic_payouts_for_dates(reminder_dates)
    paid_by_date = get_already_paid_mechanic_identifiers_for_dates(reminder_dates, branch_id=branch_id)

    overdue_payout_groups = []
    for payout_date in reminder_dates:
        mechanic_payouts_for_date = payouts_by_date.get(payout_date, [])
        paid_for_date = paid_by_date.get(
            payout_date,
            {"mechanic_ids": set(), "mechanic_names": set()},
        )
        paid_ids = paid_for_date.get("mechanic_ids", set())
        paid_names = paid_for_date.get("mechanic_names", set())

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

    return {
        "groups": overdue_payout_groups,
        "total": sum(group["count"] for group in overdue_payout_groups),
        "today": today,
    }


# ─────────────────────────────────────────────
# PAGE ROUTE
# ─────────────────────────────────────────────

@cash_bp.route("/cash-ledger")
@login_required
def cash_ledger():
    branch_id  = _get_branch_id()
    purge_deleted_cash_entries(branch_id=branch_id)
    ledger_view = _get_ledger_view()
    entry_type = request.args.get("type") or None
    start_date, end_date = _resolve_ledger_date_range(default_to_current_month=True)
    prefill_entry_type = request.args.get("prefill_entry_type") or ""
    prefill_payable_id = request.args.get("prefill_payable_id") or ""
    prefill_amount = request.args.get("prefill_amount") or ""
    prefill_description = request.args.get("prefill_description") or ""
    prefill_category_key = request.args.get("prefill_category_key") or ""

    if entry_type not in {"CASH_IN", "CASH_OUT", None}:
        entry_type = None

    total_entries = get_cash_entry_count(
        branch_id=branch_id,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
        ledger_view=ledger_view,
    )
    total_pages = max(1, (total_entries + LEDGER_PAGE_SIZE - 1) // LEDGER_PAGE_SIZE)

    page   = request.args.get("page", default=1, type=int) or 1
    page   = max(1, min(page, total_pages))
    offset = (page - 1) * LEDGER_PAGE_SIZE

    summary = get_cash_summary(branch_id=branch_id)
    category_choices = get_cash_category_choices()
    entries = get_cash_entries(
        branch_id=branch_id,
        limit=LEDGER_PAGE_SIZE,
        offset=offset,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
        ledger_view=ledger_view,
    )

    start_entry = offset + 1 if total_entries else 0
    end_entry   = offset + len(entries)

    # --- Mechanic Payout Panel ---
    today = today_local().isoformat()

    # --- Missed mechanic payouts for the past N days (quick reminder) ---
    reminder_days = request.args.get("reminder_days", default=REMINDER_DAYS_DEFAULT, type=int) or REMINDER_DAYS_DEFAULT
    reminder_days = max(1, min(REMINDER_DAYS_MAX, reminder_days))
    pending_payout_count = 0
    pending_non_cash_count = 0
    total_overdue_payouts = 0

    if ledger_view == "active":
        pending_payout_count = _build_pending_payouts_payload(branch_id=branch_id, target_date=today)["count"]
        total_overdue_payouts = _build_overdue_payouts_payload(
            branch_id=branch_id,
            reminder_days=reminder_days,
            today=today,
        )["total"]
        pending_non_cash_count = get_pending_non_cash_collection_count(branch_id=branch_id)

    return render_template(
        "cash/cash_ledger.html",
        summary=summary,
        entries=entries,
        page=page,
        total_entries=total_entries,
        total_pages=total_pages,
        start_entry=start_entry,
        end_entry=end_entry,
        selected_view=ledger_view,
        selected_type=entry_type,
        selected_start_date=start_date,
        selected_end_date=end_date,
        cash_category_choices=category_choices,
        pending_payout_count=pending_payout_count,
        pending_non_cash_count=pending_non_cash_count,
        today_display=format_date(today),
        today=today,
        overdue_payout_total=total_overdue_payouts,
        reminder_days=reminder_days,
        prefill_entry_type=prefill_entry_type,
        prefill_payable_id=prefill_payable_id,
        prefill_amount=prefill_amount,
        prefill_description=prefill_description,
        prefill_category_key=prefill_category_key,
    )


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

@cash_bp.route("/api/cash/summary")
@login_required
def cash_summary_api():
    branch_id = _get_branch_id()
    purge_deleted_cash_entries(branch_id=branch_id)
    summary   = get_cash_summary(branch_id=branch_id)
    return jsonify(summary)


@cash_bp.route("/api/cash/entries")
@login_required
def cash_entries_api():
    branch_id  = _get_branch_id()
    purge_deleted_cash_entries(branch_id=branch_id)
    ledger_view = _get_ledger_view()
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
        ledger_view=ledger_view,
    )
    return jsonify({"entries": entries})


@cash_bp.route("/api/cash/ledger")
@login_required
def cash_ledger_api():
    branch_id  = _get_branch_id()
    purge_deleted_cash_entries(branch_id=branch_id)
    ledger_view = _get_ledger_view()
    entry_type = request.args.get("type") or None
    start_date, end_date = _resolve_ledger_date_range(default_to_current_month=True)

    if entry_type not in {"CASH_IN", "CASH_OUT", None}:
        entry_type = None

    total_entries = get_cash_entry_count(
        branch_id=branch_id,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
        ledger_view=ledger_view,
    )
    total_pages = max(1, (total_entries + LEDGER_PAGE_SIZE - 1) // LEDGER_PAGE_SIZE)

    page = request.args.get("page", default=1, type=int) or 1
    page = max(1, min(page, total_pages))
    offset = (page - 1) * LEDGER_PAGE_SIZE

    entries = get_cash_entries(
        branch_id=branch_id,
        limit=LEDGER_PAGE_SIZE,
        offset=offset,
        entry_type=entry_type,
        start_date=start_date,
        end_date=end_date,
        ledger_view=ledger_view,
    )

    start_entry = offset + 1 if total_entries else 0
    end_entry = offset + len(entries)

    return jsonify({
        "entries": entries,
        "page": page,
        "total_pages": total_pages,
        "total_entries": total_entries,
        "start_entry": start_entry,
        "end_entry": end_entry,
        "selected_view": ledger_view,
        "selected_type": entry_type,
        "selected_start_date": start_date,
        "selected_end_date": end_date,
    })


@cash_bp.route("/api/cash/panel/pending-payouts")
@login_required
def cash_pending_payouts_panel_api():
    branch_id = _get_branch_id()
    today = today_local().isoformat()
    payload = _build_pending_payouts_payload(branch_id=branch_id, target_date=today)
    return jsonify(payload)


@cash_bp.route("/api/cash/panel/overdue-payouts")
@login_required
def cash_overdue_payouts_panel_api():
    branch_id = _get_branch_id()
    reminder_days = request.args.get("reminder_days", default=REMINDER_DAYS_DEFAULT, type=int) or REMINDER_DAYS_DEFAULT
    reminder_days = max(1, min(REMINDER_DAYS_MAX, reminder_days))
    today = today_local().isoformat()
    payload = _build_overdue_payouts_payload(
        branch_id=branch_id,
        reminder_days=reminder_days,
        today=today,
    )
    return jsonify(payload)


@cash_bp.route("/api/cash/panel/pending-non-cash")
@login_required
def cash_pending_non_cash_panel_api():
    branch_id = _get_branch_id()
    payload = get_pending_non_cash_collections(branch_id=branch_id)
    return jsonify(payload)


@cash_bp.route("/api/cash/add", methods=["POST"])
@login_required
def cash_add_api():
    data = request.get_json(silent=True) or {}
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)

    try:
        request_state = begin_idempotent_request(
            scope="cash.add",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=data,
        )
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400

    if request_state["state"] == "replay":
        return jsonify(request_state["response_body"]), request_state["response_code"]

    if request_state["state"] == "processing":
        return jsonify({"status": "error", "message": request_state["message"]}), 409

    if request_state["state"] == "mismatch":
        return jsonify({"status": "error", "message": request_state["message"]}), 409

    reference_id = data.get("reference_id")
    if reference_id in ("", None):
        reference_id = None
    payout_for_date = data.get("payout_for_date")
    payable_id = data.get("payable_id")

    try:
        entry_id = add_cash_entry(
            entry_type=data.get("entry_type"),
            amount=data.get("amount"),
            category_id=data.get("category_id"),
            description=data.get("description", ""),
            reference_id=reference_id,
            payout_for_date=payout_for_date,
            payable_id=payable_id,
            user_id=user_id,
            branch_id=_get_branch_id(),
            claim_sale_ids=data.get("claim_sale_ids") or [],
            claim_debt_payment_ids=data.get("claim_debt_payment_ids") or [],
        )
        response_body = {"status": "success"}
        finalize_idempotent_request(
            scope="cash.add",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="cash_entry",
            resource_id=entry_id,
        )
        return jsonify(response_body), 200

    except ValueError as e:
        response_body = {"status": "error", "message": str(e)}
        finalize_idempotent_request(
            scope="cash.add",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"status": "error", "message": "Server error: " + str(e)}
        finalize_idempotent_request(
            scope="cash.add",
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@cash_bp.route("/api/cash/delete/<int:entry_id>", methods=["DELETE"])
@login_required
@admin_required
def cash_delete_api(entry_id):
    try:
        delete_cash_entry(
            entry_id=entry_id,
            user_id=session.get("user_id"),
            branch_id=_get_branch_id(),
        )
        return jsonify({"status": "success"}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500


@cash_bp.route("/api/cash/restore/<int:entry_id>", methods=["POST"])
@login_required
@admin_required
def cash_restore_api(entry_id):
    try:
        restore_cash_entry(entry_id=entry_id, branch_id=_get_branch_id())
        return jsonify({"status": "success"}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500


@cash_bp.route("/reports/cash-ledger")
@login_required
def cash_ledger_report():
    try:
        context = _build_cash_report_context(branch_id=_get_branch_id())
        return render_template("cash/cash_ledger_pdf.html", report=context)
    except ValueError as exc:
        flash(str(exc), "warning")
        return redirect(url_for("cash.cash_ledger", view=_get_ledger_view()))

