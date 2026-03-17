import csv
import io
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, Response
from db.database import get_db
from utils.formatters import format_date
from services.loyalty_service import (
    get_customer_loyalty_summary,
    get_customer_eligibility_bulk,
    get_customer_earn_only_bulk,
    get_customer_points_bulk,
)

customer_bp = Blueprint('customer', __name__)

POINT_TIERS = {
    "0-50": {"label": "0-50 Points", "min": 0, "max": 50},
    "51-99": {"label": "51-99 Points", "min": 51, "max": 99},
    "100+": {"label": "100+ Points", "min": 100, "max": None},
}


def _format_export_date(value):
    if not value or str(value).strip() == "":
        return ""
    try:
        parsed = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return str(value)
    return parsed.strftime("%b %d, %Y")


def _get_customer_export_rows():
    conn = get_db()
    try:
        customers = conn.execute("""
            SELECT
                c.id,
                c.customer_no,
                c.customer_name,
                c.created_at,
                COUNT(s.id) AS total_visits,
                MAX(s.transaction_date) AS last_visit,
                (
                    SELECT STRING_AGG(v2.vehicle_name::text, ', ' ORDER BY v2.vehicle_name)
                    FROM vehicles v2
                    WHERE v2.customer_id = c.id
                      AND v2.is_active = 1
                ) AS vehicles
            FROM customers c
            LEFT JOIN sales s ON s.customer_id = c.id
            WHERE c.is_active = 1
            GROUP BY c.id
            ORDER BY c.customer_name ASC
        """).fetchall()

        customer_ids = [int(c["id"]) for c in customers]
        points_by_customer = get_customer_points_bulk(customer_ids)

        export_rows = []
        for customer in customers:
            customer_id = int(customer["id"])
            export_rows.append({
                "customer_no": customer["customer_no"] or "",
                "customer_name": customer["customer_name"] or "",
                "total_visits": int(customer["total_visits"] or 0),
                "loyalty_points": int(points_by_customer.get(customer_id, 0) or 0),
                "last_visit": _format_export_date(customer["last_visit"]),
                "vehicles": customer["vehicles"] or "",
                "membership_date": _format_export_date(customer["created_at"]),
            })
        return export_rows
    finally:
        conn.close()


# ─────────────────────────────────────────────
# API: Search customers (used in out.html autocomplete)
# ─────────────────────────────────────────────
@customer_bp.route("/api/search/customers")
def search_customers():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"customers": []})

    conn = get_db()
    rows = conn.execute("""
        SELECT id, customer_no, customer_name
        FROM customers
        WHERE (customer_no ILIKE %s OR customer_name ILIKE %s)
        AND is_active = 1
        ORDER BY customer_name ASC
        LIMIT 10
    """, (f'%{query}%', f'%{query}%')).fetchall()
    conn.close()

    return jsonify({"customers": [dict(r) for r in rows]})


# ─────────────────────────────────────────────
# API: Add new customer
# ─────────────────────────────────────────────
@customer_bp.route("/api/customers/add", methods=["POST"])
def add_customer():
    data = request.get_json()
    customer_no = (data.get('customer_no') or '').strip()
    customer_name = (data.get('customer_name') or '').strip()
    vehicle_name = (data.get('vehicle_name') or '').strip()

    if not customer_no or not customer_name or not vehicle_name:
        return jsonify({"status": "error", "message": "Customer number, name, and vehicle are required."}), 400

    conn = get_db()
    try:
        customer_row = conn.execute(
            "INSERT INTO customers (customer_no, customer_name) VALUES (%s, %s) RETURNING id",
            (customer_no, customer_name)
        ).fetchone()
        new_customer_id = customer_row["id"]

        vehicle_row = conn.execute(
            "INSERT INTO vehicles (customer_id, vehicle_name, is_active) VALUES (%s, %s, 1) RETURNING id",
            (new_customer_id, vehicle_name)
        ).fetchone()
        new_vehicle_id = vehicle_row["id"]

        conn.commit()
        return jsonify({
            "status": "success",
            "customer": {
                "id": new_customer_id,
                "customer_no": customer_no,
                "customer_name": customer_name
            },
            "vehicle": {
                "id": new_vehicle_id,
                "vehicle_name": vehicle_name
            }
        })
    except Exception as e:
        # Most likely a UNIQUE constraint on customer_no
        conn.rollback()
        if "UNIQUE" in str(e):
            return jsonify({"status": "error", "message": "A customer with that number already exists."}), 409
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


@customer_bp.route("/api/customers/<int:customer_id>/vehicles")
def get_customer_vehicles(customer_id):
    conn = get_db()
    customer = conn.execute(
        "SELECT id FROM customers WHERE id = %s AND is_active = 1",
        (customer_id,)
    ).fetchone()

    if not customer:
        conn.close()
        return jsonify({"error": "Customer not found"}), 404

    vehicles = conn.execute("""
        SELECT id, vehicle_name, is_active
        FROM vehicles
        WHERE customer_id = %s AND is_active = 1
        ORDER BY vehicle_name ASC
    """, (customer_id,)).fetchall()

    conn.close()
    return jsonify({
        "customer_id": customer_id,
        "vehicles": [dict(v) for v in vehicles]
    })


@customer_bp.route("/api/customers/<int:customer_id>/vehicles/add", methods=["POST"])
def add_customer_vehicle(customer_id):
    data = request.get_json(silent=True) or {}
    vehicle_name = (data.get("vehicle_name") or "").strip()

    if not vehicle_name:
        return jsonify({"status": "error", "message": "Vehicle name is required."}), 400

    conn = get_db()
    try:
        customer = conn.execute(
            "SELECT id FROM customers WHERE id = %s AND is_active = 1",
            (customer_id,)
        ).fetchone()

        if not customer:
            return jsonify({"status": "error", "message": "Customer not found."}), 404

        row = conn.execute(
            "INSERT INTO vehicles (customer_id, vehicle_name, is_active) VALUES (%s, %s, 1) RETURNING id",
            (customer_id, vehicle_name)
        ).fetchone()
        conn.commit()
        return jsonify({
            "status": "success",
            "vehicle": {
                "id": row["id"],
                "vehicle_name": vehicle_name
            }
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        conn.close()


# ─────────────────────────────────────────────
# PAGE: Customer list
# ─────────────────────────────────────────────
@customer_bp.route("/customers")
def customer_list():
    conn = get_db()

    customers = conn.execute("""
        SELECT
            c.id,
            c.customer_no,
            c.customer_name,
            c.created_at,
            COUNT(s.id) AS total_visits,
            MAX(s.transaction_date) AS last_visit,
            (
                SELECT STRING_AGG(v2.vehicle_name::text, ', ' ORDER BY v2.vehicle_name)
                FROM vehicles v2
                WHERE v2.customer_id = c.id
            ) AS vehicles
        FROM customers c
        LEFT JOIN sales s ON s.customer_id = c.id
        WHERE c.is_active = 1
        GROUP BY c.id
        ORDER BY c.customer_name ASC
    """).fetchall()

    customer_ids = [int(c["id"]) for c in customers]
    loyalty_by_customer = get_customer_eligibility_bulk(customer_ids)
    earn_only_by_customer = get_customer_earn_only_bulk(customer_ids)
    points_by_customer = get_customer_points_bulk(customer_ids)

    customers_with_loyalty = []
    for c in customers:
        c_dict = dict(c)
        c_dict["last_visit_display"] = format_date(c_dict["last_visit"])
        c_dict["loyalty_points"] = int(points_by_customer.get(int(c_dict["id"]), 0) or 0)

        loyalty_programs = loyalty_by_customer.get(int(c_dict["id"]), [])
        earn_only_programs = earn_only_by_customer.get(int(c_dict["id"]), [])
        preview_programs = []
        for program in loyalty_programs:
            stamp_count = int(program.get("stamp_count", 0) or 0)
            threshold = int(program.get("threshold", 0) or 0)
            progress_current = int(program.get("progress_current", stamp_count) or 0)
            progress_threshold = int(program.get("progress_threshold", threshold) or 0)
            progress_unit = str(program.get("progress_unit") or "stamps")
            preview_programs.append({
                "program_id": program["program_id"],
                "name": program["name"],
                "program_type": program.get("program_type"),
                "stamp_count": stamp_count,
                "threshold": threshold,
                "points_balance": int(program.get("points_balance", 0) or 0),
                "points_threshold": int(program.get("points_threshold", 0) or 0),
                "reward_basis": program.get("reward_basis", "STAMPS"),
                "progress_current": progress_current,
                "progress_threshold": progress_threshold,
                "progress_unit": progress_unit,
                "remaining": max(0, threshold - stamp_count),
                "progress_remaining": int(program.get("progress_remaining", 0) or 0),
                "is_eligible": bool(program.get("is_eligible")),
                "redemption_count": int(program.get("redemption_count", 0) or 0),
                "pct": (
                    min(100, int((min(progress_current, progress_threshold) / progress_threshold * 100)))
                    if progress_threshold > 0 else 0
                ),
            })
        for program in earn_only_programs:
            points_balance = int(program.get("points_balance", 0) or 0)
            stamp_count = int(program.get("stamp_count", 0) or 0)
            progress_unit = "points" if bool(program.get("points_enabled")) else "stamps"
            progress_current = points_balance if progress_unit == "points" else stamp_count
            preview_programs.append({
                "program_id": program["program_id"],
                "name": program["name"],
                "program_type": program.get("program_type"),
                "stamp_count": stamp_count,
                "threshold": 0,
                "points_balance": points_balance,
                "points_threshold": 0,
                "reward_basis": "POINTS",
                "progress_current": progress_current,
                "progress_threshold": 0,
                "progress_unit": progress_unit,
                "remaining": 0,
                "progress_remaining": 0,
                "is_eligible": False,
                "is_earn_only": True,
                "redemption_count": 0,
                "pct": 0,
            })

        c_dict["loyalty_preview"] = {
            "has_programs": bool(preview_programs),
            "programs": preview_programs
        }

        customers_with_loyalty.append(c_dict)

    customers = customers_with_loyalty

    conn.close()
    return render_template("customers/customers_list.html", customers=customers)


@customer_bp.route("/export/customers")
def export_customers_csv():
    customers = _get_customer_export_rows()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Customer No.",
        "Customer Name",
        "Total Visits",
        "Loyalty Points",
        "Last Visit",
        "Vehicles",
        "Membership Date",
    ])

    for customer in customers:
        writer.writerow([
            customer["customer_no"],
            customer["customer_name"],
            customer["total_visits"],
            customer["loyalty_points"],
            customer["last_visit"],
            customer["vehicles"],
            customer["membership_date"],
        ])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"customers_export_{timestamp}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@customer_bp.route("/reports/customers/points")
def customer_points_report():
    tier_key = (request.args.get("tier") or "").strip()
    tier = POINT_TIERS.get(tier_key)
    if not tier:
        return "Invalid customer points tier.", 400

    customers = _get_customer_export_rows()
    filtered_customers = []
    for customer in customers:
        points = int(customer["loyalty_points"] or 0)
        if points < tier["min"]:
            continue
        if tier["max"] is not None and points > tier["max"]:
            continue
        filtered_customers.append(customer)

    filtered_customers.sort(
        key=lambda customer: (-customer["loyalty_points"], customer["customer_name"].lower())
    )

    return render_template(
        "reports/customer_points_pdf.html",
        customers=filtered_customers,
        tier_label=tier["label"],
        generated_at=datetime.now().strftime("%b %d, %Y %I:%M %p"),
    )


# ─────────────────────────────────────────────
# API: Get one customer's transaction history
# ─────────────────────────────────────────────
@customer_bp.route("/api/customers/<int:customer_id>/transactions")
def customer_transactions(customer_id):
    conn = get_db()

    customer = conn.execute("""
        SELECT id, customer_no, customer_name, created_at
        FROM customers WHERE id = %s
    """, (customer_id,)).fetchone()

    if not customer:
        return jsonify({"error": "Customer not found"}), 404

    sales = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.transaction_date,
            s.total_amount,
            s.status,
            v.vehicle_name,
            pm.name AS payment_method
        FROM sales s
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN vehicles v ON v.id = s.vehicle_id
        WHERE s.customer_id = %s
        ORDER BY s.transaction_date DESC
    """, (customer_id,)).fetchall()

    loyalty_stamps_by_sale = {}
    stamp_rows = conn.execute("""
        SELECT
            ls.sale_id,
            lp.name AS program_name,
            ls.redemption_id
        FROM loyalty_stamps ls
        JOIN loyalty_programs lp ON lp.id = ls.program_id
        WHERE ls.customer_id = %s
        ORDER BY ls.stamped_at ASC
    """, (customer_id,)).fetchall()

    for row in stamp_rows:
        stamp_list = loyalty_stamps_by_sale.setdefault(row["sale_id"], [])
        stamp_list.append({
            "program_name": row["program_name"],
            "is_active": row["redemption_id"] is None
        })

    result = []
    for sale in sales:
        # Get services for this sale
        services = conn.execute("""
            SELECT sv.name, ss.price
            FROM sales_services ss
            JOIN services sv ON sv.id = ss.service_id
            WHERE ss.sale_id = %s
        """, (sale['id'],)).fetchall()

        # Get items for this sale
        items = conn.execute("""
            SELECT i.name, si.quantity, si.final_unit_price
            FROM sales_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.sale_id = %s
        """, (sale['id'],)).fetchall()

        result.append({
            "id": sale['id'],
            "sales_number": sale['sales_number'],
            "transaction_date": format_date(sale['transaction_date']),
            "total_amount": sale['total_amount'],
            "status": sale['status'],
            "vehicle_name": sale['vehicle_name'],
            "payment_method": sale['payment_method'],
            "loyalty_stamps": loyalty_stamps_by_sale.get(sale['id'], []),
            "services": [dict(s) for s in services],
            "items": [dict(i) for i in items],
        })

    loyalty_summary = get_customer_loyalty_summary(customer_id)

    conn.close()
    return jsonify({
        "customer": {
            **dict(customer),
            "created_at_display": format_date(customer["created_at"])
        },
        "transactions": result,
        "loyalty_summary": loyalty_summary,
    })

