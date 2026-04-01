import random
from datetime import datetime, timedelta

from db.database import get_db
from services.inventory_service import STOCKTAKE_WARNING_DAYS, attach_recent_stocktake_metadata
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


def _record_baseline_history(
    conn,
    stocktake_item_id,
    event_type,
    baseline_stock,
    counted_stock_snapshot,
    variance_snapshot,
    live_stock=None,
    previous_active_stock=None,
    actor_user_id=None,
    actor_username=None,
):
    conn.execute(
        """
        INSERT INTO stocktake_item_baseline_history (
            stocktake_item_id,
            event_type,
            baseline_stock,
            previous_active_stock,
            live_stock,
            counted_stock_snapshot,
            variance_snapshot,
            actor_user_id,
            actor_username
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            stocktake_item_id,
            event_type,
            baseline_stock,
            previous_active_stock,
            live_stock,
            counted_stock_snapshot,
            variance_snapshot,
            actor_user_id,
            actor_username,
        ),
    )


def _auto_refresh_active_baseline(
    conn,
    line,
    actor_user_id=None,
    actor_username=None,
):
    live_stock = _get_live_stock(conn, line["item_id"])
    active_stock = int(line["active_system_stock"] or line["system_stock"] or 0)
    if live_stock == active_stock:
        return {**dict(line), "active_system_stock": active_stock, "live_stock": live_stock, "was_auto_refreshed": False}

    counted_stock = line["counted_stock"]
    if counted_stock is None:
        variance = 0
        adjustment_type = None
        adjustment_quantity = 0
    else:
        variance, adjustment_type, adjustment_quantity = _derive_adjustment(
            int(counted_stock),
            live_stock,
        )

    _record_baseline_history(
        conn,
        stocktake_item_id=int(line["id"]),
        event_type="REFRESH",
        baseline_stock=live_stock,
        counted_stock_snapshot=int(counted_stock) if counted_stock is not None else None,
        variance_snapshot=variance,
        live_stock=live_stock,
        previous_active_stock=active_stock,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
    )

    conn.execute(
        """
        UPDATE stocktake_items
        SET active_system_stock = %s,
            variance = %s,
            baseline_mode = %s,
            baseline_refreshed_at = NOW(),
            baseline_refreshed_by = %s,
            baseline_refreshed_by_username = %s,
            adjustment_type = %s,
            adjustment_quantity = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            live_stock,
            variance,
            "REFRESHED",
            actor_user_id,
            actor_username,
            adjustment_type,
            adjustment_quantity,
            line["id"],
        ),
    )

    updated_line = dict(line)
    updated_line["active_system_stock"] = live_stock
    updated_line["variance"] = variance
    updated_line["adjustment_type"] = adjustment_type
    updated_line["adjustment_quantity"] = adjustment_quantity
    updated_line["baseline_mode"] = "REFRESHED"
    updated_line["baseline_refreshed_by"] = actor_user_id
    updated_line["baseline_refreshed_by_username"] = actor_username
    updated_line["live_stock"] = live_stock
    updated_line["was_auto_refreshed"] = True
    return updated_line


def _apply_live_stock_state(conn, session_data):
    items = session_data.get("items") or []
    drift_item_count = 0

    for item in items:
        live_stock = _get_live_stock(conn, item["item_id"])
        active_stock = int(item.get("active_system_stock") or 0)
        drift_quantity = live_stock - active_stock
        has_live_drift = drift_quantity != 0

        item["live_stock"] = live_stock
        item["live_drift_quantity"] = drift_quantity
        item["has_live_drift"] = has_live_drift

        if has_live_drift:
            drift_item_count += 1

    summary = session_data.setdefault("summary", {})
    summary["drift_item_count"] = drift_item_count
    session_data["has_live_drift"] = drift_item_count > 0
    return session_data


def _attach_baseline_history(conn, session_data):
    items = session_data.get("items") or []
    if not items:
        return session_data

    item_ids = [int(item["id"]) for item in items if item.get("id") is not None]
    if not item_ids:
        return session_data

    history_rows = conn.execute(
        """
        SELECT *
        FROM stocktake_item_baseline_history
        WHERE stocktake_item_id = ANY(%s)
        ORDER BY created_at ASC, id ASC
        """,
        (item_ids,),
    ).fetchall()

    history_map = {}
    for row in history_rows:
        event = dict(row)
        event["baseline_stock"] = int(event.get("baseline_stock") or 0)
        previous_active_stock = event.get("previous_active_stock")
        event["previous_active_stock"] = int(previous_active_stock) if previous_active_stock is not None else None
        live_stock = event.get("live_stock")
        event["live_stock"] = int(live_stock) if live_stock is not None else None
        counted_snapshot = event.get("counted_stock_snapshot")
        event["counted_stock_snapshot"] = int(counted_snapshot) if counted_snapshot is not None else None
        event["variance_snapshot"] = int(event.get("variance_snapshot") or 0)
        event["created_at_display"] = format_date(event.get("created_at"), show_time=True)
        event["event_type"] = str(event.get("event_type") or "").upper()
        history_map.setdefault(int(event["stocktake_item_id"]), []).append(event)

    for item in items:
        history = history_map.get(int(item["id"]), [])
        item["baseline_history"] = history
        refresh_events = [event for event in history if event["event_type"] == "REFRESH"]
        item["baseline_refresh_count"] = len(refresh_events)
        item["latest_baseline_refresh"] = refresh_events[-1] if refresh_events else None

    return session_data


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


def get_recent_stocktake_activity(window_days=STOCKTAKE_WARNING_DAYS):
    conn = get_db()
    try:
        cutoff = datetime.now() - timedelta(days=int(window_days or STOCKTAKE_WARNING_DAYS))
        summary_row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT si.item_id) AS unique_item_count,
                COUNT(DISTINCT ss.id) AS session_count
            FROM stocktake_items si
            JOIN stocktake_sessions ss ON ss.id = si.session_id
            WHERE ss.status = 'CONFIRMED'
              AND ss.confirmed_at IS NOT NULL
              AND ss.confirmed_at >= %s
            """,
            (cutoff,),
        ).fetchone()

        latest_row = conn.execute(
            """
            SELECT
                ss.id,
                ss.session_number,
                ss.confirmed_at,
                ss.item_count,
                ss.variance_item_count
            FROM stocktake_sessions ss
            WHERE ss.status = 'CONFIRMED'
              AND ss.confirmed_at IS NOT NULL
            ORDER BY ss.confirmed_at DESC, ss.id DESC
            LIMIT 1
            """
        ).fetchone()

        latest = dict(latest_row) if latest_row else None
        if latest:
            latest["confirmed_at_display"] = format_date(latest.get("confirmed_at"), show_time=True)

        return {
            "window_days": int(window_days or STOCKTAKE_WARNING_DAYS),
            "unique_item_count": int(summary_row["unique_item_count"] or 0),
            "session_count": int(summary_row["session_count"] or 0),
            "latest_confirmed_session": latest,
        }
    finally:
        conn.close()


def get_stocktake_overall_report(start_date, end_date):
    conn = get_db()
    try:
        summary_row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT ss.id) AS session_count,
                COUNT(si.id) AS item_count,
                COUNT(si.id) FILTER (WHERE COALESCE(si.variance, 0) <> 0) AS variance_item_count,
                COUNT(si.id) FILTER (WHERE COALESCE(si.variance, 0) < 0) AS shortage_item_count,
                COUNT(si.id) FILTER (WHERE COALESCE(si.variance, 0) > 0) AS overage_item_count,
                COALESCE(SUM(
                    CASE
                        WHEN si.counted_stock IS NOT NULL
                        THEN COALESCE(si.counted_stock, 0) * COALESCE(i.cost_per_piece, 0)
                        ELSE 0
                    END
                ), 0) AS counted_items_value,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(si.variance, 0) <> 0
                        THEN ABS(COALESCE(si.variance, 0) * COALESCE(i.cost_per_piece, 0))
                        ELSE 0
                    END
                ), 0) AS variance_items_value,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(si.variance, 0) < 0
                        THEN ABS(COALESCE(si.variance, 0) * COALESCE(i.cost_per_piece, 0))
                        ELSE 0
                    END
                ), 0) AS shortage_items_value,
                COALESCE(SUM(
                    CASE
                        WHEN COALESCE(si.variance, 0) > 0
                        THEN ABS(COALESCE(si.variance, 0) * COALESCE(i.cost_per_piece, 0))
                        ELSE 0
                    END
                ), 0) AS overage_items_value
            FROM stocktake_sessions ss
            LEFT JOIN stocktake_items si ON si.session_id = ss.id
            LEFT JOIN items i ON i.id = si.item_id
            WHERE DATE(ss.created_at) BETWEEN %s AND %s
              AND ss.status IN (%s, %s)
            """,
            (start_date, end_date, STOCKTAKE_STATUS_DRAFT, STOCKTAKE_STATUS_CONFIRMED),
        ).fetchone()

        session_rows = conn.execute(
            """
            SELECT
                id,
                session_number,
                status,
                created_at,
                confirmed_at
            FROM stocktake_sessions
            WHERE DATE(created_at) BETWEEN %s AND %s
              AND status IN (%s, %s, %s)
            ORDER BY created_at DESC, id DESC
            """,
            (
                start_date,
                end_date,
                STOCKTAKE_STATUS_DRAFT,
                STOCKTAKE_STATUS_CONFIRMED,
                STOCKTAKE_STATUS_CANCELLED,
            ),
        ).fetchall()

        item_rows = conn.execute(
            """
            SELECT
                si.*,
                i.name,
                i.category,
                i.pack_size,
                i.a4s_selling_price,
                i.cost_per_piece,
                ss.session_number,
                ss.status AS session_status,
                ss.created_at AS session_created_at,
                ss.confirmed_at AS session_confirmed_at
            FROM stocktake_items si
            JOIN stocktake_sessions ss ON ss.id = si.session_id
            JOIN items i ON i.id = si.item_id
            WHERE DATE(ss.created_at) BETWEEN %s AND %s
              AND ss.status IN (%s, %s)
            ORDER BY ss.created_at DESC, ss.id DESC, si.id DESC
            """,
            (start_date, end_date, STOCKTAKE_STATUS_DRAFT, STOCKTAKE_STATUS_CONFIRMED),
        ).fetchall()

        session_status_counts = {
            "completed": 0,
            "ongoing": 0,
            "cancelled": 0,
        }

        for row in session_rows:
            status = str(row["status"] or "").upper()
            if status == STOCKTAKE_STATUS_CONFIRMED:
                session_status_counts["completed"] += 1
            elif status == STOCKTAKE_STATUS_DRAFT:
                session_status_counts["ongoing"] += 1
            elif status == STOCKTAKE_STATUS_CANCELLED:
                session_status_counts["cancelled"] += 1

        report_items = [_serialize_item_row(row) for row in item_rows]
        for item in report_items:
            item["session_status"] = str(item.get("session_status") or STOCKTAKE_STATUS_DRAFT).upper()
            item["session_created_at_display"] = format_date(item.get("session_created_at"), show_time=True)
            item["session_confirmed_at_display"] = format_date(item.get("session_confirmed_at"), show_time=True)
        _attach_baseline_history(conn, {"items": report_items})

        return {
            "start_date": start_date,
            "end_date": end_date,
            "date_range_display": f"{format_date(start_date)} to {format_date(end_date)}",
            "summary": {
                "session_count": session_status_counts["completed"] + session_status_counts["ongoing"],
                "item_count": int(summary_row["item_count"] or 0),
                "variance_item_count": int(summary_row["variance_item_count"] or 0),
                "shortage_item_count": int(summary_row["shortage_item_count"] or 0),
                "overage_item_count": int(summary_row["overage_item_count"] or 0),
                "counted_items_value": float(summary_row["counted_items_value"] or 0),
                "variance_items_value": float(summary_row["variance_items_value"] or 0),
                "shortage_items_value": float(summary_row["shortage_items_value"] or 0),
                "overage_items_value": float(summary_row["overage_items_value"] or 0),
            },
            "session_status_counts": session_status_counts,
            "items": report_items,
        }
    finally:
        conn.close()


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
    data["active_system_stock"] = int(data.get("active_system_stock") or data["system_stock"] or 0)
    counted_stock = data.get("counted_stock")
    data["counted_stock"] = int(counted_stock) if counted_stock is not None else None
    data["variance"] = int(data.get("variance") or 0)
    data["adjustment_quantity"] = int(data.get("adjustment_quantity") or 0)
    data["is_applied"] = int(data.get("is_applied") or 0)
    data["baseline_mode"] = str(data.get("baseline_mode") or "CAPTURED").upper()
    data["has_refreshed_baseline"] = data["active_system_stock"] != data["system_stock"]
    data["cost_per_piece"] = float(data.get("cost_per_piece") or 0)
    data["system_value"] = data["system_stock"] * data["cost_per_piece"]
    data["active_system_value"] = data["active_system_stock"] * data["cost_per_piece"]
    data["counted_value"] = (
        data["counted_stock"] * data["cost_per_piece"]
        if data["counted_stock"] is not None
        else None
    )
    data["variance_value"] = data["variance"] * data["cost_per_piece"]
    if data["counted_stock"] is None:
        data["captured_variance"] = 0
        data["captured_variance_value"] = 0
    else:
        data["captured_variance"] = int(data["counted_stock"]) - data["system_stock"]
        data["captured_variance_value"] = data["captured_variance"] * data["cost_per_piece"]
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
                i.a4s_selling_price,
                i.cost_per_piece
            FROM stocktake_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.session_id = %s
            ORDER BY si.id DESC
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
        attach_recent_stocktake_metadata(
            conn,
            session_data["items"],
            item_id_key="item_id",
            exclude_session_id=session_id,
        )
        session_data["summary"] = {
            "item_count": session_data["item_count"],
            "variance_item_count": session_data["variance_item_count"],
            "overage_item_count": int(summary_row["overage_item_count"] or 0),
            "shortage_item_count": int(summary_row["shortage_item_count"] or 0),
            "total_overage_units": int(summary_row["total_overage_units"] or 0),
            "total_shortage_units": int(summary_row["total_shortage_units"] or 0),
        }
        session_data = _apply_live_stock_state(conn, session_data)
        return _attach_baseline_history(conn, session_data)
    finally:
        conn.close()


def add_stocktake_item(session_id, item_id, counted_stock=None, notes=None, actor_user_id=None, actor_username=None):
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
                active_system_stock,
                counted_stock,
                variance,
                baseline_mode,
                adjustment_type,
                adjustment_quantity,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                session_id,
                item_id,
                system_stock,
                system_stock,
                normalized_count,
                variance,
                "CAPTURED",
                adjustment_type,
                adjustment_quantity,
                (notes or "").strip() or None,
            ),
        ).fetchone()

        _record_baseline_history(
            conn,
            stocktake_item_id=int(row["id"]),
            event_type="CAPTURED",
            baseline_stock=system_stock,
            counted_stock_snapshot=normalized_count,
            variance_snapshot=variance,
            live_stock=system_stock,
            previous_active_stock=None,
            actor_user_id=actor_user_id if actor_user_id is not None else session_row["created_by"],
            actor_username=actor_username or session_row["created_by_username"],
        )

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


def update_stocktake_item(session_id, item_id, counted_stock, notes=None, actor_user_id=None, actor_username=None):
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

        line = _auto_refresh_active_baseline(
            conn,
            line,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
        )

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
                int(line["active_system_stock"] or line["system_stock"] or 0),
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


def bulk_save_stocktake_items(session_id, items, actor_user_id=None, actor_username=None):
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
            , active_system_stock
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

            line = _auto_refresh_active_baseline(
                conn,
                line,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
            )

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
                    int(line["active_system_stock"] or line["system_stock"] or 0),
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


def refresh_stocktake_item_baseline(session_id, item_id, user_id=None, username=None):
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
            raise ValueError("Only draft stocktake sessions can be refreshed.")

        line = conn.execute(
            """
            SELECT si.*, i.name
            FROM stocktake_items si
            JOIN items i ON i.id = si.item_id
            WHERE si.session_id = %s AND si.item_id = %s
            FOR UPDATE
            """,
            (session_id, item_id),
        ).fetchone()
        if not line:
            raise ValueError("Stocktake item was not found in this session.")

        active_stock = int(line["active_system_stock"] or line["system_stock"] or 0)
        live_stock = _get_live_stock(conn, item_id)
        if live_stock == active_stock:
            raise ValueError("This item no longer has live stock drift.")
        _auto_refresh_active_baseline(
            conn,
            line,
            actor_user_id=user_id,
            actor_username=username,
        )

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

        item_rows = [
            _auto_refresh_active_baseline(
                conn,
                row,
                actor_user_id=user_id,
                actor_username=username,
            )
            for row in item_rows
        ]

        for row in item_rows:
            variance = int(row["variance"] or 0)
            if variance == 0:
                continue

            counted_stock = int(row["counted_stock"] or 0)
            system_stock = int(row["active_system_stock"] or row["system_stock"] or 0)
            adjustment_type = "IN" if variance > 0 else "OUT"
            adjustment_quantity = abs(variance)
            change_reason = "STOCKTAKE_VARIANCE_GAIN" if variance > 0 else "STOCKTAKE_VARIANCE_LOSS"
            note_parts = [
                f"Stocktake {session_row['session_number']}",
                f"Captured system: {int(row['system_stock'] or 0)}",
                f"Active baseline: {system_stock}",
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
