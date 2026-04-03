import csv
import io

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for

from auth.utils import admin_required, login_required, stocktake_access_required
from services.stocktake_access_service import submit_stocktake_access_request
from services.stocktake_service import (
    PARTIAL_STOCKTAKE_LABEL,
    add_stocktake_item,
    bulk_save_stocktake_items,
    cancel_stocktake_session,
    confirm_stocktake_session,
    create_stocktake_session,
    get_stocktake_overall_report,
    get_stocktake_session,
    get_recent_stocktake_activity,
    list_stocktake_sessions,
    remove_stocktake_item,
    update_stocktake_item,
)
from utils.timezone import today_local


stocktake_bp = Blueprint("stocktake", __name__)


@stocktake_bp.route("/stocktake")
@stocktake_access_required
def stocktake_list():
    sessions = list_stocktake_sessions()
    return render_template(
        "stocktake/list.html",
        sessions=sessions,
        recent_stocktake_activity=get_recent_stocktake_activity(),
        partial_stocktake_label=PARTIAL_STOCKTAKE_LABEL,
    )


@stocktake_bp.route("/stocktake/new", methods=["POST"])
@stocktake_access_required
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
@stocktake_access_required
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
@stocktake_access_required
def stocktake_add_item_api(session_id):
    data = request.get_json(silent=True) or {}
    try:
        result = add_stocktake_item(
            session_id=session_id,
            item_id=int(data.get("item_id")),
            counted_stock=data.get("counted_stock"),
            notes=(data.get("notes") or "").strip() or None,
            actor_user_id=session.get("user_id"),
            actor_username=session.get("username"),
        )
        return jsonify({"status": "success", **result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/items/<int:item_id>", methods=["POST"])
@stocktake_access_required
def stocktake_update_item_api(session_id, item_id):
    data = request.get_json(silent=True) or {}
    try:
        result = update_stocktake_item(
            session_id=session_id,
            item_id=item_id,
            counted_stock=data.get("counted_stock"),
            notes=(data.get("notes") or "").strip() or None,
            actor_user_id=session.get("user_id"),
            actor_username=session.get("username"),
        )
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/save-draft", methods=["POST"])
@stocktake_access_required
def stocktake_save_draft_api(session_id):
    data = request.get_json(silent=True) or {}
    try:
        result = bulk_save_stocktake_items(
            session_id=session_id,
            items=data.get("items") or [],
            actor_user_id=session.get("user_id"),
            actor_username=session.get("username"),
        )
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/items/<int:item_id>/delete", methods=["POST"])
@stocktake_access_required
def stocktake_remove_item_api(session_id, item_id):
    try:
        result = remove_stocktake_item(session_id=session_id, item_id=item_id)
        return jsonify({"status": "success", "session": result})
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500


@stocktake_bp.route("/api/stocktake/<int:session_id>/confirm", methods=["POST"])
@stocktake_access_required
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
@stocktake_access_required
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
@stocktake_access_required
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
    writer.writerow([
        "Item",
        "Category",
        "Captured System Stock",
        "Active Baseline Stock",
        "Counted Stock",
        "Variance",
        "Captured Variance",
        "Baseline Mode",
        "Adjustment Type",
        "Adjustment Quantity",
        "Notes",
    ])

    for item in stocktake["items"]:
        writer.writerow([
            item.get("name") or "",
            item.get("category") or "",
            item.get("system_stock") or 0,
            item.get("active_system_stock") or 0,
            "" if item.get("counted_stock") is None else item.get("counted_stock"),
            item.get("variance") or 0,
            item.get("captured_variance") or 0,
            item.get("baseline_mode") or "CAPTURED",
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


@stocktake_bp.route("/stocktake/overall-report")
@stocktake_access_required
def stocktake_overall_report():
    today = today_local()
    default_start = today.replace(day=1)

    start_date = (request.args.get("start_date") or default_start.isoformat()).strip()
    end_date = (request.args.get("end_date") or today.isoformat()).strip()

    try:
        start_obj = date.fromisoformat(start_date)
        end_obj = date.fromisoformat(end_date)
    except ValueError:
        start_obj = default_start
        end_obj = today

    if end_obj < start_obj:
        start_obj, end_obj = end_obj, start_obj

    report_data = get_stocktake_overall_report(
        start_obj.isoformat(),
        end_obj.isoformat(),
    )

    filename = f"stocktake-overall-{report_data['start_date']}-to-{report_data['end_date']}.html"
    return Response(
        render_template(
            "stocktake/overall_report.html",
            report=report_data,
        ),
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


@stocktake_bp.route("/stocktake/overall-csv")
@stocktake_access_required
def stocktake_overall_csv():
    today = today_local()
    default_start = today.replace(day=1)

    start_date = (request.args.get("start_date") or default_start.isoformat()).strip()
    end_date = (request.args.get("end_date") or today.isoformat()).strip()

    try:
        start_obj = date.fromisoformat(start_date)
        end_obj = date.fromisoformat(end_date)
    except ValueError:
        start_obj = default_start
        end_obj = today

    if end_obj < start_obj:
        start_obj, end_obj = end_obj, start_obj

    report_data = get_stocktake_overall_report(
        start_obj.isoformat(),
        end_obj.isoformat(),
    )
    summary = report_data.get("summary", {})

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Metric", "Value"])
    writer.writerow(["Start Date", report_data["start_date"]])
    writer.writerow(["End Date", report_data["end_date"]])
    writer.writerow(["Sessions Included", summary.get("session_count", 0)])
    writer.writerow(["Completed Sessions", report_data["session_status_counts"].get("completed", 0)])
    writer.writerow(["Ongoing Sessions", report_data["session_status_counts"].get("ongoing", 0)])
    writer.writerow(["Cancelled Sessions Excluded", report_data["session_status_counts"].get("cancelled", 0)])
    writer.writerow(["Total Items Counted", summary.get("item_count", 0)])
    writer.writerow(["Total Value of Counted Items", summary.get("counted_items_value", 0)])
    writer.writerow(["No of Items w/ Variance", summary.get("variance_item_count", 0)])
    writer.writerow(["Total Value of Items w/ Variance", summary.get("variance_items_value", 0)])
    writer.writerow(["No of Items w/ Shortage", summary.get("shortage_item_count", 0)])
    writer.writerow(["Total Value of Items w/ Shortage", summary.get("shortage_items_value", 0)])
    writer.writerow(["No of Items w/ Overage", summary.get("overage_item_count", 0)])
    writer.writerow(["Total Value of Items w/ Overage", summary.get("overage_items_value", 0)])

    writer.writerow([])
    writer.writerow(["Counted Items"])
    writer.writerow([
        "Session Number",
        "Session Status",
        "Session Created",
        "Session Confirmed",
        "Item Name",
        "Category",
        "System Stock",
        "System Value",
        "Counted Stock",
        "Counted Value",
        "Variance",
        "Variance Value",
        "Baseline Mode",
        "Active Baseline Stock",
        "Active Baseline Value",
        "Captured Variance",
        "Captured Variance Value",
        "Baseline Refresh Count",
        "Latest Baseline Refresh Stock",
        "Latest Baseline Refresh At",
        "Latest Baseline Refresh By",
        "Notes",
    ])

    for item in report_data.get("items", []):
        latest_refresh = item.get("latest_baseline_refresh") or {}
        writer.writerow([
            item.get("session_number", ""),
            item.get("session_status", ""),
            item.get("session_created_at_display", ""),
            item.get("session_confirmed_at_display", ""),
            item.get("name", ""),
            item.get("category", ""),
            item.get("system_stock", 0),
            item.get("system_value", 0),
            item.get("counted_stock", ""),
            item.get("counted_value", ""),
            item.get("variance", 0),
            item.get("variance_value", 0),
            item.get("baseline_mode", ""),
            item.get("active_system_stock", 0),
            item.get("active_system_value", 0),
            item.get("captured_variance", 0),
            item.get("captured_variance_value", 0),
            item.get("baseline_refresh_count", 0),
            latest_refresh.get("baseline_stock", ""),
            latest_refresh.get("created_at_display", ""),
            latest_refresh.get("actor_username", ""),
            item.get("notes", ""),
        ])

    filename = f"stocktake-overall-{report_data['start_date']}-to-{report_data['end_date']}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@stocktake_bp.route("/stocktake/access/request", methods=["POST"])
@login_required
def stocktake_access_request():
    next_url = (request.form.get("next") or "").strip()
    if not next_url.startswith("/"):
        next_url = url_for("index")

    try:
        submit_stocktake_access_request(
            user_id=session.get("user_id"),
            username=session.get("username"),
            user_role=session.get("role"),
            request_reason=request.form.get("request_reason"),
        )
        flash("Your stocktake access request was sent to the admins.", "success")
    except ValueError as exc:
        flash(str(exc), "warning")
    except Exception as exc:
        flash(f"Unable to submit stocktake access request: {str(exc)}", "danger")

    return redirect(next_url)
