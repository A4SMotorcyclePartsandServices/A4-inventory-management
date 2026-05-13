import secrets
import string
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db.database import get_db
from services.notification_service import create_notifications_for_users, list_active_user_ids
from utils.formatters import format_date
from utils.timezone import now_local, now_local_str, today_local


ACTIVE_CHEQUE_STATUSES = ("ISSUED", "CLEARED")
ACTIVE_CASH_PAYMENT_STATUSES = ("UNPAID", "PAID")
PAYMENT_METHOD_CHEQUE = "CHEQUE"
PAYMENT_METHOD_CASH = "CASH"
PAYABLE_STATUS_OPEN = "OPEN"
PAYABLE_STATUS_PARTIAL = "PARTIAL"
PAYABLE_STATUS_FULLY_ISSUED = "FULLY_ISSUED"
PAYABLE_STATUS_CANCELLED = "CANCELLED"
CHEQUE_STATUS_ISSUED = "ISSUED"
CHEQUE_STATUS_CLEARED = "CLEARED"
CHEQUE_STATUS_CANCELLED = "CANCELLED"
CHEQUE_STATUS_BOUNCED = "BOUNCED"
CASH_PAYMENT_STATUS_UNPAID = "UNPAID"
CASH_PAYMENT_STATUS_PAID = "PAID"
PAYABLE_CASH_SETTLEMENT_REFERENCE_TYPE = "PAYABLE_CASH_SETTLEMENT"
MAX_PAYEE_NAME_LENGTH = 160
MAX_DESCRIPTION_LENGTH = 500
MAX_REFERENCE_NO_LENGTH = 120
MAX_CHEQUE_NO_LENGTH = 120
MAX_NOTES_LENGTH = 500
PAYABLE_SEARCH_STATUS_OPTIONS = (
    PAYABLE_STATUS_OPEN,
    PAYABLE_STATUS_PARTIAL,
    PAYABLE_STATUS_FULLY_ISSUED,
    PAYABLE_STATUS_CANCELLED,
)


def _now():
    return now_local_str()


def _normalize_money(value):
    return round(float(value or 0), 2)


def _parse_money(raw_value, field_label):
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"{field_label} is required.")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_label} must be a valid amount.")
    if parsed.is_nan() or parsed.is_infinite():
        raise ValueError(f"{field_label} must be a valid amount.")
    return float(parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _clean_text(raw_value, field_label, *, required=False, max_length=None):
    value = str(raw_value or "").replace("\x00", "").strip()
    if required and not value:
        raise ValueError(f"{field_label} is required.")
    if max_length and len(value) > max_length:
        raise ValueError(f"{field_label} must be at most {max_length} characters.")
    return value


def _parse_iso_date(raw_value, field_label):
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"{field_label} is required.")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field_label} must be a valid date.")


def _payables_action_url(payable_id=None, cheque_id=None):
    params = []
    if payable_id is not None:
        params.append(f"payable_id={int(payable_id)}")
    if cheque_id is not None:
        params.append(f"cheque_id={int(cheque_id)}")
    if not params:
        return "/transaction/payables"
    return f"/transaction/payables?{'&'.join(params)}"


def _normalize_page(page):
    try:
        parsed = int(page or 1)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


def _escape_like(value):
    return str(value or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_payable_search_statuses(statuses):
    normalized = []
    for status in statuses or []:
        candidate = str(status or "").strip().upper()
        if candidate in PAYABLE_SEARCH_STATUS_OPTIONS and candidate not in normalized:
            normalized.append(candidate)
    return normalized or list(PAYABLE_SEARCH_STATUS_OPTIONS)


def _build_payable_audit_snapshot(payable_id, *, cheque_id=None, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT
                p.id AS payable_id,
                p.source_type,
                p.po_id,
                p.po_receipt_id,
                p.po_number_snapshot,
                p.payee_name,
                p.amount_due,
                pc.id AS cheque_id,
                pc.cheque_no,
                pc.cheque_amount
            FROM payables p
            LEFT JOIN payable_cheques pc ON pc.id = %s
            WHERE p.id = %s
            """,
            (int(cheque_id) if cheque_id is not None else None, int(payable_id)),
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        if not external_conn:
            conn.close()


def log_payables_audit_event(
    *,
    event_type,
    payable_id=None,
    cheque_id=None,
    source_type=None,
    po_id=None,
    po_receipt_id=None,
    po_number_snapshot=None,
    payee_name_snapshot=None,
    cheque_no_snapshot=None,
    amount_snapshot=None,
    old_status=None,
    new_status=None,
    notes=None,
    created_by=None,
    created_by_username=None,
    external_conn=None,
):
    conn = external_conn if external_conn else get_db()
    try:
        conn.execute(
            """
            INSERT INTO payables_audit_log (
                payable_id,
                cheque_id,
                event_type,
                source_type,
                po_id,
                po_receipt_id,
                po_number_snapshot,
                payee_name_snapshot,
                cheque_no_snapshot,
                amount_snapshot,
                old_status,
                new_status,
                notes,
                created_by,
                created_by_username,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                int(payable_id) if payable_id is not None else None,
                int(cheque_id) if cheque_id is not None else None,
                str(event_type or "").strip(),
                str(source_type or "").strip() or None,
                int(po_id) if po_id is not None else None,
                int(po_receipt_id) if po_receipt_id is not None else None,
                str(po_number_snapshot or "").strip() or None,
                str(payee_name_snapshot or "").strip() or None,
                str(cheque_no_snapshot or "").strip() or None,
                _normalize_money(amount_snapshot) if amount_snapshot is not None else None,
                str(old_status or "").strip() or None,
                str(new_status or "").strip() or None,
                str(notes or "").strip() or None,
                int(created_by) if created_by is not None else None,
                str(created_by_username or "").strip() or None,
                _now(),
            ),
        )
        if not external_conn:
            conn.commit()
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


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


def _sum_active_cash_payment_amount(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total_amount
            FROM payable_cash_payments
            WHERE payable_id = %s
              AND status = ANY(%s)
            """,
            (int(payable_id), list(ACTIVE_CASH_PAYMENT_STATUSES)),
        ).fetchone()
        return _normalize_money(row["total_amount"] if row else 0)
    finally:
        if not external_conn:
            conn.close()


def _sum_cash_settlement_amount(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total_amount
            FROM cash_entries
            WHERE reference_type = %s
              AND reference_id = %s
              AND entry_type = 'CASH_OUT'
              AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            (PAYABLE_CASH_SETTLEMENT_REFERENCE_TYPE, int(payable_id)),
        ).fetchone()
        return _normalize_money(row["total_amount"] if row else 0)
    finally:
        if not external_conn:
            conn.close()


def _has_cash_payments(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT EXISTS(
                SELECT 1
                FROM payable_cash_payments
                WHERE payable_id = %s
            ) AS has_cash_payments
            """,
            (int(payable_id),),
        ).fetchone()
        return bool(row["has_cash_payments"]) if row else False
    finally:
        if not external_conn:
            conn.close()


def _has_cancelled_cheques(payable_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT EXISTS(
                SELECT 1
                FROM payable_cheques
                WHERE payable_id = %s
                  AND status = %s
            ) AS has_cancelled
            """,
            (int(payable_id), CHEQUE_STATUS_CANCELLED),
        ).fetchone()
        return bool(row["has_cancelled"]) if row else False
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
        issued_amount = (
            _sum_active_cheque_amount(payable_id, external_conn=conn)
            + _sum_active_cash_payment_amount(payable_id, external_conn=conn)
            + _sum_cash_settlement_amount(payable_id, external_conn=conn)
        )
        has_cancelled_cheques = _has_cancelled_cheques(payable_id, external_conn=conn)

        if issued_amount <= 0 and has_cancelled_cheques:
            next_status = PAYABLE_STATUS_CANCELLED
        elif issued_amount <= 0:
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
        log_payables_audit_event(
            event_type="PO_PAYABLE_CREATED",
            payable_id=payable_id,
            source_type="PO_DELIVERY",
            po_id=receipt["po_id"],
            po_receipt_id=receipt["id"],
            po_number_snapshot=receipt["po_number"],
            payee_name_snapshot=receipt["vendor_name"] or "Supplier",
            amount_snapshot=receipt["total_amount"],
            new_status=PAYABLE_STATUS_OPEN,
            notes="Auto-created from PO delivery batch.",
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )

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
    payee_name = _clean_text(payee_name, "Payee", required=True, max_length=MAX_PAYEE_NAME_LENGTH)
    description = _clean_text(description, "Description", required=True, max_length=MAX_DESCRIPTION_LENGTH)
    reference_no = _clean_text(reference_no, "Reference no.", max_length=MAX_REFERENCE_NO_LENGTH)
    amount_due_value = _parse_money(amount_due, "Amount due")

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
        payable_id = int(row["id"])
        log_payables_audit_event(
            event_type="MANUAL_PAYABLE_CREATED",
            payable_id=payable_id,
            source_type="MANUAL",
            payee_name_snapshot=payee_name,
            amount_snapshot=amount_due_value,
            new_status=PAYABLE_STATUS_OPEN,
            notes=description,
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )
        conn.commit()
        return payable_id
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
    cheque_amount,
    notes=None,
    created_by=None,
    created_by_username=None,
):
    cheque_no = _clean_text(cheque_no, "Cheque number", required=True, max_length=MAX_CHEQUE_NO_LENGTH)
    notes = _clean_text(notes, "Notes", max_length=MAX_NOTES_LENGTH)
    cheque_amount_value = _parse_money(cheque_amount, "Cheque amount")
    cheque_date_value = _parse_iso_date(cheque_date, "Cheque date")
    due_date_value = cheque_date_value

    if cheque_amount_value <= 0:
        raise ValueError("Cheque amount must be greater than zero.")

    conn = get_db()
    try:
        payable = conn.execute(
            """
            SELECT id, source_type, po_id, po_receipt_id, po_number_snapshot, payee_name, amount_due, payment_method, status
            FROM payables
            WHERE id = %s
            FOR UPDATE
            """,
            (int(payable_id),),
        ).fetchone()
        if not payable:
            raise ValueError("Payable record not found.")
        if str(payable["status"] or "").strip().upper() == PAYABLE_STATUS_CANCELLED:
            raise ValueError("Cancelled payables cannot receive new cheques.")
        payment_method = str(payable["payment_method"] or "").strip().upper()
        if payment_method == PAYMENT_METHOD_CASH:
            raise ValueError("This payable is locked to cash payments and cannot receive cheques.")

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

        if not payment_method:
            conn.execute(
                """
                UPDATE payables
                SET payment_method = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (PAYMENT_METHOD_CHEQUE, _now(), int(payable_id)),
            )

        cheque_row = conn.execute(
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
            RETURNING id
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
        ).fetchone()
        cheque_id = int(cheque_row["id"])

        log_payables_audit_event(
            event_type="CHEQUE_ISSUED",
            payable_id=payable_id,
            cheque_id=cheque_id,
            source_type=payable["source_type"],
            po_id=payable["po_id"],
            po_receipt_id=payable["po_receipt_id"],
            po_number_snapshot=payable["po_number_snapshot"],
            payee_name_snapshot=payable["payee_name"],
            cheque_no_snapshot=cheque_no,
            amount_snapshot=cheque_amount_value,
            new_status=CHEQUE_STATUS_ISSUED,
            notes=notes,
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )

        sync_payable_status(payable_id, external_conn=conn)
        conn.commit()
        return cheque_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_payable_cheque_status(cheque_id, status, *, notes=None, created_by=None, created_by_username=None):
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in {
        CHEQUE_STATUS_ISSUED,
        CHEQUE_STATUS_CLEARED,
        CHEQUE_STATUS_CANCELLED,
        CHEQUE_STATUS_BOUNCED,
    }:
        raise ValueError("Invalid cheque status.")
    normalized_notes = _clean_text(notes, "Cancellation note", max_length=MAX_NOTES_LENGTH)

    conn = get_db()
    try:
        current = conn.execute(
            """
            SELECT
                pc.id,
                pc.payable_id,
                pc.cheque_no,
                pc.cheque_amount,
                pc.status,
                pc.notes,
                p.source_type,
                p.po_id,
                p.po_receipt_id,
                p.po_number_snapshot,
                p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.id = %s
            """,
            (int(cheque_id),),
        ).fetchone()
        if not current:
            raise ValueError("Cheque record not found.")
        current_status = str(current["status"] or "").strip().upper()
        if current_status in {CHEQUE_STATUS_CLEARED, CHEQUE_STATUS_CANCELLED} and normalized_status != current_status:
            raise ValueError(f"{current_status.title()} cheques are locked and can no longer be changed to another status.")
        if normalized_status == CHEQUE_STATUS_CANCELLED and not normalized_notes:
            raise ValueError("Cancellation note is required when cancelling a cheque.")
        if current_status == normalized_status:
            return

        next_notes = current["notes"] or None
        audit_notes = f"Cheque status updated from {current_status} to {normalized_status}."
        if normalized_status == CHEQUE_STATUS_CANCELLED:
            cancellation_note = f"Cancellation reason: {normalized_notes}"
            if next_notes:
                if cancellation_note not in next_notes:
                    next_notes = f"{next_notes}\n{cancellation_note}"
            else:
                next_notes = cancellation_note
            audit_notes = f"{audit_notes} {cancellation_note}"

        row = conn.execute(
            """
            UPDATE payable_cheques
            SET status = %s,
                notes = %s,
                updated_at = %s
            WHERE id = %s
            RETURNING payable_id
            """,
            (normalized_status, next_notes, _now(), int(cheque_id)),
        ).fetchone()
        log_payables_audit_event(
            event_type="CHEQUE_STATUS_UPDATED",
            payable_id=current["payable_id"],
            cheque_id=current["id"],
            source_type=current["source_type"],
            po_id=current["po_id"],
            po_receipt_id=current["po_receipt_id"],
            po_number_snapshot=current["po_number_snapshot"],
            payee_name_snapshot=current["payee_name"],
            cheque_no_snapshot=current["cheque_no"],
            amount_snapshot=current["cheque_amount"],
            old_status=current_status,
            new_status=normalized_status,
            notes=audit_notes,
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )

        sync_payable_status(int(row["payable_id"]), external_conn=conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _generate_cash_payment_ref(conn, payable):
    source_type = str(payable["source_type"] or "").strip().upper()
    po_number = str(payable["po_number_snapshot"] or "").strip()
    if source_type == "PO_DELIVERY" and po_number:
        return po_number

    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        candidate = "".join(secrets.choice(alphabet) for _ in range(5))
        duplicate = conn.execute(
            """
            SELECT id
            FROM payable_cash_payments
            WHERE payment_ref = %s
            LIMIT 1
            """,
            (candidate,),
        ).fetchone()
        if not duplicate:
            return candidate
    raise ValueError("Could not generate a unique payment reference. Please try again.")


def issue_payable_cash_payment(
    payable_id,
    *,
    payment_due_date,
    amount,
    notes=None,
    created_by=None,
    created_by_username=None,
):
    notes = _clean_text(notes, "Notes", max_length=MAX_NOTES_LENGTH)
    amount_value = _parse_money(amount, "Amount")
    due_date_value = _parse_iso_date(payment_due_date, "Payment due date")

    if amount_value <= 0:
        raise ValueError("Amount must be greater than zero.")

    conn = get_db()
    try:
        payable = conn.execute(
            """
            SELECT id, source_type, po_id, po_receipt_id, po_number_snapshot, payee_name, amount_due, payment_method, status
            FROM payables
            WHERE id = %s
            FOR UPDATE
            """,
            (int(payable_id),),
        ).fetchone()
        if not payable:
            raise ValueError("Payable record not found.")
        if str(payable["status"] or "").strip().upper() == PAYABLE_STATUS_CANCELLED:
            raise ValueError("Cancelled payables cannot receive new payments.")

        payment_method = str(payable["payment_method"] or "").strip().upper()
        if payment_method == PAYMENT_METHOD_CHEQUE:
            raise ValueError("This payable is locked to cheque payments and cannot receive cash payments.")

        issued_amount = _sum_active_cash_payment_amount(payable_id, external_conn=conn)
        remaining_balance = max(0.0, _normalize_money(payable["amount_due"]) - issued_amount)
        if amount_value > remaining_balance:
            raise ValueError(f"Payment amount exceeds the remaining balance of {remaining_balance:,.2f}.")

        if not payment_method:
            conn.execute(
                """
                UPDATE payables
                SET payment_method = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (PAYMENT_METHOD_CASH, _now(), int(payable_id)),
            )

        payment_ref = _generate_cash_payment_ref(conn, payable)
        row = conn.execute(
            """
            INSERT INTO payable_cash_payments (
                payable_id,
                payment_ref,
                payment_due_date,
                amount,
                status,
                notes,
                created_by,
                created_by_username,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                int(payable_id),
                payment_ref,
                due_date_value.isoformat(),
                amount_value,
                CASH_PAYMENT_STATUS_UNPAID,
                notes or None,
                int(created_by) if created_by else None,
                str(created_by_username or "").strip() or None,
                _now(),
                _now(),
            ),
        ).fetchone()
        payment_id = int(row["id"])

        log_payables_audit_event(
            event_type="CASH_PAYMENT_ISSUED",
            payable_id=payable_id,
            source_type=payable["source_type"],
            po_id=payable["po_id"],
            po_receipt_id=payable["po_receipt_id"],
            po_number_snapshot=payable["po_number_snapshot"],
            payee_name_snapshot=payable["payee_name"],
            cheque_no_snapshot=payment_ref,
            amount_snapshot=amount_value,
            new_status=CASH_PAYMENT_STATUS_UNPAID,
            notes=notes,
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )

        sync_payable_status(payable_id, external_conn=conn)
        conn.commit()
        return payment_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_payable_cash_payment_status(payment_id, status, *, created_by=None, created_by_username=None):
    normalized_status = str(status or "").strip().upper()
    if normalized_status not in {CASH_PAYMENT_STATUS_UNPAID, CASH_PAYMENT_STATUS_PAID}:
        raise ValueError("Invalid cash payment status.")

    conn = get_db()
    try:
        current = conn.execute(
            """
            SELECT
                pcp.id,
                pcp.payable_id,
                pcp.payment_ref,
                pcp.amount,
                pcp.status,
                p.source_type,
                p.po_id,
                p.po_receipt_id,
                p.po_number_snapshot,
                p.payee_name
            FROM payable_cash_payments pcp
            JOIN payables p ON p.id = pcp.payable_id
            WHERE pcp.id = %s
            """,
            (int(payment_id),),
        ).fetchone()
        if not current:
            raise ValueError("Cash payment record not found.")

        current_status = str(current["status"] or "").strip().upper()
        if current_status == CASH_PAYMENT_STATUS_PAID and normalized_status != current_status:
            raise ValueError("Paid cash payments are locked and can no longer be changed.")
        if current_status == normalized_status:
            return

        row = conn.execute(
            """
            UPDATE payable_cash_payments
            SET status = %s,
                updated_at = %s
            WHERE id = %s
            RETURNING payable_id
            """,
            (normalized_status, _now(), int(payment_id)),
        ).fetchone()

        log_payables_audit_event(
            event_type="CASH_PAYMENT_STATUS_UPDATED",
            payable_id=current["payable_id"],
            cheque_id=None,
            source_type=current["source_type"],
            po_id=current["po_id"],
            po_receipt_id=current["po_receipt_id"],
            po_number_snapshot=current["po_number_snapshot"],
            payee_name_snapshot=current["payee_name"],
            cheque_no_snapshot=current["payment_ref"],
            amount_snapshot=current["amount"],
            old_status=current_status,
            new_status=normalized_status,
            notes=f"Cash payment status updated from {current_status} to {normalized_status}.",
            created_by=created_by,
            created_by_username=created_by_username,
            external_conn=conn,
        )

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
        "cheque_no_raw": data["cheque_no"] or "",
        "cheque_date": format_date(cheque_date_value),
        "due_date": format_date(due_date_value),
        "cheque_amount": _normalize_money(data["cheque_amount"]),
        "status": data["status"] or CHEQUE_STATUS_ISSUED,
        "notes": data["notes"] or "",
        "is_due_today": bool(due_date_obj and due_date_obj == today_value and (data["status"] or "").upper() == CHEQUE_STATUS_ISSUED),
        "is_due_soon": bool(due_date_obj and today_value < due_date_obj <= today_value + timedelta(days=7) and (data["status"] or "").upper() == CHEQUE_STATUS_ISSUED),
    }


def _serialize_cash_payment_row(row, today_value):
    data = dict(row)
    due_date_value = data.get("payment_due_date")
    due_date_obj = due_date_value if isinstance(due_date_value, date) else None
    if not due_date_obj:
        try:
            due_date_obj = datetime.strptime(str(due_date_value), "%Y-%m-%d").date()
        except ValueError:
            due_date_obj = None

    return {
        "id": int(data["id"]),
        "payment_ref": data["payment_ref"] or "-",
        "payment_ref_raw": data["payment_ref"] or "",
        "payment_due_date": format_date(due_date_value),
        "amount": _normalize_money(data["amount"]),
        "status": data["status"] or CASH_PAYMENT_STATUS_UNPAID,
        "notes": data["notes"] or "",
        "is_due_today": bool(due_date_obj and due_date_obj == today_value and (data["status"] or "").upper() == CASH_PAYMENT_STATUS_UNPAID),
        "is_due_soon": bool(due_date_obj and today_value < due_date_obj <= today_value + timedelta(days=7) and (data["status"] or "").upper() == CASH_PAYMENT_STATUS_UNPAID),
    }


def _to_sort_timestamp(value):
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).timestamp()
    if value:
        raw_value = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw_value, fmt).timestamp()
            except ValueError:
                continue
    return None


def _serialize_payable_summary_row(row):
    amount_due = _normalize_money(row["amount_due"])
    issued_amount = _normalize_money(row["issued_amount"])
    cash_paid_amount = _normalize_money(row.get("cash_paid_amount"))
    remaining_balance = max(0.0, amount_due - issued_amount)
    total_cheque_count = int(row.get("total_cheque_count") or 0)
    cleared_cheque_count = int(row.get("cleared_cheque_count") or 0)
    uncleared_cheque_count = int(row.get("uncleared_cheque_count") or 0)
    total_cash_payment_count = int(row.get("total_cash_payment_count") or 0)
    paid_cash_payment_count = int(row.get("paid_cash_payment_count") or 0)
    unpaid_cash_payment_count = int(row.get("unpaid_cash_payment_count") or 0)
    current_month_cheque_count = int(row.get("current_month_cheque_count") or 0)
    current_month_cash_payment_count = int(row.get("current_month_cash_payment_count") or 0)
    due_this_month_count = int(row.get("due_this_month_count") or 0) + int(row.get("due_this_month_cash_payment_count") or 0)
    due_soon_count = int(row.get("due_soon_cheque_count") or 0) + int(row.get("due_soon_cash_payment_count") or 0)
    due_today_count = int(row.get("due_today_cheque_count") or 0) + int(row.get("due_today_cash_payment_count") or 0)
    latest_cheque_status = row.get("latest_cheque_status") or "-"
    latest_cash_payment_status = row.get("latest_cash_payment_status") or "-"
    latest_cancelled_cheque_amount = _normalize_money(row.get("latest_cancelled_cheque_amount"))
    nearest_cheque_date = row.get("nearest_cheque_date")
    nearest_cheque_distance = row.get("nearest_cheque_distance")
    priority_cheque_due_date = row.get("priority_cheque_due_date")
    status = row["status"] or PAYABLE_STATUS_OPEN
    normalized_status = str(status).strip().upper()
    is_fully_cleared = bool(
        normalized_status == PAYABLE_STATUS_FULLY_ISSUED
        and total_cheque_count > 0
        and total_cash_payment_count == 0
        and uncleared_cheque_count == 0
        and cleared_cheque_count == total_cheque_count
    )
    is_fully_cash_paid = bool(
        normalized_status == PAYABLE_STATUS_FULLY_ISSUED
        and total_cash_payment_count > 0
        and total_cheque_count == 0
        and unpaid_cash_payment_count == 0
        and paid_cash_payment_count == total_cash_payment_count
    )
    is_history_entry = bool(is_fully_cleared or is_fully_cash_paid or normalized_status == PAYABLE_STATUS_CANCELLED)

    return {
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
        "cash_paid_amount": cash_paid_amount,
        "remaining_balance": remaining_balance,
        "status": status,
        "payment_method": row.get("payment_method") or "",
        "cash_payment_amount": latest_cancelled_cheque_amount,
        "is_cash_paid": cash_paid_amount > 0,
        "latest_due_date": format_date(row["latest_due_date"]),
        "latest_cheque_status": latest_cheque_status,
        "latest_cash_payment_status": latest_cash_payment_status,
        "latest_payment_status": latest_cash_payment_status if total_cash_payment_count > 0 else latest_cheque_status,
        "nearest_cheque_date": format_date(nearest_cheque_date),
        "nearest_cheque_distance": int(nearest_cheque_distance) if nearest_cheque_distance is not None else None,
        "priority_cheque_due_date": format_date(priority_cheque_due_date),
        "priority_cheque_due_sort": _to_sort_timestamp(priority_cheque_due_date),
        "sort_anchor_ts": _to_sort_timestamp(row.get("delivery_received_at_snapshot") or row.get("created_at")),
        "created_at": format_date(row["created_at"], show_time=True),
        "cheque_count": total_cheque_count,
        "cash_payment_count": total_cash_payment_count,
        "payment_count": total_cheque_count + total_cash_payment_count,
        "cleared_cheque_count": cleared_cheque_count,
        "uncleared_cheque_count": uncleared_cheque_count,
        "paid_cash_payment_count": paid_cash_payment_count,
        "unpaid_cash_payment_count": unpaid_cash_payment_count,
        "current_month_cheque_count": current_month_cheque_count,
        "current_month_cash_payment_count": current_month_cash_payment_count,
        "current_month_payment_count": current_month_cheque_count + current_month_cash_payment_count,
        "due_this_month_count": due_this_month_count,
        "due_soon_count": due_soon_count,
        "due_today_count": due_today_count,
        "is_fully_cleared": is_fully_cleared,
        "is_fully_cash_paid": is_fully_cash_paid,
        "is_history_entry": is_history_entry,
    }


def _payable_history_anchor(row):
    for field_name in ("latest_due_date", "delivery_received_at_snapshot", "created_at"):
        value = row.get(field_name)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if value:
            raw_value = str(value).strip()
            for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw_value, fmt)
                    return parsed.date()
                except ValueError:
                    continue
    return today_local()


def _get_payable_summary_rows(search_query=None, statuses=None):
    today_value = today_local()
    month_start = today_value.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)

    query_value = str(search_query or "").strip()
    selected_statuses = _normalize_payable_search_statuses(statuses)
    conn = get_db()
    try:
        conditions = ["p.status = ANY(%s)"]
        aggregate_params = [
            list(ACTIVE_CHEQUE_STATUSES),
            list(ACTIVE_CASH_PAYMENT_STATUSES),
            PAYABLE_CASH_SETTLEMENT_REFERENCE_TYPE,
            CASH_PAYMENT_STATUS_PAID,
            PAYABLE_CASH_SETTLEMENT_REFERENCE_TYPE,
            PAYABLE_CASH_SETTLEMENT_REFERENCE_TYPE,
            CHEQUE_STATUS_ISSUED,
            (today_value + timedelta(days=7)).isoformat(),
            CHEQUE_STATUS_CANCELLED,
            today_value.isoformat(),
            today_value.isoformat(),
            CHEQUE_STATUS_CANCELLED,
            today_value.isoformat(),
            CHEQUE_STATUS_CANCELLED,
            month_start.isoformat(),
            next_month_start.isoformat(),
            CHEQUE_STATUS_ISSUED,
            today_value.isoformat(),
            CHEQUE_STATUS_ISSUED,
            today_value.isoformat(),
            (today_value + timedelta(days=7)).isoformat(),
            CHEQUE_STATUS_ISSUED,
            month_start.isoformat(),
            next_month_start.isoformat(),
            CHEQUE_STATUS_CLEARED,
            CHEQUE_STATUS_CLEARED,
            CASH_PAYMENT_STATUS_UNPAID,
            month_start.isoformat(),
            next_month_start.isoformat(),
            CASH_PAYMENT_STATUS_UNPAID,
            today_value.isoformat(),
            CASH_PAYMENT_STATUS_UNPAID,
            today_value.isoformat(),
            (today_value + timedelta(days=7)).isoformat(),
            CASH_PAYMENT_STATUS_UNPAID,
            month_start.isoformat(),
            next_month_start.isoformat(),
            CASH_PAYMENT_STATUS_PAID,
            CASH_PAYMENT_STATUS_PAID,
        ]
        search_params = [selected_statuses]

        if query_value:
            conditions.append(
                """
                (
                    p.payee_name ILIKE %s ESCAPE '\\'
                    OR COALESCE(p.vendor_name_snapshot, '') ILIKE %s ESCAPE '\\'
                    OR COALESCE(p.po_number_snapshot, '') ILIKE %s ESCAPE '\\'
                    OR EXISTS (
                        SELECT 1
                        FROM payable_cheques pc_search
                        WHERE pc_search.payable_id = p.id
                          AND pc_search.cheque_no ILIKE %s ESCAPE '\\'
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM payable_cash_payments pcp_search
                        WHERE pcp_search.payable_id = p.id
                          AND pcp_search.payment_ref ILIKE %s ESCAPE '\\'
                    )
                )
                """
            )
            like_value = f"%{_escape_like(query_value)}%"
            search_params.extend([like_value, like_value, like_value, like_value, like_value])

        where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        payable_rows = conn.execute(
            f"""
            SELECT
                p.*,
                COALESCE(SUM(CASE WHEN pc.status = ANY(%s) THEN pc.cheque_amount ELSE 0 END), 0)
                + COALESCE((
                    SELECT SUM(pcp_issued.amount)
                    FROM payable_cash_payments pcp_issued
                    WHERE pcp_issued.payable_id = p.id
                      AND pcp_issued.status = ANY(%s)
                ), 0)
                + COALESCE((
                    SELECT SUM(ce_payable.amount)
                    FROM cash_entries ce_payable
                    WHERE ce_payable.reference_type = %s
                      AND ce_payable.reference_id = p.id
                      AND ce_payable.entry_type = 'CASH_OUT'
                      AND COALESCE(ce_payable.is_deleted, FALSE) = FALSE
                ), 0) AS issued_amount,
                COALESCE((
                    SELECT SUM(pcp_paid.amount)
                    FROM payable_cash_payments pcp_paid
                    WHERE pcp_paid.payable_id = p.id
                      AND pcp_paid.status = %s
                ), 0)
                + COALESCE((
                    SELECT SUM(ce_payable.amount)
                    FROM cash_entries ce_payable
                    WHERE ce_payable.reference_type = %s
                      AND ce_payable.reference_id = p.id
                      AND ce_payable.entry_type = 'CASH_OUT'
                      AND COALESCE(ce_payable.is_deleted, FALSE) = FALSE
                ), 0) AS cash_paid_amount,
                COALESCE((
                    SELECT SUM(ce_payable.amount)
                    FROM cash_entries ce_payable
                    WHERE ce_payable.reference_type = %s
                      AND ce_payable.reference_id = p.id
                      AND ce_payable.entry_type = 'CASH_OUT'
                      AND COALESCE(ce_payable.is_deleted, FALSE) = FALSE
                ), 0) AS legacy_cash_paid_amount,
                GREATEST(
                    MAX(pc.due_date),
                    (
                        SELECT MAX(pcp_due.payment_due_date)
                        FROM payable_cash_payments pcp_due
                        WHERE pcp_due.payable_id = p.id
                    )
                ) AS latest_due_date,
                (
                    SELECT pc_priority.due_date
                    FROM payable_cheques pc_priority
                    WHERE pc_priority.payable_id = p.id
                      AND pc_priority.status = %s
                      AND pc_priority.due_date <= %s
                    ORDER BY pc_priority.due_date ASC, pc_priority.id ASC
                    LIMIT 1
                ) AS priority_cheque_due_date,
                (
                    SELECT pc_nearest.due_date
                    FROM payable_cheques pc_nearest
                    WHERE pc_nearest.payable_id = p.id
                      AND pc_nearest.status <> %s
                    ORDER BY ABS(pc_nearest.due_date - %s) ASC, pc_nearest.due_date ASC, pc_nearest.id ASC
                    LIMIT 1
                ) AS nearest_cheque_date,
                (
                    SELECT ABS(pc_nearest.due_date - %s)
                    FROM payable_cheques pc_nearest
                    WHERE pc_nearest.payable_id = p.id
                      AND pc_nearest.status <> %s
                    ORDER BY ABS(pc_nearest.due_date - %s) ASC, pc_nearest.due_date ASC, pc_nearest.id ASC
                    LIMIT 1
                ) AS nearest_cheque_distance,
                COUNT(pc.id) AS total_cheque_count,
                COUNT(CASE WHEN pc.status <> %s AND pc.due_date >= %s AND pc.due_date < %s THEN 1 END) AS current_month_cheque_count,
                COUNT(CASE WHEN pc.status = %s AND pc.due_date = %s THEN 1 END) AS due_today_cheque_count,
                COUNT(CASE WHEN pc.status = %s AND pc.due_date > %s AND pc.due_date <= %s THEN 1 END) AS due_soon_cheque_count,
                COUNT(CASE WHEN pc.status = %s AND pc.due_date >= %s AND pc.due_date < %s THEN 1 END) AS due_this_month_count,
                COUNT(CASE WHEN pc.status = %s THEN 1 END) AS cleared_cheque_count,
                COUNT(CASE WHEN pc.id IS NOT NULL AND pc.status <> %s THEN 1 END) AS uncleared_cheque_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                ) AS total_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status = %s
                      AND pcp_count.payment_due_date >= %s
                      AND pcp_count.payment_due_date < %s
                ) AS current_month_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status = %s
                      AND pcp_count.payment_due_date = %s
                ) AS due_today_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status = %s
                      AND pcp_count.payment_due_date > %s
                      AND pcp_count.payment_due_date <= %s
                ) AS due_soon_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status = %s
                      AND pcp_count.payment_due_date >= %s
                      AND pcp_count.payment_due_date < %s
                ) AS due_this_month_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status = %s
                ) AS paid_cash_payment_count,
                (
                    SELECT COUNT(*)
                    FROM payable_cash_payments pcp_count
                    WHERE pcp_count.payable_id = p.id
                      AND pcp_count.status <> %s
                ) AS unpaid_cash_payment_count,
                (
                    SELECT pc_latest.status
                    FROM payable_cheques pc_latest
                    WHERE pc_latest.payable_id = p.id
                    ORDER BY pc_latest.created_at DESC, pc_latest.id DESC
                    LIMIT 1
                ) AS latest_cheque_status,
                (
                    SELECT pcp_latest.status
                    FROM payable_cash_payments pcp_latest
                    WHERE pcp_latest.payable_id = p.id
                    ORDER BY pcp_latest.created_at DESC, pcp_latest.id DESC
                    LIMIT 1
                ) AS latest_cash_payment_status,
                (
                    SELECT pc_cancelled.cheque_amount
                    FROM payable_cheques pc_cancelled
                    WHERE pc_cancelled.payable_id = p.id
                      AND pc_cancelled.status = %s
                    ORDER BY pc_cancelled.updated_at DESC NULLS LAST, pc_cancelled.created_at DESC, pc_cancelled.id DESC
                    LIMIT 1
                ) AS latest_cancelled_cheque_amount
            FROM payables p
            LEFT JOIN payable_cheques pc ON pc.payable_id = p.id
            {where_sql}
            GROUP BY p.id
            ORDER BY
                COALESCE(p.delivery_received_at_snapshot, p.created_at) DESC,
                p.id DESC
            """,
            aggregate_params + [CHEQUE_STATUS_CANCELLED] + search_params,
        ).fetchall()
    finally:
        conn.close()

    return payable_rows


def get_payables_page_context(search_query=None, statuses=None):
    explicit_statuses = []
    for status in statuses or []:
        candidate = str(status or "").strip().upper()
        if candidate in PAYABLE_SEARCH_STATUS_OPTIONS and candidate not in explicit_statuses:
            explicit_statuses.append(candidate)

    payable_rows = _get_payable_summary_rows(search_query=search_query, statuses=explicit_statuses)
    query_value = str(search_query or "").strip()

    po_based_payables = []
    manual_payables = []
    history_total_count = 0
    total_remaining = 0.0
    open_count = 0
    due_soon_count = 0
    due_today_count = 0

    for row in payable_rows:
        payable_data = _serialize_payable_summary_row(row)
        if payable_data["is_history_entry"]:
            history_total_count += 1
        else:
            # When the user explicitly filters by payable status, honor that
            # selection even if the payable would normally be hidden by the
            # default cheque-timing rules of the active view.
            should_show_in_active = bool(
                explicit_statuses
                or payable_data["payment_count"] == 0
                or payable_data["current_month_payment_count"] > 0
                or payable_data["due_soon_count"] > 0
                or payable_data["due_today_count"] > 0
                or query_value
            )
            if not should_show_in_active:
                continue

            total_remaining += payable_data["remaining_balance"]
            if payable_data["status"] in {PAYABLE_STATUS_OPEN, PAYABLE_STATUS_PARTIAL}:
                open_count += 1
            due_soon_count += payable_data["due_soon_count"]
            due_today_count += payable_data["due_today_count"]

            if row["source_type"] == "PO_DELIVERY":
                po_based_payables.append(payable_data)
            else:
                manual_payables.append(payable_data)

    def _active_sort_key(payable):
        priority_due_sort = payable.get("priority_cheque_due_sort")
        sort_anchor_ts = payable.get("sort_anchor_ts") or 0
        fallback_id = int(payable.get("id") or 0)
        return (
            0 if priority_due_sort is not None else 1,
            priority_due_sort if priority_due_sort is not None else float("inf"),
            -sort_anchor_ts,
            -fallback_id,
        )

    po_based_payables.sort(key=_active_sort_key)
    manual_payables.sort(key=_active_sort_key)

    return {
        "summary": {
            "open_count": open_count,
            "total_remaining": round(total_remaining, 2),
            "due_soon_count": due_soon_count,
            "due_today_count": due_today_count,
        },
        "po_based_payables": po_based_payables,
        "manual_payables": manual_payables,
        "active_total_count": len(po_based_payables) + len(manual_payables),
        "history_total_count": history_total_count,
        "search_query": query_value,
        "selected_statuses": explicit_statuses,
        "has_status_filter": bool(explicit_statuses),
        "payable_status_filter_options": list(PAYABLE_SEARCH_STATUS_OPTIONS),
        "today": today_local().isoformat(),
    }


def get_payables_history_month_summaries(search_query=None, statuses=None):
    rows = _get_payable_summary_rows(search_query=search_query, statuses=statuses)
    current_month_key = today_local().strftime("%Y-%m")
    groups_map = {}

    for row in rows:
        payable_data = _serialize_payable_summary_row(row)
        if not payable_data["is_history_entry"]:
            continue
        anchor_date = _payable_history_anchor(row)
        month_key = anchor_date.strftime("%Y-%m")
        group = groups_map.setdefault(
            month_key,
            {
                "key": month_key,
                "label": anchor_date.strftime("%B %Y"),
                "sort_date": anchor_date.replace(day=1),
                "payable_count": 0,
                "is_current_month": month_key == current_month_key,
            },
        )
        group["payable_count"] += 1

    groups = sorted(groups_map.values(), key=lambda item: item["sort_date"], reverse=True)
    for group in groups:
        del group["sort_date"]

    return {
        "groups": groups,
        "total_count": sum(group["payable_count"] for group in groups),
        "current_month_key": current_month_key,
    }


def get_payables_history_by_month(month_key, search_query=None, statuses=None):
    normalized_month_key = str(month_key or "").strip()
    try:
        month_anchor = datetime.strptime(normalized_month_key, "%Y-%m").date()
    except ValueError:
        raise ValueError("Invalid payables history month.")

    rows = _get_payable_summary_rows(search_query=search_query, statuses=statuses)
    payables = []
    for row in rows:
        payable_data = _serialize_payable_summary_row(row)
        if not payable_data["is_history_entry"]:
            continue
        if _payable_history_anchor(row).strftime("%Y-%m") != normalized_month_key:
            continue
        payables.append(payable_data)

    payables.sort(
        key=lambda item: (
            item["latest_due_date"] or "",
            item["created_at"] or "",
            item["id"],
        ),
        reverse=True,
    )

    return {
        "month_key": normalized_month_key,
        "month_label": month_anchor.strftime("%B %Y"),
        "payables": payables,
        "payable_count": len(payables),
    }


def get_payable_cheque_history(payable_id):
    today_value = today_local()
    month_start = today_value.replace(day=1)
    if month_start.month == 12:
        next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month_start = month_start.replace(month=month_start.month + 1, day=1)

    conn = get_db()
    try:
        payable = conn.execute(
            """
            SELECT id, payee_name, source_type, po_number_snapshot, payment_method
            FROM payables
            WHERE id = %s
            """,
            (int(payable_id),),
        ).fetchone()
        if not payable:
            raise ValueError("Payable record not found.")

        rows = conn.execute(
            """
            SELECT *
            FROM payable_cheques
            WHERE payable_id = %s
            ORDER BY due_date ASC, id ASC
            """,
            (int(payable_id),),
        ).fetchall()
        cash_rows = conn.execute(
            """
            SELECT *
            FROM payable_cash_payments
            WHERE payable_id = %s
            ORDER BY payment_due_date ASC, id ASC
            """,
            (int(payable_id),),
        ).fetchall()
    finally:
        conn.close()

    current_month_due = []
    other_history = []
    for row in rows:
        serialized = _serialize_cheque_row(row, today_value)
        due_date_value = row["due_date"]
        due_date_obj = due_date_value if isinstance(due_date_value, date) else None
        if not due_date_obj:
            try:
                due_date_obj = datetime.strptime(str(due_date_value), "%Y-%m-%d").date()
            except ValueError:
                due_date_obj = None

        is_current_month_due = bool(
            due_date_obj
            and month_start <= due_date_obj < next_month_start
            and str(row["status"] or "").strip().upper() == CHEQUE_STATUS_ISSUED
        )
        if is_current_month_due:
            current_month_due.append(serialized)
        else:
            other_history.append(serialized)

    current_month_cash_due = []
    other_cash_history = []
    for row in cash_rows:
        serialized = _serialize_cash_payment_row(row, today_value)
        due_date_value = row["payment_due_date"]
        due_date_obj = due_date_value if isinstance(due_date_value, date) else None
        if not due_date_obj:
            try:
                due_date_obj = datetime.strptime(str(due_date_value), "%Y-%m-%d").date()
            except ValueError:
                due_date_obj = None

        is_current_month_due = bool(
            due_date_obj
            and month_start <= due_date_obj < next_month_start
            and str(row["status"] or "").strip().upper() == CASH_PAYMENT_STATUS_UNPAID
        )
        if is_current_month_due:
            current_month_cash_due.append(serialized)
        else:
            other_cash_history.append(serialized)

    return {
        "payable_id": int(payable["id"]),
        "payee_name": payable["payee_name"] or "-",
        "source_type": payable["source_type"] or "MANUAL",
        "po_number_snapshot": payable["po_number_snapshot"] or "",
        "payment_method": payable["payment_method"] or "",
        "current_month_due": current_month_due,
        "other_history": other_history,
        "current_month_cash_due": current_month_cash_due,
        "other_cash_history": other_cash_history,
        "counts": {
            "current_month_due": len(current_month_due),
            "other_history": len(other_history),
            "current_month_cash_due": len(current_month_cash_due),
            "other_cash_history": len(other_cash_history),
            "total": len(current_month_due) + len(other_history) + len(current_month_cash_due) + len(other_cash_history),
        },
    }


def build_payables_report_context(start_date=None, end_date=None):
    start_value = str(start_date or "").strip()
    end_value = str(end_date or "").strip()

    today_value = today_local()
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
                pc.updated_at,
                pc.notes,
                p.source_type,
                p.payee_name,
                p.description,
                p.po_number_snapshot,
                p.delivery_received_at_snapshot
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE (
                pc.due_date BETWEEN %s AND %s
                OR (
                    pc.status = %s
                    AND DATE(pc.updated_at) BETWEEN %s AND %s
                )
            )
            ORDER BY pc.due_date ASC, pc.id ASC
            """,
            (
                start_value,
                end_value,
                CHEQUE_STATUS_CLEARED,
                start_value,
                end_value,
            ),
        ).fetchall()
        cash_rows = conn.execute(
            """
            SELECT
                pcp.id,
                pcp.payment_ref,
                pcp.payment_due_date,
                pcp.amount,
                pcp.status,
                pcp.updated_at,
                pcp.notes,
                p.source_type,
                p.payee_name,
                p.description,
                p.po_number_snapshot,
                p.delivery_received_at_snapshot
            FROM payable_cash_payments pcp
            JOIN payables p ON p.id = pcp.payable_id
            WHERE (
                pcp.payment_due_date BETWEEN %s AND %s
                OR (
                    pcp.status = %s
                    AND DATE(pcp.updated_at) BETWEEN %s AND %s
                )
            )
            ORDER BY pcp.payment_due_date ASC, pcp.id ASC
            """,
            (
                start_value,
                end_value,
                CASH_PAYMENT_STATUS_PAID,
                start_value,
                end_value,
            ),
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
            "cheque_date": format_date(row["due_date"]),
            "sort_due_date": row["due_date"].isoformat() if row["due_date"] else "",
            "cleared_at": (
                format_date(row["updated_at"], show_time=True)
                if str(row["status"] or "").strip().upper() == CHEQUE_STATUS_CLEARED
                else ""
            ),
            "cheque_amount": amount,
            "status": row["status"] or CHEQUE_STATUS_ISSUED,
            "payee_name": row["payee_name"] or "-",
            "description": row["description"] or "",
            "source_type": row["source_type"] or "MANUAL",
            "po_number_snapshot": row["po_number_snapshot"] or "",
            "delivery_received_at_snapshot": format_date(row["delivery_received_at_snapshot"], show_time=True),
            "notes": row["notes"] or "",
            "payment_type": "CHEQUE",
        })

    for row in cash_rows:
        amount = _normalize_money(row["amount"])
        total_amount += amount
        items.append({
            "id": int(row["id"]),
            "cheque_no": row["payment_ref"] or "-",
            "cheque_date": format_date(row["payment_due_date"]),
            "sort_due_date": row["payment_due_date"].isoformat() if row["payment_due_date"] else "",
            "cleared_at": (
                format_date(row["updated_at"], show_time=True)
                if str(row["status"] or "").strip().upper() == CASH_PAYMENT_STATUS_PAID
                else ""
            ),
            "cheque_amount": amount,
            "status": row["status"] or CASH_PAYMENT_STATUS_UNPAID,
            "payee_name": row["payee_name"] or "-",
            "description": row["description"] or "",
            "source_type": row["source_type"] or "MANUAL",
            "po_number_snapshot": row["po_number_snapshot"] or "",
            "delivery_received_at_snapshot": format_date(row["delivery_received_at_snapshot"], show_time=True),
            "notes": row["notes"] or "",
            "payment_type": "CASH",
        })

    items.sort(key=lambda item: (item["sort_due_date"] or "", item["payment_type"], item["id"]))

    return {
        "report_title": "Payables Payment Report",
        "date_label": f"{format_date(start_value)} to {format_date(end_value)}",
        "generated_at": format_date(now_local(), show_time=True),
        "items": items,
        "total_amount": round(total_amount, 2),
        "start_date": start_value,
        "end_date": end_value,
    }


def get_cleared_cheque_entries_for_report(start_date, end_date):
    start_value = str(start_date or "").strip()
    end_value = str(end_date or "").strip()
    if not start_value or not end_value:
        return []

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                pc.id,
                pc.cheque_no,
                pc.cheque_amount,
                pc.updated_at AS cleared_at,
                p.payee_name,
                COALESCE(
                    (
                        SELECT pal.created_by_username
                        FROM payables_audit_log pal
                        WHERE pal.cheque_id = pc.id
                          AND pal.event_type = 'CHEQUE_STATUS_UPDATED'
                          AND pal.new_status = %s
                        ORDER BY pal.created_at DESC, pal.id DESC
                        LIMIT 1
                    ),
                    pc.created_by_username,
                    'System'
                ) AS cleared_by
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.status = %s
              AND DATE(pc.updated_at) BETWEEN %s AND %s
            ORDER BY pc.updated_at ASC, pc.id ASC
            """,
            (CHEQUE_STATUS_CLEARED, CHEQUE_STATUS_CLEARED, start_value, end_value),
        ).fetchall()
    finally:
        conn.close()

    entries = []
    for row in rows:
        cheque_no = str(row["cheque_no"] or "").strip() or "-"
        payee_name = str(row["payee_name"] or "").strip() or "Payee"
        entries.append({
            "id": f"cheque-cleared-{int(row['id'])}",
            "entry_type": "CASH_OUT",
            "amount": _normalize_money(row["cheque_amount"]),
            "category": "Cheque cleared (via Bank)",
            "description": f"Cheque #{cheque_no} — {payee_name}",
            "created_at": format_date(row["cleared_at"], show_time=True),
            "recorded_by": row["cleared_by"] or "System",
            "source": "cheque_cleared",
            "_sort_at": row["cleared_at"],
        })

    return entries


def get_paid_cash_payment_entries_for_report(start_date, end_date):
    start_value = str(start_date or "").strip()
    end_value = str(end_date or "").strip()
    if not start_value or not end_value:
        return []

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                pcp.id,
                pcp.payment_ref,
                pcp.amount,
                pcp.updated_at AS paid_at,
                p.payee_name,
                COALESCE(
                    (
                        SELECT pal.created_by_username
                        FROM payables_audit_log pal
                        WHERE pal.cheque_no_snapshot = pcp.payment_ref
                          AND pal.event_type = 'CASH_PAYMENT_STATUS_UPDATED'
                          AND pal.new_status = %s
                        ORDER BY pal.created_at DESC, pal.id DESC
                        LIMIT 1
                    ),
                    pcp.created_by_username,
                    'System'
                ) AS paid_by
            FROM payable_cash_payments pcp
            JOIN payables p ON p.id = pcp.payable_id
            WHERE pcp.status = %s
              AND DATE(pcp.updated_at) BETWEEN %s AND %s
            ORDER BY pcp.updated_at ASC, pcp.id ASC
            """,
            (CASH_PAYMENT_STATUS_PAID, CASH_PAYMENT_STATUS_PAID, start_value, end_value),
        ).fetchall()
    finally:
        conn.close()

    entries = []
    for row in rows:
        payment_ref = str(row["payment_ref"] or "").strip() or "-"
        payee_name = str(row["payee_name"] or "").strip() or "Payee"
        entries.append({
            "id": f"cash-payment-paid-{int(row['id'])}",
            "entry_type": "CASH_OUT",
            "amount": _normalize_money(row["amount"]),
            "category": "Payable paid by cash",
            "description": f"Payment {payment_ref} - {payee_name}",
            "created_at": format_date(row["paid_at"], show_time=True),
            "recorded_by": row["paid_by"] or "System",
            "source": "payable_cash_paid",
            "_sort_at": row["paid_at"],
        })

    return entries


def get_payables_audit_log(
    *,
    page=1,
    start_date=None,
    end_date=None,
    event_type=None,
    source_type=None,
    payee_search=None,
    cheque_no_search=None,
    per_page=20,
):
    current_page = _normalize_page(page)
    offset = (current_page - 1) * per_page
    params = []
    conditions = []

    start_value = str(start_date or "").strip() or None
    end_value = str(end_date or "").strip() or None
    if start_value:
        conditions.append("DATE(created_at) >= %s")
        params.append(start_value)
    if end_value:
        conditions.append("DATE(created_at) <= %s")
        params.append(end_value)

    event_value = str(event_type or "").strip().upper() or None
    valid_event_types = {
        "PO_PAYABLE_CREATED",
        "MANUAL_PAYABLE_CREATED",
        "CHEQUE_ISSUED",
        "CHEQUE_STATUS_UPDATED",
        "CASH_PAYMENT_ISSUED",
        "CASH_PAYMENT_STATUS_UPDATED",
        "PAYABLE_CASH_SETTLED",
    }
    if event_value:
        if event_value not in valid_event_types:
            raise ValueError("Invalid payables audit event type.")
        conditions.append("event_type = %s")
        params.append(event_value)

    source_value = str(source_type or "").strip().upper() or None
    valid_source_types = {"PO_DELIVERY", "MANUAL"}
    if source_value:
        if source_value not in valid_source_types:
            raise ValueError("Invalid payables audit source type.")
        conditions.append("source_type = %s")
        params.append(source_value)

    payee_value = str(payee_search or "").strip()
    if payee_value:
        conditions.append("payee_name_snapshot ILIKE %s ESCAPE '\\'")
        params.append(f"%{_escape_like(payee_value)}%")

    cheque_value = str(cheque_no_search or "").strip()
    if cheque_value:
        conditions.append("cheque_no_snapshot ILIKE %s ESCAPE '\\'")
        params.append(f"%{_escape_like(cheque_value)}%")

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    conn = get_db()
    try:
        total_row = conn.execute(
            f"""
            SELECT COUNT(*) AS total_count
            FROM payables_audit_log
            {where_sql}
            """,
            params,
        ).fetchone()
        total = int(total_row["total_count"] or 0)
        total_pages = max(1, (total + per_page - 1) // per_page)
        if current_page > total_pages:
            current_page = total_pages
            offset = (current_page - 1) * per_page

        rows = conn.execute(
            f"""
            SELECT *
            FROM payables_audit_log
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        ).fetchall()
    finally:
        conn.close()

    serialized_rows = []
    for row in rows:
        data = dict(row)
        event_type_value = data.get("event_type") or "-"
        source_type_value = data.get("source_type") or "-"
        serialized_rows.append({
            "id": int(data["id"]),
            "created_at": format_date(data.get("created_at"), show_time=True),
            "event_type": event_type_value,
            "event_label": {
                "PO_PAYABLE_CREATED": "PO Payable Created",
                "MANUAL_PAYABLE_CREATED": "Manual Payable Created",
                "CHEQUE_ISSUED": "Cheque Issued",
                "CHEQUE_STATUS_UPDATED": "Cheque Status Updated",
                "CASH_PAYMENT_ISSUED": "Cash Payment Issued",
                "CASH_PAYMENT_STATUS_UPDATED": "Cash Payment Status Updated",
                "PAYABLE_CASH_SETTLED": "Payable Cash Settled",
            }.get(event_type_value, event_type_value.replace("_", " ").title()),
            "source_type": source_type_value,
            "source_label": "PO Delivery" if source_type_value == "PO_DELIVERY" else ("Manual" if source_type_value == "MANUAL" else source_type_value),
            "payable_id": int(data["payable_id"]) if data.get("payable_id") is not None else None,
            "cheque_id": int(data["cheque_id"]) if data.get("cheque_id") is not None else None,
            "po_id": int(data["po_id"]) if data.get("po_id") is not None else None,
            "po_number_snapshot": data.get("po_number_snapshot") or "",
            "payee_name_snapshot": data.get("payee_name_snapshot") or "-",
            "cheque_no_snapshot": data.get("cheque_no_snapshot") or "",
            "amount_snapshot": _normalize_money(data.get("amount_snapshot")),
            "old_status": data.get("old_status") or "",
            "new_status": data.get("new_status") or "",
            "notes": data.get("notes") or "",
            "created_by_username": data.get("created_by_username") or "System",
        })

    return {
        "rows": serialized_rows,
        "page": current_page,
        "total": total,
        "total_pages": total_pages,
    }


def run_payable_cheque_due_reminders():
    recipient_user_ids = list_active_user_ids()
    if not recipient_user_ids:
        return {"due_in_7_days": 0, "due_today": 0}

    today_value = today_local()
    due_in_7_days = today_value + timedelta(days=7)

    conn = get_db()
    try:
        upcoming_rows = conn.execute(
            """
            SELECT pc.id, pc.payable_id, pc.cheque_no, pc.due_date, p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.status = %s
              AND pc.due_date > %s
              AND pc.due_date <= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM notifications n
                  WHERE n.entity_type = %s
                    AND n.entity_id = pc.id
                    AND n.notification_type = %s
                    AND DATE(n.created_at) = %s
              )
            """,
            (
                CHEQUE_STATUS_ISSUED,
                today_value.isoformat(),
                due_in_7_days.isoformat(),
                "PAYABLE_CHEQUE",
                "PAYABLE_CHEQUE_DUE_IN_7_DAYS",
                today_value.isoformat(),
            ),
        ).fetchall()

        today_rows = conn.execute(
            """
            SELECT pc.id, pc.payable_id, pc.cheque_no, pc.due_date, p.payee_name
            FROM payable_cheques pc
            JOIN payables p ON p.id = pc.payable_id
            WHERE pc.status = %s
              AND pc.due_date = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM notifications n
                  WHERE n.entity_type = %s
                    AND n.entity_id = pc.id
                    AND n.notification_type = %s
                    AND DATE(n.created_at) = %s
              )
            """,
            (
                CHEQUE_STATUS_ISSUED,
                today_value.isoformat(),
                "PAYABLE_CHEQUE",
                "PAYABLE_CHEQUE_DUE_TODAY",
                today_value.isoformat(),
            ),
        ).fetchall()

        for row in upcoming_rows:
            create_notifications_for_users(
                recipient_user_ids,
                "PAYABLE_CHEQUE_DUE_IN_7_DAYS",
                "Cheque due within 7 days",
                f"Cheque #{row['cheque_no']} for {row['payee_name'] or 'payee'} is due on {format_date(row['due_date'])}.",
                category="payables",
                entity_type="PAYABLE_CHEQUE",
                entity_id=int(row["id"]),
                action_url=_payables_action_url(payable_id=row["payable_id"], cheque_id=row["id"]),
                external_conn=conn,
                metadata={"payable_id": int(row["payable_id"]), "cheque_id": int(row["id"]), "due_date": str(row["due_date"])},
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
                action_url=_payables_action_url(payable_id=row["payable_id"], cheque_id=row["id"]),
                external_conn=conn,
                metadata={"payable_id": int(row["payable_id"]), "cheque_id": int(row["id"]), "due_date": str(row["due_date"])},
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
