from flask import Blueprint, jsonify, request, session

from auth.utils import admin_required, login_required
from services.idempotency_service import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)
from services.approval_service import (
    approve_request,
    cancel_request,
    get_approval_request,
    get_approval_request_with_history,
    list_approval_requests,
    resubmit_request,
    request_revisions,
)
from services.transactions_service import (
    approve_purchase_order,
    cancel_purchase_order,
    request_po_revisions,
)

approval_bp = Blueprint("approval", __name__)


def _begin_json_idempotent_request(scope, data):
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    request_state = begin_idempotent_request(
        scope=scope,
        actor_user_id=user_id,
        idempotency_key=idempotency_key,
        request_payload=data,
    )
    return user_id, idempotency_key, request_state


def _idempotency_error_response(request_state):
    if request_state["state"] == "replay":
        return jsonify(request_state["response_body"]), request_state["response_code"]
    if request_state["state"] in {"processing", "mismatch"}:
        return jsonify({"status": "error", "message": request_state["message"]}), 409
    return None


@approval_bp.route("/api/admin/approvals", methods=["GET"])
@admin_required
def admin_list_approvals():
    try:
        status = request.args.get("status") or None
        approval_type = request.args.get("approval_type") or None
        rows = list_approval_requests(status=status, approval_type=approval_type)
        return jsonify({"requests": rows})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@approval_bp.route("/api/admin/approvals/<int:approval_request_id>", methods=["GET"])
@admin_required
def admin_get_approval_request(approval_request_id):
    try:
        data = get_approval_request_with_history(approval_request_id)
        if not data:
            return jsonify({"error": "Approval request not found."}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@approval_bp.route("/api/admin/approvals/<int:approval_request_id>/approve", methods=["POST"])
@admin_required
def admin_approve_request(approval_request_id):
    payload = request.get_json(silent=True) or {}
    scope = f"approval.admin.approve:{approval_request_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        request_row = get_approval_request(approval_request_id)
        if not request_row:
            response_body = {"error": "Approval request not found."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=404,
                response_body=response_body,
            )
            return jsonify(response_body), 404

        if request_row["approval_type"] == "PURCHASE_ORDER" and request_row["entity_type"] == "purchase_order":
            details = approve_purchase_order(
                po_id=request_row["entity_id"],
                admin_user_id=user_id,
                notes=(payload.get("notes") or "").strip() or None,
            )
            response_body = {"status": "success", "details": details}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=COMPLETED_STATUS,
                response_code=200,
                response_body=response_body,
                resource_type="approval_request",
                resource_id=approval_request_id,
            )
            return jsonify(response_body)

        row = approve_request(
            approval_request_id=approval_request_id,
            admin_user_id=user_id,
            notes=(payload.get("notes") or "").strip() or None,
        )
        response_body = {"status": "success", "request": row}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="approval_request",
            resource_id=approval_request_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@approval_bp.route("/api/admin/approvals/<int:approval_request_id>/revisions", methods=["POST"])
@admin_required
def admin_request_revisions(approval_request_id):
    payload = request.get_json(silent=True) or {}
    scope = f"approval.admin.revisions:{approval_request_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        request_row = get_approval_request(approval_request_id)
        if not request_row:
            response_body = {"error": "Approval request not found."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=404,
                response_body=response_body,
            )
            return jsonify(response_body), 404

        if request_row["approval_type"] == "PURCHASE_ORDER" and request_row["entity_type"] == "purchase_order":
            details = request_po_revisions(
                po_id=request_row["entity_id"],
                admin_user_id=user_id,
                notes=(payload.get("notes") or "").strip(),
                revision_items=payload.get("revision_items") or [],
            )
            response_body = {"status": "success", "details": details}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=COMPLETED_STATUS,
                response_code=200,
                response_body=response_body,
                resource_type="approval_request",
                resource_id=approval_request_id,
            )
            return jsonify(response_body)

        row = request_revisions(
            approval_request_id=approval_request_id,
            admin_user_id=user_id,
            notes=(payload.get("notes") or "").strip(),
            revision_items=payload.get("revision_items") or [],
        )
        response_body = {"status": "success", "request": row}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="approval_request",
            resource_id=approval_request_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@approval_bp.route("/api/admin/approvals/<int:approval_request_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_request(approval_request_id):
    payload = request.get_json(silent=True) or {}
    scope = f"approval.admin.cancel:{approval_request_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        request_row = get_approval_request(approval_request_id)
        if not request_row:
            response_body = {"error": "Approval request not found."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=404,
                response_body=response_body,
            )
            return jsonify(response_body), 404

        if request_row["approval_type"] == "PURCHASE_ORDER" and request_row["entity_type"] == "purchase_order":
            details = cancel_purchase_order(
                po_id=request_row["entity_id"],
                user_id=user_id,
                user_role=session.get("role"),
                notes=(payload.get("notes") or "").strip(),
            )
            response_body = {"status": "success", "details": details}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=COMPLETED_STATUS,
                response_code=200,
                response_body=response_body,
                resource_type="approval_request",
                resource_id=approval_request_id,
            )
            return jsonify(response_body)

        row = cancel_request(
            approval_request_id=approval_request_id,
            actor_id=user_id,
            actor_role=session.get("role"),
            notes=(payload.get("notes") or "").strip(),
        )
        response_body = {"status": "success", "request": row}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="approval_request",
            resource_id=approval_request_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@approval_bp.route("/api/approvals/<int:approval_request_id>/cancel", methods=["POST"])
@login_required
def requester_cancel_request(approval_request_id):
    payload = request.get_json(silent=True) or {}
    scope = f"approval.requester.cancel:{approval_request_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        request_row = get_approval_request(approval_request_id)
        if not request_row:
            response_body = {"error": "Approval request not found."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=404,
                response_body=response_body,
            )
            return jsonify(response_body), 404

        if request_row["approval_type"] == "PURCHASE_ORDER" and request_row["entity_type"] == "purchase_order":
            details = cancel_purchase_order(
                po_id=request_row["entity_id"],
                user_id=user_id,
                user_role=session.get("role"),
                notes=(payload.get("notes") or "").strip() or None,
            )
            response_body = {"status": "success", "details": details}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=COMPLETED_STATUS,
                response_code=200,
                response_body=response_body,
                resource_type="approval_request",
                resource_id=approval_request_id,
            )
            return jsonify(response_body)

        row = cancel_request(
            approval_request_id=approval_request_id,
            actor_id=user_id,
            actor_role=session.get("role"),
            notes=(payload.get("notes") or "").strip() or None,
        )
        response_body = {"status": "success", "request": row}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="approval_request",
            resource_id=approval_request_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500


@approval_bp.route("/api/approvals/<int:approval_request_id>", methods=["GET"])
@login_required
def requester_get_approval_request(approval_request_id):
    try:
        data = get_approval_request_with_history(approval_request_id)
        if not data:
            return jsonify({"error": "Approval request not found."}), 404

        requester_id = session.get("user_id")
        if int(data["requested_by"]) != int(requester_id) and session.get("role") != "admin":
            return jsonify({"error": "You do not have access to this approval request."}), 403

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@approval_bp.route("/api/approvals/<int:approval_request_id>/resubmit", methods=["POST"])
@login_required
def requester_resubmit_request(approval_request_id):
    payload = request.get_json(silent=True) or {}
    scope = f"approval.requester.resubmit:{approval_request_id}"
    try:
        user_id, idempotency_key, request_state = _begin_json_idempotent_request(scope, payload)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    early_response = _idempotency_error_response(request_state)
    if early_response:
        return early_response

    try:
        request_row = get_approval_request(approval_request_id)
        if not request_row:
            response_body = {"error": "Approval request not found."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=404,
                response_body=response_body,
            )
            return jsonify(response_body), 404

        if request_row["approval_type"] == "PURCHASE_ORDER" and request_row["entity_type"] == "purchase_order":
            response_body = {"error": "Purchase orders must be edited through the PO update flow before resubmission."}
            finalize_idempotent_request(
                scope=scope,
                actor_user_id=user_id,
                idempotency_key=idempotency_key,
                status=FAILED_STATUS,
                response_code=400,
                response_body=response_body,
            )
            return jsonify(response_body), 400

        row = resubmit_request(
            approval_request_id=approval_request_id,
            requester_id=user_id,
            metadata=payload.get("metadata"),
            notes=(payload.get("notes") or "").strip() or None,
        )
        response_body = {"status": "success", "request": row}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="approval_request",
            resource_id=approval_request_id,
        )
        return jsonify(response_body)
    except ValueError as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as e:
        response_body = {"error": str(e)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500
