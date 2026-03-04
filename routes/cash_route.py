from flask import Blueprint, render_template, request, jsonify, session
from services.cash_service import (
    get_cash_summary,
    get_cash_entries,
    add_cash_entry,
    delete_cash_entry,
    CASH_IN_CATEGORIES,
    CASH_OUT_CATEGORIES,
)

cash_bp = Blueprint('cash', __name__)

# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def _get_branch_id():
    """
    Central branch resolution.
    Today: always returns 1 (single branch).
    Future: return session.get('branch_id') once multi-branch is live.
    All routes call this — so the day branch support is needed,
    this is the only function that needs to change.
    """
    return 1


# ─────────────────────────────────────────────
# PAGE ROUTE
# ─────────────────────────────────────────────

@cash_bp.route("/cash-ledger")
def cash_ledger():
    """
    Main petty cash page.
    Renders the ledger table + summary + the form to add new entries.
    """
    branch_id = _get_branch_id()

    summary = get_cash_summary(branch_id=branch_id)
    entries = get_cash_entries(branch_id=branch_id)

    return render_template(
        "cash/cash_ledger.html",
        summary=summary,
        entries=entries,
        cash_in_categories=CASH_IN_CATEGORIES,
        cash_out_categories=CASH_OUT_CATEGORIES,
    )


# ─────────────────────────────────────────────
# API ROUTES
# ─────────────────────────────────────────────

@cash_bp.route("/api/cash/summary")
def cash_summary_api():
    """
    Returns current cash on hand summary as JSON.
    Useful for dashboard widgets or future mobile integrations.
    """
    branch_id = _get_branch_id()
    summary = get_cash_summary(branch_id=branch_id)
    return jsonify(summary)


@cash_bp.route("/api/cash/entries")
def cash_entries_api():
    """
    Returns the full ledger as JSON.
    Optional ?limit=N for dashboard preview use.
    """
    branch_id = _get_branch_id()
    limit = request.args.get("limit", type=int)
    entries = get_cash_entries(branch_id=branch_id, limit=limit)
    return jsonify({"entries": entries})


@cash_bp.route("/api/cash/add", methods=["POST"])
def cash_add_api():
    """
    Records a new cash entry (CASH_IN or CASH_OUT).
    Expects JSON body:
    { entry_type, amount, category, description }
    """
    data = request.get_json()

    try:
        add_cash_entry(
            entry_type=data.get("entry_type"),
            amount=data.get("amount"),
            category=data.get("category"),
            description=data.get("description", ""),
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
    """
    Hard deletes a cash entry.
    Admin only — enforced here at the route level, not in the service.
    The service handles the branch_id guard (can't delete another branch's entry).
    """
    if session.get("role") != "admin":
        return jsonify({"status": "error", "message": "Admin access required."}), 403

    try:
        delete_cash_entry(entry_id=entry_id, branch_id=_get_branch_id())
        return jsonify({"status": "success"}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500