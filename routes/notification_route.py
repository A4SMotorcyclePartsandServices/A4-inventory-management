from flask import Blueprint, jsonify, request, session

from auth.utils import login_required
from services.notification_service import (
    get_notification_summary,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)


notification_bp = Blueprint("notification", __name__)


@notification_bp.route("/api/notifications/summary", methods=["GET"])
@login_required
def notification_summary():
    user_id = session.get("user_id")
    limit = request.args.get("limit", 5)
    return jsonify(get_notification_summary(user_id, limit=limit))


@notification_bp.route("/api/notifications", methods=["GET"])
@login_required
def notification_list():
    user_id = session.get("user_id")
    limit = request.args.get("limit", 10)
    unread_only = str(request.args.get("unread_only") or "").strip().lower() in {"1", "true", "yes"}
    rows = list_notifications(user_id, limit=limit, unread_only=unread_only)
    return jsonify({"notifications": rows})


@notification_bp.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def notification_mark_read(notification_id):
    user_id = session.get("user_id")
    row = mark_notification_read(notification_id, user_id)
    if not row:
        return jsonify({"error": "Notification not found."}), 404
    return jsonify({"status": "success", "notification": row})


@notification_bp.route("/api/notifications/read-all", methods=["POST"])
@login_required
def notification_mark_all_read():
    user_id = session.get("user_id")
    updated_count = mark_all_notifications_read(user_id)
    return jsonify({"status": "success", "updated_count": updated_count})
