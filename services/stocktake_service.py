import random
from datetime import datetime

from db.database import get_db
from utils.formatters import format_date


PARTIAL_STOCKTAKE_LABEL = "This is a partial stocktake. Only items added to this session will be adjusted when confirmed."
STOCKTAKE_SCOPE_DEFAULT = "PARTIAL"
STOCKTAKE_STATUS_DRAFT = "DRAFT"
STOCKTAKE_STATUS_CONFIRMED = "CONFIRMED"
STOCKTAKE_STATUS_CANCELLED = "CANCELLED"


def _normalize_scope(scope):
    value = str(scope or STOCKTAKE_SCOPE_DEFAULT).strip().upper()
    return value or STOCKTAKE_SCOPE_DEFAULT


def _generate_session_number(conn):
    date_stamp = datetime.now().strftime("%m%d")
    for _ in range(25):
        candidate = f"ST-{date_stamp}-{random.randint(0, 999):03d}"
        exists = conn.execute(
            "SELECT 1 FROM stocktake_sessions WHERE LOWER(session_number) = LOWER(%s) LIMIT 1",
            (candidate,),
        ).fetchone()
        if not exists:
            return candidate
    raise ValueError("Unable to generate a unique stocktake session number. Please try again.")


def _get_live_stock(conn, item_id):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE
                WHEN transaction_type = 'IN' THEN quantity
                WHEN transaction_type = 'OUT' THEN -quantity
                ELSE 0
            END
        ), 0) AS current_stock
        FROM inventory_transactions
        WHERE item_id = %s
        """,
        (item_id,),
    ).fetchone()
    return int(row["current_stock"] or 0) if row else 0


def _derive_adjustment(counted_stock, system_stock):
    variance = int(counted_stock) - int(system_stock)
    if variance > 0:
        return variance, "IN", variance
    if variance < 0:
        return variance, "OUT", abs(variance)
    return variance, None, 0


def _update_session_counters(conn, session_id):
    counts = conn.execute(
        """
        SELECT
            COUNT(*) AS item_count,
            COUNT(*) FILTER (WHERE variance <> 0) AS variance_item_count
        FROM stocktake_items
        WHERE session_id = %s
        """,
        (session_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE stocktake_sessions
        SET item_count = %s,
            variance_item_count = %s
        WHERE id = %s
        """,
        (
            int(counts["item_count"] or 0),
            int(counts["variance_item_count"] or 0),
            session_id,
        ),
    )


def _serialize_session_row(row):
    if not row:
        return None
    data = dict(row)
    data["item_count"] = int(data.get("item_count") or 0)
    data["variance_item_count"] = int(data.get("variance_item_count") or 0)
    data["count_scope"] = _normalize_scope(data.get("count_scope"))
    data["status"] = str(data.get("status") or STOCKTAKE_STATUS_DRAFT).upper()
    data["created_at_display"] = format_date(data.get("created_at"), show_time=True)
    data["confirmed_at_display"] = format_date(data.get("confirmed_at"), show_time=True)
    data["cancelled_at_display"] = format_date(data.get("cancelled_at"), show_time=True)
    data["partial_scope_label"] = PARTIAL_STOCKTAKE_LABEL if data["count_scope"] == STOCKTAKE_SCOPE_DEFAULT else ""
    return data


def _serialize_item_row(row):
    data = dict(row)
    data["system_stock"] = int(data.get("system_stock") or 0)
    counted_stock = data.get("counted_stock")
    data["counted_stock"] = int(counted_stock) if counted_stock is not None else None
    data["variance"] = int(data.get("variance") or 0)
    data["adjustment_quantity"] = int(data.get("adjustment_quantity") or 0)
    data["is_applied"] = int(data.get("is_applied") or 0)
    return data


def create_stocktake_session(user_id, username, notes=None, count_scope=STOCKTAKE_SCOPE_DEFAULT):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_number = _generate_session_number(conn)
        row = conn.execute(
            """
            INSERT INTO stocktake_sessions (
                session_number,
                status,
                count_scope,
                notes,
                created_by,
                created_by_username
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                session_number,
                STOCKTAKE_STATUS_DRAFT,
                _normalize_scope(count_scope),
                (notes or "").strip() or None,
                user_id,
                username,
            ),
        ).fetchone()
        conn.commit()
        return _serialize_session_row(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_stocktake_sessions():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
        return [_serialize_session_row(row) for row in rows]
    finally:
        conn.close()


def get_stocktake_session(session_id):
    conn = get_db()
    try:
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            return None

        item_rows = conn.execute(
            """
            SELECT
                si.*,
                i.name,
                i.category,
                i.pack_size,
                i.a4s_selling_price
            FROM stocktake_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.session_id = %s
            ORDER BY i.name ASC
            """,
            (session_id,),
        ).fetchall()

        summary_row = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE variance > 0) AS overage_item_count,
                COUNT(*) FILTER (WHERE variance < 0) AS shortage_item_count,
                COALESCE(SUM(CASE WHEN variance > 0 THEN variance ELSE 0 END), 0) AS total_overage_units,
                COALESCE(SUM(CASE WHEN variance < 0 THEN ABS(variance) ELSE 0 END), 0) AS total_shortage_units
            FROM stocktake_items
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()

        session_data = _serialize_session_row(session_row)
        session_data["items"] = [_serialize_item_row(row) for row in item_rows]
        session_data["summary"] = {
            "item_count": session_data["item_count"],
            "variance_item_count": session_data["variance_item_count"],
            "overage_item_count": int(summary_row["overage_item_count"] or 0),
            "shortage_item_count": int(summary_row["shortage_item_count"] or 0),
            "total_overage_units": int(summary_row["total_overage_units"] or 0),
            "total_shortage_units": int(summary_row["total_shortage_units"] or 0),
        }
        return session_data
    finally:
        conn.close()


def add_stocktake_item(session_id, item_id, counted_stock=None, notes=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            raise ValueError("Stocktake session not found.")
        if str(session_row["status"] or "").upper() != STOCKTAKE_STATUS_DRAFT:
            raise ValueError("Only draft stocktake sessions can be edited.")

        item_row = conn.execute(
            "SELECT id, name FROM items WHERE id = %s",
            (item_id,),
        ).fetchone()
        if not item_row:
            raise ValueError("Item not found.")

        existing = conn.execute(
            """
            SELECT id
            FROM stocktake_items
            WHERE session_id = %s AND item_id = %s
            """,
            (session_id, item_id),
        ).fetchone()
        if existing:
            raise ValueError("This item is already included in the stocktake session.")

        system_stock = _get_live_stock(conn, item_id)
        normalized_count = None
        variance = 0
        adjustment_type = None
        adjustment_quantity = 0

        if counted_stock is not None:
            normalized_count = int(counted_stock)
            if normalized_count < 0:
                raise ValueError("Counted stock cannot be negative.")
            variance, adjustment_type, adjustment_quantity = _derive_adjustment(normalized_count, system_stock)

        row = conn.execute(
            """
            INSERT INTO stocktake_items (
                session_id,
                item_id,
                system_stock,
                counted_stock,
                variance,
                adjustment_type,
                adjustment_quantity,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                session_id,
                item_id,
                system_stock,
                normalized_count,
                variance,
                adjustment_type,
                adjustment_quantity,
                (notes or "").strip() or None,
            ),
        ).fetchone()

        _update_session_counters(conn, session_id)
        conn.commit()
        session_data = get_stocktake_session(session_id)
        new_item = next((item for item in session_data["items"] if int(item["id"]) == int(row["id"])), None)
        return {"session": session_data, "item": new_item}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_stocktake_item(session_id, item_id, counted_stock, notes=None):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            raise ValueError("Stocktake session not found.")
        if str(session_row["status"] or "").upper() != STOCKTAKE_STATUS_DRAFT:
            raise ValueError("Only draft stocktake sessions can be edited.")

        line = conn.execute(
            """
            SELECT *
            FROM stocktake_items
            WHERE session_id = %s AND item_id = %s
            FOR UPDATE
            """,
            (session_id, item_id),
        ).fetchone()
        if not line:
            raise ValueError("Stocktake item was not found in this session.")

        if counted_stock is None:
            normalized_count = None
            variance = 0
            adjustment_type = None
            adjustment_quantity = 0
        else:
            normalized_count = int(counted_stock)
            if normalized_count < 0:
                raise ValueError("Counted stock cannot be negative.")
            variance, adjustment_type, adjustment_quantity = _derive_adjustment(
                normalized_count,
                int(line["system_stock"] or 0),
            )

        conn.execute(
            """
            UPDATE stocktake_items
            SET counted_stock = %s,
                variance = %s,
                adjustment_type = %s,
                adjustment_quantity = %s,
                notes = %s,
                updated_at = NOW()
            WHERE session_id = %s AND item_id = %s
            """,
            (
                normalized_count,
                variance,
                adjustment_type,
                adjustment_quantity,
                (notes or "").strip() or None,
                session_id,
                item_id,
            ),
        )

        _update_session_counters(conn, session_id)
        conn.commit()
        return get_stocktake_session(session_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def bulk_save_stocktake_items(session_id, items):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            raise ValueError("Stocktake session not found.")
        if str(session_row["status"] or "").upper() != STOCKTAKE_STATUS_DRAFT:
            raise ValueError("Only draft stocktake sessions can be edited.")

        existing_rows = conn.execute(
            """
            SELECT id, item_id, system_stock
            FROM stocktake_items
            WHERE session_id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchall()
        existing_map = {int(row["item_id"]): dict(row) for row in existing_rows}

        if not items:
            raise ValueError("There are no stocktake items to save.")

        seen_item_ids = set()
        for raw_item in items:
            try:
                item_id = int(raw_item.get("item_id"))
            except (TypeError, ValueError):
                raise ValueError("One or more stocktake items are invalid.")

            if item_id in seen_item_ids:
                raise ValueError("Duplicate stocktake items were submitted.")
            seen_item_ids.add(item_id)

            line = existing_map.get(item_id)
            if not line:
                raise ValueError("One or more stocktake items are no longer part of this session.")

            counted_stock = raw_item.get("counted_stock")
            if counted_stock in ("", None):
                normalized_count = None
                variance = 0
                adjustment_type = None
                adjustment_quantity = 0
            else:
                try:
                    normalized_count = int(counted_stock)
                except (TypeError, ValueError):
                    raise ValueError("Counted stock must be a whole number.")
                if normalized_count < 0:
                    raise ValueError("Counted stock cannot be negative.")
                variance, adjustment_type, adjustment_quantity = _derive_adjustment(
                    normalized_count,
                    int(line["system_stock"] or 0),
                )

            conn.execute(
                """
                UPDATE stocktake_items
                SET counted_stock = %s,
                    variance = %s,
                    adjustment_type = %s,
                    adjustment_quantity = %s,
                    notes = %s,
                    updated_at = NOW()
                WHERE session_id = %s AND item_id = %s
                """,
                (
                    normalized_count,
                    variance,
                    adjustment_type,
                    adjustment_quantity,
                    (raw_item.get("notes") or "").strip() or None,
                    session_id,
                    item_id,
                ),
            )

        _update_session_counters(conn, session_id)
        conn.commit()
        return get_stocktake_session(session_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_stocktake_item(session_id, item_id):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            raise ValueError("Stocktake session not found.")
        if str(session_row["status"] or "").upper() != STOCKTAKE_STATUS_DRAFT:
            raise ValueError("Only draft stocktake sessions can be edited.")

        deleted = conn.execute(
            """
            DELETE FROM stocktake_items
            WHERE session_id = %s AND item_id = %s
            """,
            (session_id, item_id),
        )
        if deleted.rowcount <= 0:
            raise ValueError("Stocktake item was not found in this session.")

        _update_session_counters(conn, session_id)
        conn.commit()
        return get_stocktake_session(session_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def cancel_stocktake_session(session_id, user_id, username):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not row:
            raise ValueError("Stocktake session not found.")
        status = str(row["status"] or "").upper()
        if status == STOCKTAKE_STATUS_CONFIRMED:
            raise ValueError("Confirmed stocktake sessions cannot be cancelled.")
        if status == STOCKTAKE_STATUS_CANCELLED:
            raise ValueError("This stocktake session has already been cancelled.")

        conn.execute(
            """
            UPDATE stocktake_sessions
            SET status = %s,
                cancelled_by = %s,
                cancelled_by_username = %s,
                cancelled_at = NOW()
            WHERE id = %s
            """,
            (STOCKTAKE_STATUS_CANCELLED, user_id, username, session_id),
        )
        conn.commit()
        return get_stocktake_session(session_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirm_stocktake_session(session_id, user_id, username):
    conn = get_db()
    try:
        conn.execute("BEGIN")
        session_row = conn.execute(
            """
            SELECT *
            FROM stocktake_sessions
            WHERE id = %s
            FOR UPDATE
            """,
            (session_id,),
        ).fetchone()
        if not session_row:
            raise ValueError("Stocktake session not found.")
        status = str(session_row["status"] or "").upper()
        if status == STOCKTAKE_STATUS_CONFIRMED:
            raise ValueError("This stocktake session has already been confirmed.")
        if status == STOCKTAKE_STATUS_CANCELLED:
            raise ValueError("Cancelled stocktake sessions cannot be confirmed.")

        item_rows = conn.execute(
            """
            SELECT
                si.*,
                i.name
            FROM stocktake_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.session_id = %s
            ORDER BY i.name ASC
            FOR UPDATE
            """,
            (session_id,),
        ).fetchall()
        if not item_rows:
            raise ValueError("Add at least one item to the stocktake session before confirming.")

        missing_counts = [row["name"] for row in item_rows if row["counted_stock"] is None]
        if missing_counts:
            raise ValueError("Every stocktake item needs a counted stock before confirmation.")

        drift_rows = []
        for row in item_rows:
            live_stock = _get_live_stock(conn, row["item_id"])
            stored_stock = int(row["system_stock"] or 0)
            if live_stock != stored_stock:
                drift_rows.append(
                    f"{row['name']} (captured {stored_stock}, live {live_stock})"
                )

        if drift_rows:
            preview = ", ".join(drift_rows[:5])
            if len(drift_rows) > 5:
                preview += f", and {len(drift_rows) - 5} more"
            raise ValueError(
                "Live stock changed since this draft was captured. "
                f"Please review and refresh the affected items: {preview}."
            )

        for row in item_rows:
            variance = int(row["variance"] or 0)
            if variance == 0:
                continue

            counted_stock = int(row["counted_stock"] or 0)
            system_stock = int(row["system_stock"] or 0)
            adjustment_type = "IN" if variance > 0 else "OUT"
            adjustment_quantity = abs(variance)
            change_reason = "STOCKTAKE_VARIANCE_GAIN" if variance > 0 else "STOCKTAKE_VARIANCE_LOSS"
            note_parts = [
                f"Stocktake {session_row['session_number']}",
                f"System: {system_stock}",
                f"Counted: {counted_stock}",
                f"Variance: {variance}",
            ]
            if row["notes"]:
                note_parts.append(str(row["notes"]).strip())

            txn_row = conn.execute(
                """
                INSERT INTO inventory_transactions (
                    item_id, quantity, transaction_type, transaction_date, user_id, user_name,
                    reference_id, reference_type, change_reason, unit_price, notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    row["item_id"],
                    adjustment_quantity,
                    adjustment_type,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    user_id,
                    username,
                    session_id,
                    "STOCKTAKE",
                    change_reason,
                    None,
                    " | ".join(note_parts),
                ),
            ).fetchone()

            conn.execute(
                """
                UPDATE stocktake_items
                SET is_applied = 1,
                    applied_transaction_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (txn_row["id"], row["id"]),
            )

        conn.execute(
            """
            UPDATE stocktake_sessions
            SET status = %s,
                confirmed_by = %s,
                confirmed_by_username = %s,
                confirmed_at = NOW()
            WHERE id = %s
            """,
            (STOCKTAKE_STATUS_CONFIRMED, user_id, username, session_id),
        )
        _update_session_counters(conn, session_id)
        conn.commit()
        return get_stocktake_session(session_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
