from db.database import get_db
from datetime import datetime
from utils.formatters import format_date

def get_all_debts():
    conn = get_db()

    rows = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            s.paid_at,
            m.name  AS mechanic_name,
            pm.name AS payment_method,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.status IN ('Unresolved', 'Partial')
        GROUP BY s.id
        ORDER BY s.transaction_date ASC
    """).fetchall()

    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d['remaining'] = round(d['total_amount'] - d['total_paid'], 2)
        d['transaction_date'] = format_date(d['transaction_date'], show_time=True)
        d['paid_at'] = format_date(d['paid_at'], show_time=True)
        result.append(d)

    return result

def get_debt_detail(sale_id):
    conn = get_db()

    sale = conn.execute("""
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.notes,
            s.transaction_date,
            s.paid_at,
            m.name  AS mechanic_name,
            pm.name AS payment_method,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.id = ?
        GROUP BY s.id
    """, (sale_id,)).fetchone()

    if not sale:
        conn.close()
        return None

    items = conn.execute("""
        SELECT
            i.name AS item_name,
            si.quantity,
            si.original_unit_price,
            si.discount_amount,
            si.final_unit_price,
            (si.quantity * si.final_unit_price) AS line_total
        FROM sales_items si
        JOIN items i ON i.id = si.item_id
        WHERE si.sale_id = ?
    """, (sale_id,)).fetchall()

    services = conn.execute("""
        SELECT sv.name AS service_name, ss.price
        FROM sales_services ss
        JOIN services sv ON sv.id = ss.service_id
        WHERE ss.sale_id = ?
    """, (sale_id,)).fetchall()

    payments = conn.execute("""
        SELECT
            dp.id,
            dp.amount_paid,
            dp.reference_no,
            dp.notes,
            dp.paid_at,
            u.username  AS paid_by,
            pm.name     AS payment_method
        FROM debt_payments dp
        LEFT JOIN users u            ON u.id = dp.paid_by
        LEFT JOIN payment_methods pm ON pm.id = dp.payment_method_id
        WHERE dp.sale_id = ?
        ORDER BY dp.paid_at ASC
    """, (sale_id,)).fetchall()

    conn.close()

    sale_dict = dict(sale)
    sale_dict['remaining'] = round(sale_dict['total_amount'] - sale_dict['total_paid'], 2)
    sale_dict['transaction_date'] = format_date(sale_dict['transaction_date'], show_time=True)
    sale_dict['paid_at'] = format_date(sale_dict['paid_at'], show_time=True)

    formatted_payments = []
    for p in payments:
        pd = dict(p)
        pd['paid_at'] = format_date(pd['paid_at'], show_time=True)
        formatted_payments.append(pd)

    return {
        'sale':     sale_dict,
        'items':    [dict(r) for r in items],
        'services': [dict(r) for r in services],
        'payments': formatted_payments,
    }

def record_payment(sale_id, amount_paid, payment_method_id, reference_no, notes, paid_by):
    conn = get_db()

    try:
        # 0) Validate payment method (must exist, active, and NOT Debt-category)
        try:
            pm_id = int(payment_method_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid payment method.")

        # NOTE (future branches): add branch_id filter here later.
        pm = conn.execute("""
            SELECT id, category, is_active
            FROM payment_methods
            WHERE id = ?
        """, (pm_id,)).fetchone()

        if not pm or pm["is_active"] != 1:
            raise ValueError("Invalid or inactive payment method.")

        if (pm["category"] or "").strip() == "Debt":
            raise ValueError("Debt payment cannot use a Debt-category payment method.")

        # 1) Current state
        sale = conn.execute("""
            SELECT s.total_amount,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
            FROM sales s
            LEFT JOIN debt_payments dp ON dp.sale_id = s.id
            WHERE s.id = ?
            GROUP BY s.id
        """, (sale_id,)).fetchone()

        if not sale:
            raise ValueError("Sale not found.")

        total_amount = sale['total_amount']
        total_paid   = sale['total_paid']
        remaining    = round(total_amount - total_paid, 2)

        # 1b) Calculate service_portion for this payment
        # Payments always fill service cost first before items
        total_service_cost = conn.execute("""
            SELECT COALESCE(SUM(price), 0)
            FROM sales_services
            WHERE sale_id = ?
        """, (sale_id,)).fetchone()[0]

        already_paid_to_service = conn.execute("""
            SELECT COALESCE(SUM(service_portion), 0)
            FROM debt_payments
            WHERE sale_id = ?
        """, (sale_id,)).fetchone()[0]

        remaining_service = round(total_service_cost - already_paid_to_service, 2)

        # 2) Guard: overpayment check
        amount_paid = round(float(amount_paid), 2)
        if amount_paid <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        if amount_paid > remaining:
            raise ValueError(
                f"Payment of ₱{amount_paid:,.2f} exceeds remaining balance of ₱{remaining:,.2f}."
            )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 3) Insert payment row with service_portion
        service_portion = round(min(amount_paid, max(remaining_service, 0.0)), 2)

        conn.execute("""
            INSERT INTO debt_payments
                (sale_id, amount_paid, service_portion, payment_method_id, reference_no, notes, paid_by, paid_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (sale_id, amount_paid, service_portion, pm_id, reference_no, notes, paid_by, now))

        # 4) Determine new status
        new_total_paid = round(total_paid + amount_paid, 2)
        new_remaining  = round(total_amount - new_total_paid, 2)

        if new_remaining <= 0:
            new_status = 'Paid'
            conn.execute(
                "UPDATE sales SET status = 'Paid', paid_at = ? WHERE id = ?",
                (now, sale_id)
            )
        else:
            new_status = 'Partial'
            conn.execute(
                "UPDATE sales SET status = 'Partial' WHERE id = ?",
                (sale_id,)
            )

        conn.commit()

        return {
            'new_status':    new_status,
            'new_remaining': new_remaining,
            'amount_paid':   amount_paid,
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()