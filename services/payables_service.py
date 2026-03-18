from datetime import date, datetime, timedelta

from db.database import get_db
from services.notification_service import create_notifications_for_users, list_active_user_ids
from utils.formatters import format_date


ACTIVE_CHEQUE_STATUSES = ("ISSUED", "CLEARED")
PAYABLE_STATUS_OPEN = "OPEN"
PAYABLE_STATUS_PARTIAL = "PARTIAL"
PAYABLE_STATUS_FULLY_ISSUED = "FULLY_ISSUED"
PAYABLE_STATUS_CANCELLED = "CANCELLED"
CHEQUE_STATUS_ISSUED = "ISSUED"
CHEQUE_STATUS_CLEARED = "CLEARED"
CHEQUE_STATUS_CANCELLED = "CANCELLED"
CHEQUE_STATUS_BOUNCED = "BOUNCED"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_money(value):
    return round(float(value or 0), 2)


def _parse_iso_date(raw_value, field_label):
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"{field_label} is required.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field_label} must be a valid date.")


def _payables_action_url():
    return "/transaction/payables"


def _sum_active_cheque_amount(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(cheque_amount), 0) AS total_amount
            FROM payable_cheques
            WHERE payable_id = %s
              AND status = ANY(%s)
            """,
            (int(payable_id), list(ACTIVE_CHEQUE_STATUSES)),
        ).fetchone()
        return _normalize_money(row["total_amount"] if row else 0)
    finally:
        if not external_conn:
            conn.close()


def sync_payable_status(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        payable = conn.execute(
            """
            SELECT id, amount_due, status
            FROM payables
            WHERE id = %s
            """,
            (int(payable_id),),
        ).fetchone()
        if not payable:
            raise ValueError("Payable record not found.")

        if str(payable["status"] or "").strip().upper() == PAYABLE_STATUS_CANCELLED:
            return PAYABLE_STATUS_CANCELLED

        amount_due = _normalize_money(payable["amount_due"])
        issued_amount = _sum_active_cheque_amount(payable_id, external_conn=conn)

        if issued_amount <= 0:
            next_status = PAYABLE_STATUS_OPEN
        elif issued_amount < amount_due:
            next_status = PAYABLE_STATUS_PARTIAL
        else:
            next_status = PAYABLE_STATUS_FULLY_ISSUED

        conn.execute(
            """
            UPDATE payables
            SET status = %s,
                updated_at = %s
            WHERE id = %s
            """,
            (next_status, _now(), int(payable_id)),
        )

        if not external_conn:
            conn.commit()
        return next_status
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def ensure_payable_for_po_receipt(receipt_id, *, created_by=None, created_by_username=None, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        existing = conn.execute(
            """
            SELECT id
            FROM payables
            WHERE po_receipt_id = %s
            LIMIT 1
            """,
            (int(receipt_id),),
        ).fetchone()
        if existing:
            sync_payable_status(existing["id"], external_conn=conn)
            if not external_conn:
                conn.commit()
            return int(existing["id"])

        receipt = conn.execute(
            """
            SELECT
                pr.id,
                pr.po_id,
                pr.received_at,
                po.vendor_id,
                po.vendor_name,
                po.po_number,
                po.created_at AS po_created_at,
                COALESCE(SUM(pri.line_total), 0) AS total_amount
            FROM po_receipts pr
            JOIN purchase_orders po ON po.id = pr.po_id
            LEFT JOIN po_receipt_items pri ON pri.receipt_id = pr.id
            WHERE pr.id = %s
            GROUP BY pr.id, pr.po_id, pr.received_at, po.vendor_id, po.vendor_name, po.po_number, po.created_at
            """,
            (int(receipt_id),),
        ).fetchone()
        if not receipt:
            raise ValueError("PO receipt record not found.")

        payable_row = conn.execute(
            """
            INSERT INTO payables (
                source_type,
                po_id,
                po_receipt_id,
                vendor_id,
                vendor_name_snapshot,
                po_number_snapshot,
                po_created_at_snapshot,
                delivery_received_at_snapshot,
                payee_name,
                description,
                amount_due,
                status,
                created_by,
                created_by_username,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "PO_DELIVERY",
                int(receipt["po_id"]),
                int(receipt["id"]),
                receipt["vendor_id"],
                receipt["vendor_name"] or "",
                receipt["po_number"] or "",
                receipt["po_created_at"],
                receipt["received_at"],
                receipt["vendor_name"] or "Supplier",
                f"PO delivery batch for {receipt['po_number'] or 'PO'}",
                _normalize_money(receipt["total_amount"]),
                PAYABLE_STATUS_OPEN,
                int(created_by) if created_by else None,
                str(created_by_username or "").strip() or None,
                _now(),
                _now(),
            ),
        ).fetchone()

        payable_id = int(payable_row["id"])
        sync_payable_status(payable_id, external_conn=conn)

        if not external_conn:
            conn.commit()
        return payable_id
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def create_manual_payable(*, payee_name, description, amount_due, reference_no=None, created_by=None, created_by_username=None):
    payee_name = str(payee_name or "").strip()
    description = str(description or "").strip()
    reference_no = str(reference_no or "").strip()
    amount_due_value = _normalize_money(amount_due)

    if not payee_name:
        raise ValueError("Payee is required.")
    if not description:
        raise ValueError("Description is required.")
    if amount_due_value <= 0:
        raise ValueError("Amount due must be greater than zero.")

    conn = get_db()
    try:
        row = conn.execute(
            """
            INSERT INTO payables (
                source_type,
                payee_name,
                description,
                reference_no,
                amount_due,
                status,
                created_by,
                created_by_username,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                "MANUAL",
                payee_name,
                description,
                reference_no or None,
                amount_due_value,
                PAYABLE_STATUS_OPEN,
                int(created_by) if created_by else None,
                str(created_by_username or "").strip() or None,
                _now(),
                _now(),
            ),
        ).fetchone()
        conn.commit()
        return int(row["id"])
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def issue_payable_cheque(
    payable_id,
    *,
    cheque_no,
    cheque_date,
    due_date,
    cheque_amount,
    notes=None,
    created_by=None,
    created_by_username=None,
):
    cheque_no = str(cheque_no or "").strip()
    notes = str(notes or "").strip()
    cheque_amount_value = _normalize_money(cheque_amount)
    cheque_date_value = _parse_iso_date(cheque_date, "Cheque date")
    due_date_value = _parse_iso_date(due_date, "Due date")

    if not cheque_no:
        raise ValueError("Cheque number is required.")
    if cheque_amount_value <= 0:
        raise ValueError("Cheque amount must be greater than zero.")
    if due_date_value < cheque_date_value:
        raise ValueError("Due date cannot be earlier than cheque date.")

    conn = get_db()
    try:
        payable = conn.execute(
            """
            SELECT id, payee_name, amount_due, status
            FROM payables
            WHERE id = %s
            """,
            (int(payable_id),),
        ).fetchone()
        if not payable:
            raise ValueError("Payable record not found.")
        if str(payable["status"] or "").strip().upper() == PAYABLE_STATUS_CANCELLED:
            raise ValueError("Cancelled payables cannot receive new cheques.")

        duplicate = conn.execute(
            """
            SELECT id
            FROM payable_cheques
            WHERE cheque_no = %s
            LIMIT 1
            """,
            (cheque_no,),
        ).fetchone()
        if duplicate:
            raise ValueError("Cheque number already exists.")

        issued_amount = _sum_active_cheque_amount(payable_id, external_conn=conn)
        remaining_balance = max(0.0, _normalize_money(payable["amount_due"]) - issued_amount)
        if cheque_amount_value > remaining_balance:
            raise ValueError(f"Cheque amount exceeds the remaining balance of {remaining_balance:,.2f}.")

        conn.execute(
            """
            INSERT INTO payable_cheques (
                payable_id,
                cheque_no,
                cheque_date,
                due_date,
                cheque_amount,
                status,
                notes,
                created_by,
                created_by_username,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(payable_id),
                cheque_no,
                cheque_date_value.isoformat(),
                due_date_value.isoformat(),
                cheque_amount_value,
                CHEQUE_STATUS_ISSUED,
                notes or None,
                int(created_by) if created_by else None,
                str(created_by_username or "").strip() or None,
                _now(),
                _now(),
            ),
        )

        sync_payable_status(payable_id, external_conn=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_payable_cheque_status(cheque_id, status):
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in {
        CHEQUE_STATUS_ISSUED,
        CHEQUE_STATUS_CLEARED,
        CHEQUE_STATUS_CANCELLED,
        CHEQUE_STATUS_BOUNCED,
    }:
        raise ValueError("Invalid cheque status.")

    conn = get_db()
    try:
        row = conn.execute(
            """
            UPDATE payable_cheques
            SET status = %s,
                updated_at = %s
            WHERE id = %s
            RETURNING payable_id
            """,
            (normalized_status, _now(), int(cheque_id)),
        ).fetchone()
        if not row:
            raise ValueError("Cheque record not found.")

        sync_payable_status(int(row["payable_id"]), external_conn=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _serialize_cheque_row(row, today_value):
    data = dict(row)
    due_date_value = data.get("due_date")
    cheque_date_value = data.get("cheque_date")
    due_date_obj = due_date_value if isinstance(due_date_value, date) else None
    if not due_date_obj:
        try:
            due_date_obj = datetime.strptime(str(due_date_value), "%Y-%m-%d").date()
        except ValueError:
            due_date_obj = None

    return {
        "id": int(data["id"]),
        "cheque_no": data["cheque_no"] or "-",
        "cheque_date": format_date(cheque_date_value),
        "due_date": format_date(due_date_value),
        "cheque_amount": _normalize_money(data["cheque_amount"]),
        "status": data["status"] or CHEQUE_STATUS_ISSUED,
        "notes": data["notes"] or "",
        "is_due_today": bool(due_date_obj and due_date_obj == today_value and (data["status"] or "").upper() == CHEQUE_STATUS_ISSUED),
        "is_due_soon": bool(due_date_obj and today_value < due_date_obj <= today_value + timedelta(days=7) and (data["status"] or "").upper() == CHEQUE_STATUS_ISSUED),
    }


def get_payables_page_context():
    today_value = date.today()
    conn = get_db()
    try:
        payable_rows = conn.execute(
            """
            SELECT
                p.*,
                COALESCE(SUM(CASE WHEN pc.status = ANY(%s) THEN pc.cheque_amount ELSE 0 END), 0) AS issued_amount,
                MAX(pc.due_date) AS latest_due_date
            FROM payables p
            LEFT JOIN payable_cheques pc ON pc.payable_id = p.id
            GROUP BY p.id
            ORDER BY
                COALESCE(p.delivery_received_at_snapshot, p.created_at) DESC,
                p.id DESC
            """,
            (list(ACTIVE_CHEQUE_STATUSES),),
        ).fetchall()

        cheque_rows = conn.execute(
            """
            SELECT
                pc.*,
                p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            ORDER BY pc.created_at DESC, pc.id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    cheques_by_payable = {}
    for row in cheque_rows:
        cheques_by_payable.setdefault(int(row["payable_id"]), []).append(_serialize_cheque_row(row, today_value))

    po_based_payables = []
    manual_payables = []
    total_remaining = 0.0
    open_count = 0
    due_soon_count = 0
    due_today_count = 0

    for row in payable_rows:
        issued_amount = _normalize_money(row["issued_amount"])
        amount_due = _normalize_money(row["amount_due"])
        remaining_balance = max(0.0, amount_due - issued_amount)
        cheque_history = cheques_by_payable.get(int(row["id"]), [])
        latest_cheque_status = cheque_history[0]["status"] if cheque_history else "-"

        payable_data = {
            "id": int(row["id"]),
            "source_type": row["source_type"],
            "po_id": row["po_id"],
            "po_receipt_id": row["po_receipt_id"],
            "po_number_snapshot": row["po_number_snapshot"] or "-",
            "vendor_name_snapshot": row["vendor_name_snapshot"] or "",
            "po_created_at_snapshot": format_date(row["po_created_at_snapshot"]),
            "delivery_received_at_snapshot": format_date(row["delivery_received_at_snapshot"], show_time=True),
            "payee_name": row["payee_name"] or "-",
            "description": row["description"] or "",
            "reference_no": row["reference_no"] or "",
            "amount_due": amount_due,
            "issued_amount": issued_amount,
            "remaining_balance": remaining_balance,
            "status": row["status"] or PAYABLE_STATUS_OPEN,
            "latest_due_date": format_date(row["latest_due_date"]),
            "latest_cheque_status": latest_cheque_status,
            "created_at": format_date(row["created_at"], show_time=True),
            "cheques": cheque_history,
        }

        total_remaining += remaining_balance
        if payable_data["status"] in {PAYABLE_STATUS_OPEN, PAYABLE_STATUS_PARTIAL}:
            open_count += 1
        due_soon_count += sum(1 for cheque in cheque_history if cheque["is_due_soon"])
        due_today_count += sum(1 for cheque in cheque_history if cheque["is_due_today"])

        if row["source_type"] == "PO_DELIVERY":
            po_based_payables.append(payable_data)
        else:
            manual_payables.append(payable_data)

    return {
        "summary": {
            "open_count": open_count,
            "total_remaining": round(total_remaining, 2),
            "due_soon_count": due_soon_count,
            "due_today_count": due_today_count,
        },
        "po_based_payables": po_based_payables,
        "manual_payables": manual_payables,
        "today": today_value.isoformat(),
    }


def build_payables_report_context(start_date=None, end_date=None):
    start_value = str(start_date or "").strip()
    end_value = str(end_date or "").strip()

    today_value = date.today()
    if not start_value and not end_value:
        start_value = today_value.replace(day=1).isoformat()
        end_value = today_value.isoformat()
    elif bool(start_value) != bool(end_value):
        raise ValueError("Both start and end date are required for a custom payables report.")

    if end_value < start_value:
        raise ValueError("End date cannot be earlier than start date.")

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                pc.id,
                pc.cheque_no,
                pc.cheque_date,
                pc.due_date,
                pc.cheque_amount,
                pc.status,
                pc.notes,
                p.source_type,
                p.payee_name,
                p.description,
                p.po_number_snapshot,
                p.delivery_received_at_snapshot
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.cheque_date BETWEEN %s AND %s
            ORDER BY pc.cheque_date DESC, pc.id DESC
            """,
            (start_value, end_value),
        ).fetchall()
    finally:
        conn.close()

    items = []
    total_amount = 0.0
    for row in rows:
        amount = _normalize_money(row["cheque_amount"])
        total_amount += amount
        items.append({
            "id": int(row["id"]),
            "cheque_no": row["cheque_no"] or "-",
            "cheque_date": format_date(row["cheque_date"]),
            "due_date": format_date(row["due_date"]),
            "cheque_amount": amount,
            "status": row["status"] or CHEQUE_STATUS_ISSUED,
            "payee_name": row["payee_name"] or "-",
            "description": row["description"] or "",
            "source_type": row["source_type"] or "MANUAL",
            "po_number_snapshot": row["po_number_snapshot"] or "",
            "delivery_received_at_snapshot": format_date(row["delivery_received_at_snapshot"], show_time=True),
            "notes": row["notes"] or "",
        })

    return {
        "report_title": "Payables Cheque Report",
        "date_label": f"{format_date(start_value)} to {format_date(end_value)}",
        "generated_at": format_date(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), show_time=True),
        "items": items,
        "total_amount": round(total_amount, 2),
        "start_date": start_value,
        "end_date": end_value,
    }


def run_payable_cheque_due_reminders():
    recipient_user_ids = list_active_user_ids()
    if not recipient_user_ids:
        return {"due_in_7_days": 0, "due_today": 0}

    today_value = date.today()
    due_in_7_days = today_value + timedelta(days=7)

    conn = get_db()
    try:
        upcoming_rows = conn.execute(
            """
            SELECT pc.id, pc.cheque_no, pc.due_date, p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.status = %s
              AND pc.due_date = %s
              AND pc.reminded_due_minus_7 = 0
            """,
            (CHEQUE_STATUS_ISSUED, due_in_7_days.isoformat()),
        ).fetchall()

        today_rows = conn.execute(
            """
            SELECT pc.id, pc.cheque_no, pc.due_date, p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.status = %s
              AND pc.due_date = %s
              AND pc.reminded_due_today = 0
            """,
            (CHEQUE_STATUS_ISSUED, today_value.isoformat()),
        ).fetchall()

        for row in upcoming_rows:
            create_notifications_for_users(
                recipient_user_ids,
                "PAYABLE_CHEQUE_DUE_IN_7_DAYS",
                "Cheque due in 7 days",
                f"Cheque #{row['cheque_no']} for {row['payee_name'] or 'payee'} is due on {format_date(row['due_date'])}.",
                category="payables",
                entity_type="PAYABLE_CHEQUE",
                entity_id=int(row["id"]),
                action_url=_payables_action_url(),
                external_conn=conn,
                metadata={"cheque_id": int(row["id"]), "due_date": str(row["due_date"])},
            )
            conn.execute(
                """
                UPDATE payable_cheques
                SET reminded_due_minus_7 = 1,
                    updated_at = %s
                WHERE id = %s
                """,
                (_now(), int(row["id"])),
            )

        for row in today_rows:
            create_notifications_for_users(
                recipient_user_ids,
                "PAYABLE_CHEQUE_DUE_TODAY",
                "Cheque due today",
                f"Cheque #{row['cheque_no']} for {row['payee_name'] or 'payee'} is due today ({format_date(row['due_date'])}).",
                category="payables",
                entity_type="PAYABLE_CHEQUE",
                entity_id=int(row["id"]),
                action_url=_payables_action_url(),
                external_conn=conn,
                metadata={"cheque_id": int(row["id"]), "due_date": str(row["due_date"])},
            )
            conn.execute(
                """
                UPDATE payable_cheques
                SET reminded_due_today = 1,
                    updated_at = %s
                WHERE id = %s
                """,
                (_now(), int(row["id"])),
            )

        conn.commit()
        return {
            "due_in_7_days": len(upcoming_rows),
            "due_today": len(today_rows),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
