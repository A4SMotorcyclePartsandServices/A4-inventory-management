from flask import Blueprint, jsonify, render_template, request, session

from auth.utils import admin_required
from services.idempotency_service import (
    COMPLETED_STATUS,
    FAILED_STATUS,
    begin_idempotent_request,
    extract_idempotency_key,
    finalize_idempotent_request,
)
from services.void_sales_service import (
    VOID_REASON_OPTIONS,
    get_void_sale_context,
    search_void_sales,
    void_sale,
)


void_sales_bp = Blueprint("void_sales", __name__)


@void_sales_bp.route("/admin/void-sales", methods=["GET"])
@admin_required
def void_sales_page():
    return render_template(
        "admin/void_sales.html",
        void_reason_options=VOID_REASON_OPTIONS,
    )


@void_sales_bp.route("/api/admin/void-sales/search", methods=["GET"])
@admin_required
def void_sales_search_api():
    try:
        query = (request.args.get("q") or "").strip()
        limit = request.args.get("limit", 50)
        return jsonify({"rows": search_void_sales(query=query, limit=limit)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@void_sales_bp.route("/api/admin/void-sales/<int:sale_id>", methods=["GET"])
@admin_required
def void_sale_context_api(sale_id):
    try:
        return jsonify(get_void_sale_context(sale_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@void_sales_bp.route("/api/admin/void-sales/<int:sale_id>/void", methods=["POST"])
@admin_required
def void_sale_api(sale_id):
    data = request.get_json(silent=True) or {}
    user_id = session.get("user_id")
    idempotency_key = extract_idempotency_key(request)
    scope = f"sale.void:{sale_id}"

    try:
        request_state = begin_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            request_payload=data,
        )
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400

    if request_state["state"] == "replay":
        return jsonify(request_state["response_body"]), request_state["response_code"]
    if request_state["state"] in {"processing", "mismatch"}:
        return jsonify({"status": "error", "message": request_state["message"]}), 409

    try:
        result = void_sale(
            sale_id=sale_id,
            data=data,
            user_id=user_id,
            username=session.get("username"),
        )
        response_body = {"status": "success", **result}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=COMPLETED_STATUS,
            response_code=200,
            response_body=response_body,
            resource_type="sale_void",
            resource_id=sale_id,
        )
        return jsonify(response_body), 200
    except ValueError as exc:
        response_body = {"status": "error", "message": str(exc)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=400,
            response_body=response_body,
        )
        return jsonify(response_body), 400
    except Exception as exc:
        response_body = {"status": "error", "message": str(exc)}
        finalize_idempotent_request(
            scope=scope,
            actor_user_id=user_id,
            idempotency_key=idempotency_key,
            status=FAILED_STATUS,
            response_code=500,
            response_body=response_body,
        )
        return jsonify(response_body), 500
