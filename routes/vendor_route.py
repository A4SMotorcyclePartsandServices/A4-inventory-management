from flask import Blueprint, abort, jsonify, request
from werkzeug.exceptions import HTTPException

from auth.utils import login_required
from db.database import get_db
from services.vendor_service import add_vendor_record, get_vendor_payload, update_vendor_record


vendor_bp = Blueprint("vendor", __name__)


@vendor_bp.route("/api/search/vendors")
@login_required
def search_vendors():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"vendors": []})

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id, vendor_name, address, contact_person, contact_no, email
            FROM vendors
            WHERE is_active = 1
              AND (
                    vendor_name ILIKE %s
                 OR contact_person ILIKE %s
                 OR contact_no ILIKE %s
                 OR email ILIKE %s
              )
            ORDER BY vendor_name ASC
            LIMIT 10
            """,
            (f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
        ).fetchall()
        return jsonify({"vendors": [dict(row) for row in rows]})
    finally:
        conn.close()


@vendor_bp.route("/api/vendors/<int:vendor_id>")
@login_required
def get_vendor(vendor_id):
    vendor = get_vendor_payload(vendor_id, active_only=False)
    if not vendor:
        return jsonify({"status": "error", "message": "Vendor not found."}), 404

    return jsonify({"status": "success", "vendor": vendor})


@vendor_bp.route("/api/vendors/add", methods=["POST"])
@login_required
def add_vendor():
    data = request.get_json(silent=True) or {}
    try:
        result = add_vendor_record(
            vendor_name=data.get("vendor_name"),
            address=data.get("address"),
            contact_person=data.get("contact_person"),
            contact_no=data.get("contact_no"),
            email=data.get("email"),
        )
        if result["status"] == "missing_fields":
            return jsonify({"status": "error", "message": result["message"]}), 400
        if result["status"] == "duplicate":
            abort(409, description="A vendor with that name already exists.")
        if result["status"] == "ok":
            return jsonify({"status": "success", "vendor": result["vendor"]})
        return jsonify({"status": "error", "message": "Could not save vendor."}), 500
    except HTTPException:
        raise
    except Exception:
        return jsonify({"status": "error", "message": "Unexpected server error."}), 500


@vendor_bp.route("/api/vendors/<int:vendor_id>/update", methods=["POST"])
@login_required
def update_vendor(vendor_id):
    data = request.get_json(silent=True) or {}

    try:
        result = update_vendor_record(
            vendor_id=vendor_id,
            vendor_name=data.get("vendor_name"),
            address=data.get("address"),
            contact_person=data.get("contact_person"),
            contact_no=data.get("contact_no"),
            email=data.get("email"),
        )
        if result["status"] == "missing":
            return jsonify({"status": "error", "message": "Vendor not found."}), 404
        if result["status"] == "missing_fields":
            return jsonify({"status": "error", "message": result["message"]}), 400
        if result["status"] == "duplicate":
            abort(409, description="A vendor with that name already exists.")
        if result["status"] == "ok":
            return jsonify({"status": "success", "vendor": result["vendor"]})
        return jsonify({"status": "error", "message": "Could not update vendor."}), 500
    except HTTPException:
        raise
    except Exception:
        return jsonify({"status": "error", "message": "Unexpected server error."}), 500
