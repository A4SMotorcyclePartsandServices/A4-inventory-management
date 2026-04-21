from db.database import get_db
from services.transactions_service import add_transaction, get_sale_refund_context
from utils.formatters import format_date
from utils.timezone import now_local


VOID_BLOCKED_TRANSACTION_CLASSES = {"MECHANIC_SUPPLY"}
VOID_REASON_OPTIONS = [
    {"value": "WRONG_ITEMS", "label": "Wrong items encoded"},
    {"value": "WRONG_QTY", "label": "Wrong quantity encoded"},
    {"value": "WRONG_PAYMENT_METHOD", "label": "Wrong Payment Method"},
    {"value": "WRONG_ITEM_PRICE", "label": "Wrong Item Price"},
    {"value": "WRONG_CUSTOMER", "label": "Wrong customer selected"},
    {"value": "DUPLICATE_ENCODING", "label": "Duplicate sale encoding"},
    {"value": "TEST_ENTRY", "label": "Test / accidental entry"},
    {"value": "OTHER", "label": "Other"},
]


def _normalize_bool(value):
    return bool(value) and str(value).strip().lower() not in {"0", "false", "no", "off"}


def _normalize_void_timestamp(raw_value=None):
    if raw_value:
        clean_time = str(raw_value).replace("T", " ")
        if len(clean_time) == 16:
            clean_time += ":00"
        return clean_time
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def _parse_void_reason(data):
    reason = str((data or {}).get("reason") or "").strip().upper()
    valid_reasons = {option["value"] for option in VOID_REASON_OPTIONS}
    if reason not in valid_reasons:
        raise ValueError("Select a valid void reason.")
    return reason


def _parse_void_notes(data):
    notes = str((data or {}).get("notes") or "").strip()
    if not notes:
        raise ValueError("Void notes are required.")
    return notes


def _build_void_number(sale_id, sale_number):
    base_reference = str(sale_number or sale_id).strip()
    return f"VOID-{base_reference}"


def _get_sale_core_row(conn, sale_id):
    return conn.execute(
        """
        SELECT
            s.id,
            s.sales_number,
            s.customer_name,
            s.total_amount,
            s.status,
            s.transaction_date,
            COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
            COALESCE(s.is_voided, FALSE) AS is_voided,
            s.voided_at,
            s.voided_by,
            s.voided_by_username,
            s.void_reason,
            s.void_notes
        FROM sales s
        WHERE s.id = %s
        """,
        (sale_id,),
    ).fetchone()


def _has_refunds(conn, sale_id):
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM sale_refunds WHERE sale_id = %s",
        (sale_id,),
    ).fetchone()
    return int(row["count"] or 0) > 0


def _has_exchange_links(conn, sale_id):
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM sale_exchanges
        WHERE original_sale_id = %s
           OR replacement_sale_id = %s
        """,
        (sale_id, sale_id),
    ).fetchone()
    return int(row["count"] or 0) > 0


def _has_debt_payments(conn, sale_id):
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM debt_payments WHERE sale_id = %s",
        (sale_id,),
    ).fetchone()
    return int(row["count"] or 0) > 0


def _get_loyalty_void_state(conn, sale_id):
    stamp_row = conn.execute(
        """
        SELECT
            COUNT(*) AS stamp_count,
            COUNT(*) FILTER (WHERE redemption_id IS NOT NULL) AS consumed_stamp_count
        FROM loyalty_stamps
        WHERE sale_id = %s
        """,
        (sale_id,),
    ).fetchone()
    points_row = conn.execute(
        """
        SELECT
            COUNT(*) AS point_count,
            COUNT(*) FILTER (WHERE redemption_id IS NOT NULL) AS consumed_point_count
        FROM loyalty_point_ledger
        WHERE sale_id = %s
        """,
        (sale_id,),
    ).fetchone()
    return {
        "stamp_count": int(stamp_row["stamp_count"] or 0),
        "consumed_stamp_count": int(stamp_row["consumed_stamp_count"] or 0),
        "point_count": int(points_row["point_count"] or 0),
        "consumed_point_count": int(points_row["consumed_point_count"] or 0),
    }


def _get_sale_item_rows(conn, sale_id):
    return conn.execute(
        """
        SELECT
            si.id,
            si.item_id,
            si.quantity,
            si.original_unit_price,
            i.name
        FROM sales_items si
        JOIN items i ON i.id = si.item_id
        WHERE si.sale_id = %s
        ORDER BY si.id ASC
        """,
        (sale_id,),
    ).fetchall()


def _get_bundle_item_rows(conn, sale_id):
    return conn.execute(
        """
        SELECT
            sbi.id,
            sbi.item_id,
            sbi.quantity,
            sbi.selling_price_snapshot,
            sbi.item_name_snapshot,
            sb.bundle_name_snapshot,
            sb.subcategory_name_snapshot
        FROM sales_bundle_items sbi
        JOIN sales_bundles sb ON sb.id = sbi.sales_bundle_id
        WHERE sb.sale_id = %s
          AND COALESCE(sbi.is_included, 0) = 1
        ORDER BY sb.id ASC, sbi.id ASC
        """,
        (sale_id,),
    ).fetchall()


def _derive_void_block_reason(conn, sale_row):
    if not sale_row:
        return "Sale not found."

    if _normalize_bool(sale_row["is_voided"]):
        return "This sale has already been voided."

    transaction_class = str(sale_row["transaction_class"] or "").strip().upper()
    if transaction_class in VOID_BLOCKED_TRANSACTION_CLASSES:
        return "Mechanic supply transactions cannot be voided from this tool."

    if str(sale_row["status"] or "").strip() != "Paid":
        return "Only fully paid sales can be voided."

    if _has_refunds(conn, sale_row["id"]):
        return "Sales with refund history cannot be voided."

    if _has_exchange_links(conn, sale_row["id"]):
        return "Sales linked to exchanges cannot be voided."

    if _has_debt_payments(conn, sale_row["id"]):
        return "Sales with debt payments cannot be voided."

    loyalty_state = _get_loyalty_void_state(conn, sale_row["id"])
    if loyalty_state["consumed_stamp_count"] > 0 or loyalty_state["consumed_point_count"] > 0:
        return "Loyalty earned from this sale has already been redeemed."

    return ""


def get_void_sale_context(sale_id):
    conn = get_db()
    try:
        sale_row = _get_sale_core_row(conn, sale_id)
        if not sale_row:
            raise ValueError("Sale not found.")

        block_reason = _derive_void_block_reason(conn, sale_row)
        loyalty_state = _get_loyalty_void_state(conn, sale_id)
    finally:
        conn.close()

    context = get_sale_refund_context(sale_id)
    context.update(
        {
            "is_voided": _normalize_bool(sale_row["is_voided"]),
            "voided_at": sale_row["voided_at"],
            "voided_at_display": format_date(sale_row["voided_at"], show_time=True) if sale_row["voided_at"] else None,
            "voided_by": int(sale_row["voided_by"]) if sale_row["voided_by"] is not None else None,
            "voided_by_username": sale_row["voided_by_username"] or "",
            "void_reason": sale_row["void_reason"] or "",
            "void_notes": sale_row["void_notes"] or "",
            "can_void": not bool(block_reason),
            "void_block_reason": block_reason,
            "loyalty_summary": loyalty_state,
        }
    )
    return context


def search_void_sales(query=None, limit=50):
    conn = get_db()
    try:
        conditions = ["COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'"]
        params = []

        search_text = str(query or "").strip()
        if search_text:
            like = f"%{search_text}%"
            conditions.append(
                """
                (
                    COALESCE(s.sales_number, '') ILIKE %s
                    OR COALESCE(s.customer_name, '') ILIKE %s
                    OR to_char(s.transaction_date, 'YYYY-MM-DD') ILIKE %s
                    OR EXISTS (
                        SELECT 1
                        FROM sales_items si
                        JOIN items i ON i.id = si.item_id
                        WHERE si.sale_id = s.id
                          AND i.name ILIKE %s
                    )
                )
                """
            )
            params.extend([like, like, like, like])

        try:
            limit_value = max(1, min(int(limit or 50), 100))
        except (TypeError, ValueError):
            limit_value = 50

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT
                s.id,
                s.sales_number,
                s.customer_name,
                s.total_amount,
                s.status,
                s.transaction_date,
                COALESCE(s.transaction_class, 'NEW_SALE') AS transaction_class,
                COALESCE(s.is_voided, FALSE) AS is_voided,
                s.voided_at,
                s.voided_by_username
            FROM sales s
            {where_clause}
            ORDER BY s.transaction_date DESC, s.id DESC
            LIMIT %s
            """,
            params + [limit_value],
        ).fetchall()

        results = []
        for row in rows:
            block_reason = _derive_void_block_reason(conn, row)
            results.append(
                {
                    "id": int(row["id"]),
                    "sales_number": row["sales_number"] or "",
                    "customer_name": row["customer_name"] or "Walk-in",
                    "total_amount": round(float(row["total_amount"] or 0), 2),
                    "status": row["status"] or "",
                    "transaction_class": row["transaction_class"] or "NEW_SALE",
                    "transaction_date": format_date(row["transaction_date"], show_time=True),
                    "is_voided": _normalize_bool(row["is_voided"]),
                    "voided_at_display": format_date(row["voided_at"], show_time=True) if row["voided_at"] else None,
                    "voided_by_username": row["voided_by_username"] or "",
                    "can_void": not bool(block_reason),
                    "void_block_reason": block_reason,
                }
            )
        return results
    finally:
        conn.close()


def void_sale(sale_id, data, user_id, username):
    reason = _parse_void_reason(data)
    notes = _parse_void_notes(data)
    void_time = _normalize_void_timestamp((data or {}).get("void_date"))
    conn = get_db()
    try:
        sale_row = _get_sale_core_row(conn, sale_id)
        if not sale_row:
            raise ValueError("Sale not found.")

        block_reason = _derive_void_block_reason(conn, sale_row)
        if block_reason:
            raise ValueError(block_reason)

        sale_item_rows = _get_sale_item_rows(conn, sale_id)
        bundle_item_rows = _get_bundle_item_rows(conn, sale_id)
        loyalty_state = _get_loyalty_void_state(conn, sale_id)

        conn.execute("BEGIN")

        conn.execute(
            """
            UPDATE sales
            SET
                is_voided = TRUE,
                voided_at = %s,
                voided_by = %s,
                voided_by_username = %s,
                void_reason = %s,
                void_notes = %s
            WHERE id = %s
            """,
            (void_time, user_id, username, reason, notes, sale_id),
        )

        audit_note = f"{_build_void_number(sale_id, sale_row['sales_number'])}: {reason} | {notes}"

        for row in sale_item_rows:
            add_transaction(
                item_id=int(row["item_id"]),
                quantity=int(row["quantity"] or 0),
                transaction_type="IN",
                user_id=user_id,
                user_name=username,
                reference_id=sale_id,
                reference_type="SALE",
                change_reason="SALE_VOID",
                unit_price=float(row["original_unit_price"] or 0),
                transaction_date=void_time,
                external_conn=conn,
                notes=audit_note,
            )

        for row in bundle_item_rows:
            bundle_note = (
                f"{audit_note} | Bundle: {row['bundle_name_snapshot']} - {row['subcategory_name_snapshot']}"
            )
            add_transaction(
                item_id=int(row["item_id"]),
                quantity=int(row["quantity"] or 0),
                transaction_type="IN",
                user_id=user_id,
                user_name=username,
                reference_id=sale_id,
                reference_type="SALE",
                change_reason="SALE_VOID",
                unit_price=float(row["selling_price_snapshot"] or 0),
                transaction_date=void_time,
                external_conn=conn,
                notes=bundle_note,
            )

        if loyalty_state["stamp_count"] > 0:
            conn.execute(
                "DELETE FROM loyalty_stamps WHERE sale_id = %s AND redemption_id IS NULL",
                (sale_id,),
            )
        if loyalty_state["point_count"] > 0:
            conn.execute(
                "DELETE FROM loyalty_point_ledger WHERE sale_id = %s AND redemption_id IS NULL",
                (sale_id,),
            )

        conn.commit()
        return {
            "sale_id": int(sale_id),
            "sales_number": sale_row["sales_number"] or "",
            "voided_at": void_time,
            "voided_at_display": format_date(void_time, show_time=True),
            "voided_by_username": username,
            "void_reason": reason,
            "void_notes": notes,
            "restored_item_lines": len(sale_item_rows) + len(bundle_item_rows),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
