from flask import Blueprint, render_template, request, jsonify, session, flash
from services.debt_service import get_all_debts, get_debt_detail, record_payment
from db.database import get_db

debt_bp = Blueprint('debt', __name__)

@debt_bp.route("/utang")
def utang_list():
    debts = get_all_debts()

    conn = get_db()
    # Only ACTIVE, and exclude Debt-category methods (you don't "pay" debt using Utang)
    #(future branches): add branch_id filter here later.

    payment_methods = conn.execute("""
        SELECT id, name, category
        FROM payment_methods
        WHERE is_active = 1
        AND category != 'Debt'
        ORDER BY category ASC, name ASC
    """).fetchall()

    cash_pm = conn.execute("""
        SELECT id FROM payment_methods
        WHERE category = 'Cash' AND is_active = 1
        ORDER BY id ASC LIMIT 1
    """).fetchone()

    others_pm = conn.execute("""
        SELECT id FROM payment_methods
        WHERE category = 'Others' AND is_active = 1
        ORDER BY id ASC LIMIT 1
    """).fetchone()

    conn.close()

    return render_template("transactions/utang.html",
        debts=debts,
        payment_methods=payment_methods,
        cash_pm_id=cash_pm["id"] if cash_pm else None,
        others_pm_id=others_pm["id"] if others_pm else None,
    )

@debt_bp.route("/api/debt/<int:sale_id>")
def debt_detail_api(sale_id):
    data = get_debt_detail(sale_id)
    if not data:
        return jsonify({"error": "Sale not found"}), 404
    return jsonify(data)

@debt_bp.route("/api/debt/<int:sale_id>/pay", methods=["POST"])
def pay_debt(sale_id):
    data = request.get_json()

    try:
        result = record_payment(
            sale_id=sale_id,
            amount_paid=data.get('amount_paid'),
            payment_method_id=data.get('payment_method_id'),
            reference_no=data.get('reference_no', ''),
            notes=data.get('notes', ''),
            paid_by=session.get('user_id'),
        )

        if result['new_status'] == 'Paid':
            flash("Debt fully settled!", "success")
        else:
            flash(
                f"Payment of ₱{result['amount_paid']:,.2f} recorded. Balance: ₱{result['new_remaining']:,.2f}",
                "success"
            )

        return jsonify({"status": "success", **result}), 200

    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": "Server error: " + str(e)}), 500

@debt_bp.route("/api/debt/audit")
def debt_audit_api():
    from utils.formatters import format_date
    conn = get_db()
    rows = conn.execute("""
        SELECT
            dp.id,
            dp.paid_at,
            dp.amount_paid,
            dp.reference_no,
            s.sales_number,
            s.id        AS sale_id,
            s.total_amount,
            s.customer_name,
            u.username  AS paid_by,
            pm.name     AS payment_method,
            SUM(dp.amount_paid) OVER (
                PARTITION BY dp.sale_id
                ORDER BY dp.paid_at
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS running_total
        FROM debt_payments dp
        JOIN sales s                  ON s.id = dp.sale_id
        LEFT JOIN users u             ON u.id = dp.paid_by
        LEFT JOIN payment_methods pm  ON pm.id = dp.payment_method_id
        ORDER BY dp.paid_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()

    formatted = []
    for r in rows:
        d = dict(r)
        d['paid_at'] = format_date(d['paid_at'], show_time=True)
        d['fully_paid'] = round(d['running_total'], 2) >= round(d['total_amount'], 2)
        formatted.append(d)

    return jsonify({"payments": formatted})

@debt_bp.route("/api/debt/summary")
def debt_summary_api():
    """
    Returns all debt-originated sales with accurate server-side totals.
    - Uses LEFT JOIN so zero-payment Unresolved sales appear too
    - Filters by date range if start_date / end_date query params are provided
    - Status is computed from math, not from sales.status column, so it's always accurate
    NOTE (future branches): add branch_id filter here when ready.
    """
    from utils.formatters import format_date

    start_date = request.args.get("start_date")  # expects YYYY-MM-DD
    end_date   = request.args.get("end_date")    # expects YYYY-MM-DD

    conn = get_db()

    query = """
        SELECT
            s.id            AS sale_id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.transaction_date,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
        FROM sales s
        LEFT JOIN debt_payments dp ON dp.sale_id = s.id
        WHERE s.payment_method_id IN (
            SELECT id FROM payment_methods WHERE category = 'Debt'
        )
    """

    params = []

    if start_date:
        query += " AND DATE(s.transaction_date) >= ?"
        params.append(start_date)

    if end_date:
        query += " AND DATE(s.transaction_date) <= ?"
        params.append(end_date)

    query += " GROUP BY s.id ORDER BY s.transaction_date DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for r in rows:
        total_paid   = round(r["total_paid"], 2)
        total_amount = round(r["total_amount"], 2)
        remaining    = round(max(0, total_amount - total_paid), 2)

        if remaining <= 0:
            status = "paid"
        elif total_paid > 0:
            status = "partial"
        else:
            status = "unpaid"

        result.append({
            "sale_id":       r["sale_id"],
            "sales_number":  r["sales_number"],
            "customer_name": r["customer_name"] or "Walk-in",
            "total_amount":  total_amount,
            "total_paid":    total_paid,
            "remaining":     remaining,
            "status":        status,
            "date":          format_date(r["transaction_date"]),
        })

    return jsonify({"sales": result})


@debt_bp.route("/api/debt/payments/<int:sale_id>")
def debt_payments_for_sale(sale_id):
    """
    All payment entries for one specific sale — no limit.
    Called lazily when the user expands a row.
    """
    from utils.formatters import format_date
    conn = get_db()
    rows = conn.execute("""
        SELECT
            dp.paid_at,
            dp.amount_paid,
            dp.reference_no,
            dp.notes,
            pm.name     AS payment_method,
            u.username  AS paid_by
        FROM debt_payments dp
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        LEFT JOIN users u            ON u.id  = dp.paid_by
        WHERE dp.sale_id = ?
        ORDER BY dp.paid_at ASC
    """, (sale_id,)).fetchall()
    conn.close()

    return jsonify({
        "payments": [
            {**dict(r), "paid_at": format_date(r["paid_at"], show_time=True)}
            for r in rows
        ]
    })

@debt_bp.route("/debt/statement/<int:sale_id>")
def customer_debt_statement(sale_id):
    from services.debt_service import get_debt_detail
    data = get_debt_detail(sale_id)
    if not data:
        return "Statement not found.", 404
    return render_template("debt/statement.html", data=data)
