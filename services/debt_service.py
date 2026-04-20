from db.database import get_db
from datetime import datetime
from utils.formatters import format_date
from utils.timezone import now_local, now_local_str


def _money(value):
    return round(float(value or 0), 2)


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
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid,
            COALESCE((
                SELECT SUM(ss.price)
                FROM sales_services ss
                WHERE ss.sale_id = s.id
            ), 0) AS service_total,
            COALESCE((
                SELECT SUM(dp2.service_portion)
                FROM debt_payments dp2
                WHERE dp2.sale_id = s.id
            ), 0) AS service_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.status IN ('Unresolved', 'Partial')
          AND COALESCE(s.is_voided, FALSE) = FALSE
        GROUP BY s.id, m.name, pm.name
        ORDER BY s.transaction_date ASC
    """).fetchall()

    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d['total_amount'] = _money(d.get('total_amount'))
        d['total_paid'] = _money(d.get('total_paid'))
        d['service_total'] = _money(d.get('service_total'))
        d['service_paid'] = _money(d.get('service_paid'))
        d['remaining'] = round(d['total_amount'] - d['total_paid'], 2)
        d['service_remaining'] = round(max(0, d['service_total'] - d['service_paid']), 2)
        d['item_total'] = round(max(0, d['total_amount'] - d['service_total']), 2)
        d['item_paid'] = round(max(0, d['total_paid'] - d['service_paid']), 2)
        d['item_remaining'] = round(max(0, d['remaining'] - d['service_remaining']), 2)
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
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid,
            COALESCE((
                SELECT SUM(ss.price)
                FROM sales_services ss
                WHERE ss.sale_id = s.id
            ), 0) AS service_total,
            COALESCE((
                SELECT SUM(dp2.service_portion)
                FROM debt_payments dp2
                WHERE dp2.sale_id = s.id
            ), 0) AS service_paid
        FROM sales s
        LEFT JOIN mechanics m        ON m.id = s.mechanic_id
        LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
        WHERE s.id = %s
          AND COALESCE(s.is_voided, FALSE) = FALSE
        GROUP BY s.id, m.name, pm.name
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
        WHERE si.sale_id = %s
    """, (sale_id,)).fetchall()

    services = conn.execute("""
        SELECT sv.name AS service_name, ss.price, m.name AS mechanic_name
        FROM sales_services ss
        JOIN services sv ON sv.id = ss.service_id
        LEFT JOIN mechanics m ON m.id = ss.mechanic_id
        WHERE ss.sale_id = %s
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
        WHERE dp.sale_id = %s
        ORDER BY dp.paid_at ASC
    """, (sale_id,)).fetchall()

    conn.close()

    sale_dict = dict(sale)
    sale_dict['total_amount'] = _money(sale_dict.get('total_amount'))
    sale_dict['total_paid'] = _money(sale_dict.get('total_paid'))
    sale_dict['service_total'] = _money(sale_dict.get('service_total'))
    sale_dict['service_paid'] = _money(sale_dict.get('service_paid'))
    sale_dict['remaining'] = round(sale_dict['total_amount'] - sale_dict['total_paid'], 2)
    sale_dict['service_remaining'] = round(max(0, sale_dict['service_total'] - sale_dict['service_paid']), 2)
    sale_dict['item_total'] = round(max(0, sale_dict['total_amount'] - sale_dict['service_total']), 2)
    sale_dict['item_paid'] = round(max(0, sale_dict['total_paid'] - sale_dict['service_paid']), 2)
    sale_dict['item_remaining'] = round(max(0, sale_dict['remaining'] - sale_dict['service_remaining']), 2)
    sale_dict['transaction_date'] = format_date(sale_dict['transaction_date'], show_time=True)
    sale_dict['paid_at'] = format_date(sale_dict['paid_at'], show_time=True)

    formatted_payments = []
    for p in payments:
        pd = dict(p)
        pd['amount_paid'] = _money(pd.get('amount_paid'))
        pd['paid_at'] = format_date(pd['paid_at'], show_time=True)
        formatted_payments.append(pd)

    return {
        'sale':     sale_dict,
        'items':    [dict(r) for r in items],
        'services': [dict(r) for r in services],
        'payments': formatted_payments,
    }


def get_customer_debt_statement(customer_id):
    conn = get_db()
    try:
        customer = conn.execute(
            """
            SELECT
                c.id,
                c.customer_no,
                c.customer_name,
                c.created_at,
                MAX(s.transaction_date) AS last_visit
            FROM customers c
            LEFT JOIN sales s ON s.customer_id = c.id
            WHERE c.id = %s AND c.is_active = 1
            GROUP BY c.id
            """,
            (customer_id,),
        ).fetchone()

        if not customer:
            return None

        debt_sales = conn.execute(
            """
            SELECT
                s.id,
                s.sales_number,
                s.customer_id,
                s.customer_name,
                s.total_amount,
                s.status,
                s.notes,
                s.transaction_date,
                s.paid_at,
                v.vehicle_name,
                m.name AS mechanic_name,
                pm.name AS payment_method,
                COALESCE(SUM(dp.amount_paid), 0) AS total_paid,
                COALESCE((
                    SELECT SUM(ss.price)
                    FROM sales_services ss
                    WHERE ss.sale_id = s.id
                ), 0) AS service_total,
                COALESCE((
                    SELECT SUM(dp2.service_portion)
                    FROM debt_payments dp2
                    WHERE dp2.sale_id = s.id
                ), 0) AS service_paid
            FROM sales s
            LEFT JOIN mechanics m        ON m.id = s.mechanic_id
            LEFT JOIN payment_methods pm ON pm.id = s.payment_method_id
            LEFT JOIN vehicles v         ON v.id = s.vehicle_id
            LEFT JOIN debt_payments dp   ON dp.sale_id = s.id
            WHERE s.customer_id = %s
              AND COALESCE(s.is_voided, FALSE) = FALSE
              AND s.payment_method_id IN (
                    SELECT id FROM payment_methods WHERE category = 'Debt'
              )
            GROUP BY s.id, m.name, pm.name, v.vehicle_name
            ORDER BY s.transaction_date ASC, s.id ASC
            """,
            (customer_id,),
        ).fetchall()

        if not debt_sales:
            return None

        all_sales = []
        active_sales = []

        for row in debt_sales:
            sale = dict(row)
            sale["total_amount"] = _money(sale.get("total_amount"))
            sale["total_paid"] = _money(sale.get("total_paid"))
            sale["service_total"] = _money(sale.get("service_total"))
            sale["service_paid"] = _money(sale.get("service_paid"))
            sale["remaining"] = round(max(0, sale["total_amount"] - sale["total_paid"]), 2)
            sale["service_remaining"] = round(max(0, sale["service_total"] - sale["service_paid"]), 2)
            sale["item_total"] = round(max(0, sale["total_amount"] - sale["service_total"]), 2)
            sale["item_paid"] = round(max(0, sale["total_paid"] - sale["service_paid"]), 2)
            sale["item_remaining"] = round(max(0, sale["remaining"] - sale["service_remaining"]), 2)
            sale["transaction_date_display"] = format_date(sale["transaction_date"])
            sale["paid_at_display"] = format_date(sale["paid_at"])
            sale["items"] = []
            sale["services"] = []
            all_sales.append(sale)

            if sale["remaining"] > 0:
                active_sales.append(sale)

        display_sales = active_sales if active_sales else all_sales
        display_sale_ids = [int(sale["id"]) for sale in display_sales]
        showing_paid_history = not bool(active_sales)

        total_debt_amount = round(sum(sale["total_amount"] for sale in display_sales), 2)
        total_paid_amount = round(sum(sale["total_paid"] for sale in display_sales), 2)

        payments = []
        if display_sale_ids:
            item_rows = conn.execute(
                """
                SELECT
                    si.sale_id,
                    i.name AS item_name,
                    si.quantity,
                    si.final_unit_price,
                    (si.quantity * si.final_unit_price) AS line_total
                FROM sales_items si
                JOIN items i ON i.id = si.item_id
                WHERE si.sale_id = ANY(%s)
                ORDER BY si.sale_id ASC, i.name ASC, si.id ASC
                """,
                (display_sale_ids,),
            ).fetchall()

            service_rows = conn.execute(
                """
                SELECT
                    ss.sale_id,
                    sv.name AS service_name,
                    ss.price,
                    m.name AS mechanic_name
                FROM sales_services ss
                JOIN services sv ON sv.id = ss.service_id
                LEFT JOIN mechanics m ON m.id = ss.mechanic_id
                WHERE ss.sale_id = ANY(%s)
                ORDER BY ss.sale_id ASC, sv.name ASC, ss.id ASC
                """,
                (display_sale_ids,),
            ).fetchall()

            payment_rows = conn.execute(
                """
                SELECT
                    dp.id,
                    dp.sale_id,
                    dp.amount_paid,
                    dp.reference_no,
                    dp.notes,
                    dp.paid_at,
                    s.sales_number,
                    u.username AS paid_by,
                    pm.name AS payment_method
                FROM debt_payments dp
                JOIN sales s                  ON s.id = dp.sale_id
                LEFT JOIN users u             ON u.id = dp.paid_by
                LEFT JOIN payment_methods pm  ON pm.id = dp.payment_method_id
                WHERE dp.sale_id = ANY(%s)
                ORDER BY dp.paid_at ASC, dp.id ASC
                """,
                (display_sale_ids,),
            ).fetchall()

            items_by_sale = {}
            for row in item_rows:
                items_by_sale.setdefault(int(row["sale_id"]), []).append({
                    "item_name": row["item_name"],
                    "quantity": int(row["quantity"] or 0),
                    "final_unit_price": _money(row["final_unit_price"]),
                    "line_total": _money(row["line_total"]),
                })

            services_by_sale = {}
            for row in service_rows:
                services_by_sale.setdefault(int(row["sale_id"]), []).append({
                    "service_name": row["service_name"],
                    "price": _money(row["price"]),
                    "mechanic_name": row["mechanic_name"] or "",
                })

            for sale in display_sales:
                sale_id = int(sale["id"])
                sale["items"] = items_by_sale.get(sale_id, [])
                sale["services"] = services_by_sale.get(sale_id, [])

            payments = []
            for row in payment_rows:
                payment = dict(row)
                payment["amount_paid"] = _money(payment.get("amount_paid"))
                payment["paid_at_display"] = format_date(payment["paid_at"])
                payments.append(payment)
        running_balance = 0.0
        ledger = []
        for sale in display_sales:
            sale_reference = sale["sales_number"] or f"Sale #{sale['id']}"
            running_balance = round(running_balance + sale["total_amount"], 2)
            ledger.append({
                "entry_type": "debt",
                "sort_at": sale["transaction_date"],
                "sort_id": f"debt-{sale['id']}",
                "date_display": sale["transaction_date_display"],
                "description": f"Debt posted for {sale_reference}",
                "reference": sale_reference,
                "debit_amount": sale["total_amount"],
                "credit_amount": 0.0,
                "running_balance": running_balance,
            })

        for payment in payments:
            payment_reference = payment["sales_number"] or f"Sale #{payment['sale_id']}"
            running_balance = round(running_balance - payment["amount_paid"], 2)
            ledger.append({
                "entry_type": "payment",
                "sort_at": payment["paid_at"],
                "sort_id": f"payment-{payment['id']}",
                "date_display": payment["paid_at_display"],
                "description": f"Payment received for {payment_reference}",
                "reference": payment["reference_no"] or payment["sales_number"] or "-",
                "debit_amount": 0.0,
                "credit_amount": payment["amount_paid"],
                "running_balance": 0.0,
            })

        ledger.sort(key=lambda entry: (entry["sort_at"] or datetime.min, entry["sort_id"]))
        running_balance = 0.0
        for entry in ledger:
            if entry["entry_type"] == "debt":
                running_balance = round(running_balance + entry["debit_amount"], 2)
            else:
                running_balance = round(running_balance - entry["credit_amount"], 2)
            entry["running_balance"] = running_balance

        customer_dict = dict(customer)
        customer_dict["created_at_display"] = format_date(customer["created_at"])
        customer_dict["last_visit_display"] = format_date(customer["last_visit"])

        return {
            "customer": customer_dict,
            "summary": {
                "active_sale_count": len(active_sales),
                "sale_count": len(display_sales),
                "total_amount": total_debt_amount,
                "total_paid": total_paid_amount,
                "remaining": round(max(0, total_debt_amount - total_paid_amount), 2),
                "showing_paid_history": showing_paid_history,
                "statement_date": format_date(now_local()),
            },
            "active_sales": active_sales,
            "display_sales": display_sales,
            "payments": payments,
            "ledger": ledger,
        }
    finally:
        conn.close()


def get_customer_id_for_debt_sale(sale_id):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT customer_id
            FROM sales
            WHERE id = %s
              AND COALESCE(is_voided, FALSE) = FALSE
            """,
            (sale_id,),
        ).fetchone()
        return int(row["customer_id"]) if row and row["customer_id"] else None
    finally:
        conn.close()


def get_customer_active_debt_payments(customer_id):
    statement = get_customer_debt_statement(customer_id)
    if not statement:
        return None

    return {
        "customer": statement["customer"],
        "summary": statement["summary"],
        "payments": statement["payments"],
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
            WHERE id = %s
        """, (pm_id,)).fetchone()

        if not pm or pm["is_active"] != 1:
            raise ValueError("Invalid or inactive payment method.")

        if (pm["category"] or "").strip() == "Debt":
            raise ValueError("Debt payment cannot use a Debt-category payment method.")

        # 1) Current state
        sale = conn.execute("""
            SELECT s.total_amount,
            COALESCE(s.is_voided, FALSE) AS is_voided,
            COALESCE(SUM(dp.amount_paid), 0) AS total_paid
            FROM sales s
            LEFT JOIN debt_payments dp ON dp.sale_id = s.id
            WHERE s.id = %s
            GROUP BY s.id, s.total_amount, s.is_voided
        """, (sale_id,)).fetchone()

        if not sale:
            raise ValueError("Sale not found.")
        if sale["is_voided"]:
            raise ValueError("Cannot record a payment against a voided sale.")

        total_amount = _money(sale['total_amount'])
        total_paid   = _money(sale['total_paid'])
        remaining    = round(total_amount - total_paid, 2)

        # 1b) Calculate service_portion for this payment
        # Payments always fill service cost first before items
        total_service_cost = conn.execute("""
            SELECT COALESCE(SUM(price), 0)
            FROM sales_services
            WHERE sale_id = %s
        """, (sale_id,)).fetchone()[0]
        total_service_cost = _money(total_service_cost)

        already_paid_to_service = conn.execute("""
            SELECT COALESCE(SUM(service_portion), 0)
            FROM debt_payments
            WHERE sale_id = %s
        """, (sale_id,)).fetchone()[0]
        already_paid_to_service = _money(already_paid_to_service)

        remaining_service = round(total_service_cost - already_paid_to_service, 2)

        # 2) Guard: overpayment check
        amount_paid = _money(amount_paid)
        if amount_paid <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        if amount_paid > remaining:
            raise ValueError(
                f"Payment of ₱{amount_paid:,.2f} exceeds remaining balance of ₱{remaining:,.2f}."
            )

        now = now_local_str()

        # 3) Insert payment row with service_portion
        service_portion = round(min(amount_paid, max(remaining_service, 0.0)), 2)

        conn.execute("""
            INSERT INTO debt_payments
                (sale_id, amount_paid, service_portion, payment_method_id, reference_no, notes, paid_by, paid_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (sale_id, amount_paid, service_portion, pm_id, reference_no, notes, paid_by, now))

        # 4) Determine new status
        new_total_paid = round(total_paid + amount_paid, 2)
        new_remaining  = round(total_amount - new_total_paid, 2)

        if new_remaining <= 0:
            new_status = 'Paid'
            conn.execute(
                "UPDATE sales SET status = 'Paid', paid_at = %s WHERE id = %s",
                (now, sale_id)
            )
        else:
            new_status = 'Partial'
            conn.execute(
                "UPDATE sales SET status = 'Partial' WHERE id = %s",
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

