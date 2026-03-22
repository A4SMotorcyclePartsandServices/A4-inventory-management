import csv
import io

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from auth.utils import admin_required, login_required
from services.stocktake_service import (
    PARTIAL_STOCKTAKE_LABEL,
    add_stocktake_item,
    bulk_save_stocktake_items,
    cancel_stocktake_session,
    confirm_stocktake_session,
    create_stocktake_session,
    get_stocktake_session,
    list_stocktake_sessions,
    remove_stocktake_item,
    update_stocktake_item,
)


stocktake_bp = Blueprint("stocktake", __name__)


@stocktake_bp.route("/stocktake")
@login_required
def stocktake_list():
    sessions = list_stocktake_sessions()
    return render_template(
        "stocktake/list.html",
        sessions=sessions,
        partial_stocktake_label=PARTIAL_STOCKTAKE_LABEL,
    )


@stocktake_bp.route("/stocktake/new", methods=["POST"])
@login_required
def create_stocktake():
    try:
        stocktake = create_stocktake_session(
            user_id=session.get("user_id"),
            username=session.get("username"),
            notes=(request.form.get("notes") or "").strip() or None,
            count_scope=request.form.get("count_scope") or "PARTIAL",
        )
        flash(f"Stocktake session {stocktake['session_number']} created.", "success")
        return redirect(url_for("stocktake.stocktake_detail", session_id=stocktake["id"]))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("stocktake.stocktake_list"))


@stocktake_bp.route("/stocktake/<int:session_id>")
@login_required
def stocktake_detail(session_id):
    stocktake = get_stocktake_session(session_id)
    if not stocktake:
        flash("Stocktake session not found.", "danger")
        return redirect(url_for("stocktake.stocktake_list"))
    return render_template(
        "stocktake/detail.html",
        stocktake=stocktake,
        partial_stocktake_label=PARTIAL_STOCKTAKE_LABEL,
    )


@stocktake_bp.route("/api/stocktake/<int:session_id>/items", methods=["POST"])
@login_required
def stocktake_add_item_api(session_id):
    data = request.get_json(silent=True) or {}
    try:
        result = add_stocktake_item(
            session_id=session_id,
            item_id=int(data.get("item_id")),
            counted_stock=data.get("counted_stock"),
            notes=(data.get("notes") or "").strip() or None,
        )
        return jsonify({"status": "success", **result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/items/<int:item_id>", methods=["POST"])
@login_required
def stocktake_update_item_api(session_id, item_id):
    data = request.get_json(silent=True) or {}
    try:
        result = update_stocktake_item(
            session_id=session_id,
            item_id=item_id,
            counted_stock=data.get("counted_stock"),
            notes=(data.get("notes") or "").strip() or None,
        )
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/save-draft", methods=["POST"])
@login_required
def stocktake_save_draft_api(session_id):
    data = request.get_json(silent=True) or {}
    try:
        result = bulk_save_stocktake_items(
            session_id=session_id,
            items=data.get("items") or [],
        )
        flash("Stocktake draft saved.", "success")
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/items/<int:item_id>/delete", methods=["POST"])
@login_required
def stocktake_remove_item_api(session_id, item_id):
    try:
        result = remove_stocktake_item(session_id=session_id, item_id=item_id)
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/confirm", methods=["POST"])
@login_required
def stocktake_confirm_api(session_id):
    try:
        result = confirm_stocktake_session(
            session_id=session_id,
            user_id=session.get("user_id"),
            username=session.get("username"),
        )
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/cancel", methods=["POST"])
@login_required
def stocktake_cancel_api(session_id):
    try:
        result = cancel_stocktake_session(
            session_id=session_id,
            user_id=session.get("user_id"),
            username=session.get("username"),
        )
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/stocktake/<int:session_id>/report")
@login_required
def stocktake_report(session_id):
    stocktake = get_stocktake_session(session_id)
    if not stocktake:
        return "Stocktake session not found.", 404

    filename = f"{stocktake['session_number']}.html"
    return Response(
        render_template(
            "stocktake/report.html",
            stocktake=stocktake,
            partial_stocktake_label=PARTIAL_STOCKTAKE_LABEL,
        ),
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


@stocktake_bp.route("/stocktake/<int:session_id>/csv")
@admin_required
def stocktake_csv(session_id):
    stocktake = get_stocktake_session(session_id)
    if not stocktake:
        return "Stocktake session not found.", 404

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Stocktake Session", stocktake["session_number"]])
    writer.writerow(["Status", stocktake["status"]])
    writer.writerow(["Count Scope", stocktake["count_scope"]])
    writer.writerow(["Created At", stocktake["created_at_display"]])
    writer.writerow(["Created By", stocktake["created_by_username"] or "System"])
    writer.writerow(["Confirmed At", stocktake["confirmed_at_display"]])
    writer.writerow(["Confirmed By", stocktake["confirmed_by_username"] or ""])
    writer.writerow(["Cancelled At", stocktake["cancelled_at_display"]])
    writer.writerow(["Cancelled By", stocktake["cancelled_by_username"] or ""])
    writer.writerow(["Notes", stocktake["notes"] or ""])
    writer.writerow([])
    writer.writerow(["Items Counted", stocktake["summary"]["item_count"]])
    writer.writerow(["Variance Items", stocktake["summary"]["variance_item_count"]])
    writer.writerow(["Shortage Items", stocktake["summary"]["shortage_item_count"]])
    writer.writerow(["Overage Items", stocktake["summary"]["overage_item_count"]])
    writer.writerow(["Total Shortage Units", stocktake["summary"]["total_shortage_units"]])
    writer.writerow(["Total Overage Units", stocktake["summary"]["total_overage_units"]])
    writer.writerow([])
    writer.writerow(["Item", "Category", "System Stock", "Counted Stock", "Variance", "Adjustment Type", "Adjustment Quantity", "Notes"])

    for item in stocktake["items"]:
        writer.writerow([
            item.get("name") or "",
            item.get("category") or "",
            item.get("system_stock") or 0,
            "" if item.get("counted_stock") is None else item.get("counted_stock"),
            item.get("variance") or 0,
            item.get("adjustment_type") or "",
            item.get("adjustment_quantity") or 0,
            item.get("notes") or "",
        ])

    filename = f"{stocktake['session_number']}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
