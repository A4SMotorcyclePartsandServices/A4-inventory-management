from flask import Blueprint, request, jsonify, session
from auth.utils import admin_required, login_required
from db.database import get_db
from services.loyalty_service import (
    get_all_programs,
    create_program,
    toggle_program,
    extend_program_period,
    get_customer_eligibility,
    get_customer_earn_only,
    redeem_reward,
    get_customer_loyalty_summary,
)

loyalty_bp = Blueprint("loyalty", __name__)


# ─────────────────────────────────────────────────────────────
# PROGRAM ADMIN
# ─────────────────────────────────────────────────────────────

@loyalty_bp.route("/api/loyalty/programs", methods=["GET"])
@login_required
def list_programs():
    programs = get_all_programs(include_rules=True)
    return jsonify({"programs": programs})


@loyalty_bp.route("/api/loyalty/programs", methods=["POST"])
@admin_required
def add_program():
    data = request.get_json(silent=True) or {}
    try:
        new_id = create_program(data, session.get("user_id"))
        return jsonify({
            "status": "success",
            "program_id": new_id,
            "message": f"Program '{data.get('name', 'Unnamed program')}' created successfully and the list was refreshed."
        })
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@loyalty_bp.route("/api/loyalty/programs/<int:program_id>/toggle", methods=["POST"])
@admin_required
def toggle_program_route(program_id):
    data      = request.get_json(silent=True) or {}
    is_active = bool(data.get("is_active", True))
    try:
        toggle_program(program_id, is_active)
        conn = get_db()
        row = conn.execute(
            "SELECT name, is_active, period_end FROM loyalty_programs WHERE id = %s",
            (program_id,)
        ).fetchone()
        conn.close()

        program_name = row["name"] if row else f"Program #{program_id}"
        status_label = "activated" if is_active else "deactivated"
        return jsonify({
            "status": "success",
            "message": f"{program_name} has been {status_label} and the list was refreshed."
        })
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@loyalty_bp.route("/api/loyalty/programs/<int:program_id>/extend", methods=["POST"])
@admin_required
def extend_program_route(program_id):
    data = request.get_json(silent=True) or {}
    try:
        updated = extend_program_period(program_id, data.get("period_end"))
        return jsonify({
            "status": "success",
            "program": updated,
            "message": f"{updated['name']} was extended to {updated['period_end']}."
        })
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# ELIGIBILITY  (OUT page banner)
# ─────────────────────────────────────────────────────────────

@loyalty_bp.route("/api/loyalty/eligibility/<int:customer_id>", methods=["GET"])
@login_required
def eligibility(customer_id):
    """
    Called by the OUT page when a registered customer is selected.
    Returns their stamp progress on all active programs.
    Front end uses this to show/hide the eligibility banner.

    Query param branch_id=1 - pass once multi-branch is live.
    """
    branch_id = request.args.get("branch_id", type=int)  # None until multi-branch
    try:
        programs = get_customer_eligibility(customer_id, branch_id=branch_id)
        earn_only_programs = get_customer_earn_only(customer_id, branch_id=branch_id)
        return jsonify({"programs": programs, "earn_only_programs": earn_only_programs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# REDEMPTION
# ─────────────────────────────────────────────────────────────

@loyalty_bp.route("/api/loyalty/redeem", methods=["POST"])
@login_required
def redeem():
    """
    Body: { customer_id, program_id, sale_id }

    sale_id must be the sale where the reward is being applied.
    The sale must already exist (submit the sale first, then call this).

    Why sale first:
        The reward (e.g. a discount) is applied on the sale itself.
        We need a real sale_id to link the redemption to.
        Front end flow: submit sale → get sale_id → call /redeem if customer
        confirmed they want to use the reward.
    """
    data        = request.get_json(silent=True) or {}
    customer_id = data.get("customer_id")
    program_id  = data.get("program_id")
    sale_id     = data.get("sale_id")

    if not all([customer_id, program_id, sale_id]):
        return jsonify({"status": "error", "message": "customer_id, program_id, and sale_id are required."}), 400

    try:
        result = redeem_reward(
            customer_id=int(customer_id),
            program_id=int(program_id),
            sale_id=int(sale_id),
            user_id=session.get("user_id"),
        )
        return jsonify({"status": "success", "redemption": result})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 409
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ─────────────────────────────────────────────────────────────
# CUSTOMER LOYALTY SUMMARY  (customer profile page)
# ─────────────────────────────────────────────────────────────

@loyalty_bp.route("/api/loyalty/customer/<int:customer_id>/summary", methods=["GET"])
@login_required
def customer_loyalty_summary(customer_id):
    try:
        summary = get_customer_loyalty_summary(customer_id)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



