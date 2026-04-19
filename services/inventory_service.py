import math
from datetime import date, datetime, timedelta

from db.database import get_db
from utils.formatters import format_date
from utils.timezone import now_local_naive, today_local


STOCKTAKE_WARNING_DAYS = 30
RESTOCK_VARIANCE_ALERT_DAYS = 30
RESTOCK_MANUAL_STOCK_REVIEW_DAYS = 30
RESTOCK_FAST_LOOKBACK_DAYS = 30
RESTOCK_DEFAULT_LOOKBACK_DAYS = 60
RESTOCK_SLOW_LOOKBACK_DAYS = 90
RESTOCK_LEAD_TIME_DAYS = 7
RESTOCK_SAFETY_DAYS = 7
LOW_HISTORY_MAX_OUT = 2
LOW_HISTORY_FALLBACK_FLOOR = 1
MIN_VENDOR_LEAD_TIME_SAMPLE = 3
DEMAND_OUT_REASONS = ("CUSTOMER_PURCHASE", "BUNDLE_PURCHASE")
FAST_MOVER_MIN_SALE_DAYS = 3
DEFAULT_MOVER_MIN_SALE_DAYS = 2
RESTOCK_STATUS_EXCLUDED = "excluded"
RESTOCK_STATUS_HEALTHY = "healthy"
RESTOCK_STATUS_WARNING = "warning"
RESTOCK_STATUS_CRITICAL = "critical"
RESTOCK_CONFIDENCE_NONE = "none"
RESTOCK_CONFIDENCE_LOW = "low"
RESTOCK_CONFIDENCE_HIGH = "high"
MANUAL_STOCK_IN_REASON_LATE_ENCODING = "LATE_ENCODING_MISSED_STOCK"


def _normalize_anchor_date(snapshot_date=None):
    if isinstance(snapshot_date, date):
        return snapshot_date
    if isinstance(snapshot_date, str) and snapshot_date:
        try:
            return date.fromisoformat(snapshot_date)
        except ValueError:
            pass
    return today_local()


def attach_inventory_history_profile(conn, items, item_id_key="id", category_key="category", snapshot_date=None):
    if not items:
        return items

    anchor_date = _normalize_anchor_date(snapshot_date)
    fast_window_start = anchor_date - timedelta(days=RESTOCK_FAST_LOOKBACK_DAYS - 1)
    default_window_start = anchor_date - timedelta(days=RESTOCK_DEFAULT_LOOKBACK_DAYS - 1)
    slow_window_start = anchor_date - timedelta(days=RESTOCK_SLOW_LOOKBACK_DAYS - 1)

    item_ids = []
    for item in items:
        try:
            item_ids.append(int(item.get(item_id_key)))
        except (TypeError, ValueError):
            continue

    history_map = {}
    if item_ids:
        rows = conn.execute(
            """
            SELECT
                item_id,
                COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'OUT'
                         AND change_reason = ANY(%s)
                         AND DATE(transaction_date) BETWEEN %s AND %s
                        THEN quantity
                        ELSE 0
                    END
                ), 0) AS total_out_last_30_days,
                COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'OUT'
                         AND change_reason = ANY(%s)
                         AND DATE(transaction_date) BETWEEN %s AND %s
                        THEN quantity
                        ELSE 0
                    END
                ), 0) AS total_out_last_60_days,
                COALESCE(SUM(
                    CASE
                        WHEN transaction_type = 'OUT'
                         AND change_reason = ANY(%s)
                         AND DATE(transaction_date) BETWEEN %s AND %s
                        THEN quantity
                        ELSE 0
                    END
                ), 0) AS total_out_last_90_days,
                COUNT(DISTINCT CASE
                    WHEN transaction_type = 'OUT'
                     AND change_reason = ANY(%s)
                     AND DATE(transaction_date) BETWEEN %s AND %s
                    THEN DATE(transaction_date)
                    ELSE NULL
                END) AS sale_days_last_30_days,
                COUNT(DISTINCT CASE
                    WHEN transaction_type = 'OUT'
                     AND change_reason = ANY(%s)
                     AND DATE(transaction_date) BETWEEN %s AND %s
                    THEN DATE(transaction_date)
                    ELSE NULL
                END) AS sale_days_last_60_days,
                COUNT(DISTINCT CASE
                    WHEN transaction_type = 'OUT'
                     AND change_reason = ANY(%s)
                     AND DATE(transaction_date) BETWEEN %s AND %s
                    THEN DATE(transaction_date)
                    ELSE NULL
                END) AS sale_days_last_90_days,
                MAX(
                    CASE
                        WHEN transaction_type = 'OUT'
                         AND change_reason = ANY(%s)
                        THEN transaction_date
                        ELSE NULL
                    END
                ) AS last_sold_at
            FROM inventory_transactions
            WHERE item_id = ANY(%s)
            GROUP BY item_id
            """,
            (
                list(DEMAND_OUT_REASONS),
                fast_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                default_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                slow_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                fast_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                default_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                slow_window_start.isoformat(),
                anchor_date.isoformat(),
                list(DEMAND_OUT_REASONS),
                item_ids,
            ),
        ).fetchall()
        history_map = {int(row["item_id"]): dict(row) for row in rows}

    for item in items:
        try:
            item_id = int(item.get(item_id_key))
        except (TypeError, ValueError):
            item_id = None

        category = str(item.get(category_key) or "").strip().lower()
        history = history_map.get(item_id, {}) if item_id is not None else {}
        total_out_30 = float(history.get("total_out_last_30_days") or 0)
        total_out_60 = float(history.get("total_out_last_60_days") or 0)
        total_out_90 = float(history.get("total_out_last_90_days") or 0)
        sale_days_30 = int(history.get("sale_days_last_30_days") or 0)
        sale_days_60 = int(history.get("sale_days_last_60_days") or 0)
        sale_days_90 = int(history.get("sale_days_last_90_days") or 0)
        last_sold_at = history.get("last_sold_at")

        if category == "svc":
            history_status = "excluded"
            selected_lookback_days = RESTOCK_DEFAULT_LOOKBACK_DAYS
            selected_total_out = 0.0
            selected_sale_days = 0
        elif total_out_30 >= 15 and sale_days_30 >= FAST_MOVER_MIN_SALE_DAYS:
            history_status = "active"
            selected_lookback_days = RESTOCK_FAST_LOOKBACK_DAYS
            selected_total_out = total_out_30
            selected_sale_days = sale_days_30
        elif 3 <= total_out_60 <= 14 and sale_days_60 >= DEFAULT_MOVER_MIN_SALE_DAYS:
            history_status = "active"
            selected_lookback_days = RESTOCK_DEFAULT_LOOKBACK_DAYS
            selected_total_out = total_out_60
            selected_sale_days = sale_days_60
        elif total_out_90 >= 1:
            history_status = "recovering"
            selected_lookback_days = RESTOCK_SLOW_LOOKBACK_DAYS
            selected_total_out = total_out_90
            selected_sale_days = sale_days_90
        else:
            history_status = "dead_stock"
            selected_lookback_days = RESTOCK_SLOW_LOOKBACK_DAYS
            selected_total_out = 0.0
            selected_sale_days = 0

        item["historical_out_last_30_days"] = round(total_out_30, 2)
        item["historical_out_last_60_days"] = round(total_out_60, 2)
        item["historical_out_last_90_days"] = round(total_out_90, 2)
        item["sale_days_last_30_days"] = sale_days_30
        item["sale_days_last_60_days"] = sale_days_60
        item["sale_days_last_90_days"] = sale_days_90
        item["selected_lookback_days"] = selected_lookback_days
        item["historical_out_in_selected_window"] = round(selected_total_out, 2)
        item["selected_sale_days"] = selected_sale_days
        item["last_sold_at"] = last_sold_at
        item["last_sold_display"] = format_date(last_sold_at) if last_sold_at else None
        item["history_status"] = history_status
        item["is_dead_stock"] = history_status == "dead_stock"
        item["is_recovering"] = history_status == "recovering"

    return items


def attach_vendor_lead_time_profile(conn, items, vendor_id_key="vendor_id"):
    if not items:
        return items

    vendor_ids = []
    for item in items:
        try:
            vendor_id = int(item.get(vendor_id_key) or 0)
        except (TypeError, ValueError):
            vendor_id = 0
        if vendor_id > 0:
            vendor_ids.append(vendor_id)

    vendor_ids = sorted(set(vendor_ids))
    lead_time_map = {}

    if vendor_ids:
        rows = conn.execute(
            """
            SELECT
                po.vendor_id,
                COUNT(*) AS completed_po_count,
                PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (po.received_at - po.created_at)) / 86400.0
                ) AS median_lead_time_days
            FROM purchase_orders po
            WHERE po.vendor_id = ANY(%s)
              AND po.status = 'COMPLETED'
              AND po.created_at IS NOT NULL
              AND po.received_at IS NOT NULL
              AND po.received_at >= po.created_at
            GROUP BY po.vendor_id
            """,
            (vendor_ids,),
        ).fetchall()

        lead_time_map = {
            int(row["vendor_id"]): {
                "sample_size": int(row["completed_po_count"] or 0),
                "median_lead_time_days": float(row["median_lead_time_days"] or 0),
            }
            for row in rows
        }

    for item in items:
        try:
            vendor_id = int(item.get(vendor_id_key) or 0)
        except (TypeError, ValueError):
            vendor_id = 0

        vendor_profile = lead_time_map.get(vendor_id, {})
        sample_size = int(vendor_profile.get("sample_size") or 0)
        vendor_median = float(vendor_profile.get("median_lead_time_days") or 0)

        if vendor_id > 0 and sample_size >= MIN_VENDOR_LEAD_TIME_SAMPLE and vendor_median > 0:
            effective_lead_time_days = max(1, math.ceil(vendor_median))
            lead_time_source = "vendor_completed_po_history"
        else:
            effective_lead_time_days = RESTOCK_LEAD_TIME_DAYS
            lead_time_source = "default"

        item["vendor_lead_time_sample_size"] = sample_size
        item["effective_lead_time_days"] = effective_lead_time_days
        item["lead_time_source"] = lead_time_source

    return items


def attach_recent_variance_loss_profile(conn, items, item_id_key="id", snapshot_date=None):
    if not items:
        return items

    anchor_date = _normalize_anchor_date(snapshot_date)
    variance_window_start = anchor_date - timedelta(days=RESTOCK_VARIANCE_ALERT_DAYS - 1)

    item_ids = []
    for item in items:
        try:
            item_ids.append(int(item.get(item_id_key)))
        except (TypeError, ValueError):
            continue

    if not item_ids:
        for item in items:
            item["recent_variance_loss_quantity"] = 0
            item["recent_variance_loss_days"] = 0
            item["recent_variance_loss_at"] = None
        return items

    rows = conn.execute(
        """
        SELECT
            item_id,
            COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'OUT'
                     AND change_reason = 'STOCKTAKE_VARIANCE_LOSS'
                     AND DATE(transaction_date) BETWEEN %s AND %s
                    THEN quantity
                    ELSE 0
                END
            ), 0) AS recent_variance_loss_quantity,
            COUNT(DISTINCT CASE
                WHEN transaction_type = 'OUT'
                 AND change_reason = 'STOCKTAKE_VARIANCE_LOSS'
                 AND DATE(transaction_date) BETWEEN %s AND %s
                THEN DATE(transaction_date)
                ELSE NULL
            END) AS recent_variance_loss_days,
            MAX(CASE
                WHEN transaction_type = 'OUT'
                 AND change_reason = 'STOCKTAKE_VARIANCE_LOSS'
                 AND DATE(transaction_date) BETWEEN %s AND %s
                THEN transaction_date
                ELSE NULL
            END) AS recent_variance_loss_at
        FROM inventory_transactions
        WHERE item_id = ANY(%s)
        GROUP BY item_id
        """,
        (
            variance_window_start.isoformat(),
            anchor_date.isoformat(),
            variance_window_start.isoformat(),
            anchor_date.isoformat(),
            variance_window_start.isoformat(),
            anchor_date.isoformat(),
            item_ids,
        ),
    ).fetchall()

    variance_map = {int(row["item_id"]): dict(row) for row in rows}

    for item in items:
        try:
            item_id = int(item.get(item_id_key))
        except (TypeError, ValueError):
            item_id = None

        variance_profile = variance_map.get(item_id, {}) if item_id is not None else {}
        item["recent_variance_loss_quantity"] = int(variance_profile.get("recent_variance_loss_quantity") or 0)
        item["recent_variance_loss_days"] = int(variance_profile.get("recent_variance_loss_days") or 0)
        item["recent_variance_loss_at"] = variance_profile.get("recent_variance_loss_at")

    return items


def attach_recent_manual_stock_in_review_profile(conn, items, item_id_key="id", snapshot_date=None):
    if not items:
        return items

    anchor_date = _normalize_anchor_date(snapshot_date)
    review_window_start = anchor_date - timedelta(days=RESTOCK_MANUAL_STOCK_REVIEW_DAYS - 1)

    item_ids = []
    for item in items:
        try:
            item_ids.append(int(item.get(item_id_key)))
        except (TypeError, ValueError):
            continue

    if not item_ids:
        for item in items:
            item["recent_manual_review_in_quantity"] = 0
            item["recent_manual_review_in_days"] = 0
            item["recent_manual_review_in_at"] = None
        return items

    rows = conn.execute(
        """
        SELECT
            item_id,
            COALESCE(SUM(
                CASE
                    WHEN transaction_type = 'IN'
                     AND reference_type = 'MANUAL_ADJUSTMENT'
                     AND change_reason = %s
                     AND DATE(transaction_date) BETWEEN %s AND %s
                    THEN quantity
                    ELSE 0
                END
            ), 0) AS recent_manual_review_in_quantity,
            COUNT(DISTINCT CASE
                WHEN transaction_type = 'IN'
                 AND reference_type = 'MANUAL_ADJUSTMENT'
                 AND change_reason = %s
                 AND DATE(transaction_date) BETWEEN %s AND %s
                THEN DATE(transaction_date)
                ELSE NULL
            END) AS recent_manual_review_in_days,
            MAX(CASE
                WHEN transaction_type = 'IN'
                 AND reference_type = 'MANUAL_ADJUSTMENT'
                 AND change_reason = %s
                 AND DATE(transaction_date) BETWEEN %s AND %s
                THEN transaction_date
                ELSE NULL
            END) AS recent_manual_review_in_at
        FROM inventory_transactions
        WHERE item_id = ANY(%s)
        GROUP BY item_id
        """,
        (
            MANUAL_STOCK_IN_REASON_LATE_ENCODING,
            review_window_start.isoformat(),
            anchor_date.isoformat(),
            MANUAL_STOCK_IN_REASON_LATE_ENCODING,
            review_window_start.isoformat(),
            anchor_date.isoformat(),
            MANUAL_STOCK_IN_REASON_LATE_ENCODING,
            review_window_start.isoformat(),
            anchor_date.isoformat(),
            item_ids,
        ),
    ).fetchall()

    review_map = {int(row["item_id"]): dict(row) for row in rows}

    for item in items:
        try:
            item_id = int(item.get(item_id_key))
        except (TypeError, ValueError):
            item_id = None

        review_profile = review_map.get(item_id, {}) if item_id is not None else {}
        item["recent_manual_review_in_quantity"] = int(review_profile.get("recent_manual_review_in_quantity") or 0)
        item["recent_manual_review_in_days"] = int(review_profile.get("recent_manual_review_in_days") or 0)
        item["recent_manual_review_in_at"] = review_profile.get("recent_manual_review_in_at")

    return items


def attach_incoming_po_profile(conn, items, item_id_key="id"):
    if not items:
        return items

    item_ids = []
    for item in items:
        try:
            item_ids.append(int(item.get(item_id_key)))
        except (TypeError, ValueError):
            continue

    if not item_ids:
        for item in items:
            item["incoming_po_quantity"] = 0
            item["incoming_po_count"] = 0
            item["incoming_pos"] = []
        return items

    rows = conn.execute(
        """
        SELECT
            pi.item_id,
            po.id AS po_id,
            po.po_number,
            po.status,
            po.created_at,
            SUM(GREATEST(pi.quantity_ordered - pi.quantity_received, 0)) AS remaining_quantity
        FROM po_items pi
        JOIN purchase_orders po ON po.id = pi.po_id
        WHERE pi.item_id = ANY(%s)
          AND po.status IN ('PENDING', 'PARTIAL')
          AND pi.quantity_ordered > pi.quantity_received
        GROUP BY pi.item_id, po.id, po.po_number, po.status, po.created_at
        ORDER BY po.created_at DESC, po.id DESC
        """,
        (item_ids,),
    ).fetchall()

    incoming_map = {}
    for row in rows:
        item_id = int(row["item_id"])
        incoming_map.setdefault(item_id, []).append(
            {
                "po_id": int(row["po_id"]),
                "po_number": row["po_number"] or f"PO #{row['po_id']}",
                "status": row["status"] or "",
                "remaining_quantity": int(row["remaining_quantity"] or 0),
            }
        )

    for item in items:
        try:
            item_id = int(item.get(item_id_key))
        except (TypeError, ValueError):
            item_id = None

        incoming_pos = incoming_map.get(item_id, []) if item_id is not None else []
        item["incoming_pos"] = incoming_pos
        item["incoming_po_count"] = len(incoming_pos)
        item["incoming_po_quantity"] = sum(
            int(po.get("remaining_quantity") or 0) for po in incoming_pos
        )

    return items


def attach_restock_recommendation(conn, items, item_id_key="id", category_key="category", current_stock_key="current_stock", snapshot_date=None):
    if not items:
        return items

    attach_inventory_history_profile(
        conn,
        items,
        item_id_key=item_id_key,
        category_key=category_key,
        snapshot_date=snapshot_date,
    )
    attach_vendor_lead_time_profile(conn, items, vendor_id_key="vendor_id")
    attach_recent_variance_loss_profile(conn, items, item_id_key=item_id_key, snapshot_date=snapshot_date)
    attach_recent_manual_stock_in_review_profile(conn, items, item_id_key=item_id_key, snapshot_date=snapshot_date)
    attach_incoming_po_profile(conn, items, item_id_key=item_id_key)

    for item in items:
        current_stock = float(item.get(current_stock_key) or 0)
        selected_lookback_days = int(item.get("selected_lookback_days") or RESTOCK_DEFAULT_LOOKBACK_DAYS)
        total_out = float(item.get("historical_out_in_selected_window") or 0)
        avg_daily_usage = total_out / selected_lookback_days if selected_lookback_days > 0 else 0
        effective_lead_time_days = int(item.get("effective_lead_time_days") or RESTOCK_LEAD_TIME_DAYS)
        safety_days = RESTOCK_SAFETY_DAYS

        item["avg_daily_usage"] = round(avg_daily_usage, 4)
        item["restock_coverage_days"] = effective_lead_time_days + safety_days
        item["lead_time_demand"] = 0
        item["safety_stock"] = 0
        item["restock_status"] = RESTOCK_STATUS_HEALTHY
        item["restock_confidence"] = RESTOCK_CONFIDENCE_NONE
        item["is_watchlist"] = False
        item["incoming_po_covers_restock"] = False
        item["hide_from_low_stock_notifications"] = False

        if item.get("history_status") == "excluded":
            item["suggested_restock_point"] = None
            item["should_restock"] = False
            item["restock_basis"] = "excluded"
            item["restock_status"] = RESTOCK_STATUS_EXCLUDED
            item["restock_confidence"] = RESTOCK_CONFIDENCE_NONE
            continue

        if item.get("history_status") == "dead_stock":
            item["suggested_restock_point"] = 0
            item["should_restock"] = False
            item["restock_basis"] = "dead_stock_excluded_from_reorder_alerts"
            item["restock_status"] = RESTOCK_STATUS_HEALTHY
            item["restock_confidence"] = RESTOCK_CONFIDENCE_NONE
            continue

        if item.get("history_status") == "recovering":
            suggested_restock_point = LOW_HISTORY_FALLBACK_FLOOR
            recent_variance_loss_quantity = int(item.get("recent_variance_loss_quantity") or 0)
            has_recent_variance_loss = recent_variance_loss_quantity > 0
            recent_manual_review_in_quantity = int(item.get("recent_manual_review_in_quantity") or 0)
            recent_manual_review_in_at = item.get("recent_manual_review_in_at")
            last_sold_at = item.get("last_sold_at")
            has_recent_manual_stock_review = (
                recent_manual_review_in_quantity > 0
                and current_stock <= 0
                and bool(last_sold_at)
                and bool(recent_manual_review_in_at)
                and last_sold_at >= recent_manual_review_in_at
            )
            item["suggested_restock_point"] = suggested_restock_point
            item["should_restock"] = (
                (has_recent_variance_loss and current_stock <= 0)
                or has_recent_manual_stock_review
            )
            item["is_watchlist"] = (not item["should_restock"]) and current_stock <= suggested_restock_point
            item["restock_basis"] = (
                "recovering_recent_variance_loss_zero_stock"
                if has_recent_variance_loss and current_stock <= 0
                else "recovering_manual_stock_history_review"
                if has_recent_manual_stock_review
                else "recovering_learning_watchlist"
            )
            item["restock_status"] = (
                RESTOCK_STATUS_CRITICAL if has_recent_variance_loss and current_stock <= 0
                else RESTOCK_STATUS_WARNING if has_recent_manual_stock_review
                else RESTOCK_STATUS_WARNING if item["is_watchlist"]
                else RESTOCK_STATUS_HEALTHY
            )
            item["restock_confidence"] = RESTOCK_CONFIDENCE_LOW
            incoming_po_quantity = int(item.get("incoming_po_quantity") or 0)
            if (
                item["should_restock"]
                and suggested_restock_point > 0
                and incoming_po_quantity >= suggested_restock_point
            ):
                item["incoming_po_covers_restock"] = True
                item["hide_from_low_stock_notifications"] = True
            continue

        lead_time_demand = math.ceil(avg_daily_usage * effective_lead_time_days)
        safety_stock = math.ceil(avg_daily_usage * safety_days)
        suggested_restock_point = lead_time_demand + safety_stock
        item["lead_time_demand"] = lead_time_demand
        item["safety_stock"] = safety_stock
        item["suggested_restock_point"] = suggested_restock_point
        item["should_restock"] = current_stock <= suggested_restock_point
        item["is_watchlist"] = False
        item["restock_basis"] = "movement_based"
        item["restock_confidence"] = RESTOCK_CONFIDENCE_HIGH
        item["restock_status"] = (
            RESTOCK_STATUS_CRITICAL if current_stock <= 0 or current_stock <= lead_time_demand
            else RESTOCK_STATUS_WARNING if item["should_restock"]
            else RESTOCK_STATUS_HEALTHY
        )
        incoming_po_quantity = int(item.get("incoming_po_quantity") or 0)
        if (
            item["should_restock"]
            and suggested_restock_point > 0
            and incoming_po_quantity >= suggested_restock_point
        ):
            item["incoming_po_covers_restock"] = True
            item["hide_from_low_stock_notifications"] = True

    return items


def attach_recent_stocktake_metadata(conn, items, item_id_key="id", exclude_session_id=None):
    if not items:
        return items

    item_ids = []
    for item in items:
        try:
            item_ids.append(int(item.get(item_id_key)))
        except (TypeError, ValueError):
            continue

    if not item_ids:
        return items

    query = """
        SELECT DISTINCT ON (si.item_id)
            si.item_id,
            ss.id AS stocktake_session_id,
            ss.session_number,
            ss.confirmed_at,
            si.counted_stock
        FROM stocktake_items si
        JOIN stocktake_sessions ss ON ss.id = si.session_id
        WHERE ss.status = 'CONFIRMED'
          AND ss.confirmed_at IS NOT NULL
          AND si.item_id = ANY(%s)
    """
    params = [item_ids]
    if exclude_session_id is not None:
        query += " AND ss.id <> %s"
        params.append(int(exclude_session_id))
    query += """
        ORDER BY si.item_id, ss.confirmed_at DESC, ss.id DESC
    """

    history_rows = conn.execute(query, tuple(params)).fetchall()

    history_map = {int(row["item_id"]): dict(row) for row in history_rows}
    now = now_local_naive()

    for item in items:
        try:
            item_id = int(item.get(item_id_key))
        except (TypeError, ValueError):
            continue

        history = history_map.get(item_id)
        item["has_stocktake_history"] = bool(history)
        item["recently_counted"] = False
        item["days_since_last_stocktake"] = None
        item["last_stocktake_session_id"] = None
        item["last_stocktake_session_number"] = None
        item["last_stocktake_confirmed_at"] = None
        item["last_stocktake_confirmed_at_display"] = None
        item["last_stocktake_counted_stock"] = None

        if not history:
            continue

        confirmed_at = history.get("confirmed_at")
        days_since = None
        if confirmed_at:
            days_since = max((now - confirmed_at).days, 0)

        item["days_since_last_stocktake"] = days_since
        item["last_stocktake_session_id"] = history.get("stocktake_session_id")
        item["last_stocktake_session_number"] = history.get("session_number")
        item["last_stocktake_confirmed_at"] = confirmed_at
        item["last_stocktake_confirmed_at_display"] = format_date(confirmed_at, show_time=True)
        counted_stock = history.get("counted_stock")
        item["last_stocktake_counted_stock"] = int(counted_stock) if counted_stock is not None else None
        item["recently_counted"] = days_since is not None and days_since <= STOCKTAKE_WARNING_DAYS

    return items

def get_items_with_stock(snapshot_date=None, external_conn=None, item_ids=None):
    conn = external_conn or get_db()
    try:
        normalized_item_ids = []
        for raw_id in item_ids or []:
            try:
                normalized_item_ids.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        params = []
        where_clause = ""
        if normalized_item_ids:
            where_clause = "WHERE items.id = ANY(%s)"
            params.append(normalized_item_ids)

        if snapshot_date:
            query = f"""
            SELECT 
                items.id,
                items.name,
                items.a4s_selling_price,
                COALESCE(SUM(
                    CASE 
                        WHEN inventory_transactions.transaction_type = 'IN'
                        THEN inventory_transactions.quantity
                        WHEN inventory_transactions.transaction_type = 'OUT'
                        AND inventory_transactions.transaction_date >= %s
                        THEN -inventory_transactions.quantity
                        ELSE 0
                    END
                ), 0) AS current_stock
            FROM items
            LEFT JOIN inventory_transactions
                ON items.id = inventory_transactions.item_id
            {where_clause}
            GROUP BY items.id
            """
            items = conn.execute(query, [snapshot_date, *params]).fetchall()
        else:
            query = f"""
            SELECT 
                items.id,
                items.name,
                items.a4s_selling_price,
                COALESCE(SUM(
                    CASE 
                        WHEN inventory_transactions.transaction_type = 'IN'
                        THEN inventory_transactions.quantity
                        WHEN inventory_transactions.transaction_type = 'OUT'
                        THEN -inventory_transactions.quantity
                        ELSE 0
                    END
                ), 0) AS current_stock
            FROM items
            LEFT JOIN inventory_transactions
                ON items.id = inventory_transactions.item_id
            {where_clause}
            GROUP BY items.id
            """
            items = conn.execute(query, params).fetchall() if params else conn.execute(query).fetchall()

        return items
    finally:
        if external_conn is None:
            conn.close()

def search_items_with_stock(search_query=None, snapshot_date="2026-03-26", item_id=None):
    conn = get_db()
    try:
        if item_id:
            sql = "SELECT * FROM items WHERE id = %s"
            rows = conn.execute(sql, (item_id,)).fetchall()
        elif search_query:
            normalized_query = search_query.strip()
            words = search_query.split()
            if not words:
                rows = conn.execute("SELECT * FROM items ORDER BY id DESC LIMIT 75").fetchall()
            else:
                query_parts = []
                params = []
                for word in words:
                    query_parts.append("(name ILIKE %s OR description ILIKE %s OR category ILIKE %s)")
                    pattern = f"%{word}%"
                    params.extend([pattern, pattern, pattern])

                where_clause = " AND ".join(query_parts)
                sql = f"""
                    SELECT * FROM items 
                    WHERE {where_clause}
                    ORDER BY
                        CASE
                            WHEN LOWER(TRIM(name)) = LOWER(TRIM(%s)) THEN 0
                            WHEN LOWER(TRIM(name)) LIKE LOWER(TRIM(%s)) THEN 1
                            WHEN LOWER(name) LIKE LOWER(%s) THEN 2
                            WHEN LOWER(COALESCE(description, '')) LIKE LOWER(%s) THEN 3
                            WHEN LOWER(category) LIKE LOWER(%s) THEN 4
                            ELSE 5
                        END,
                        id DESC
                    LIMIT 100
                """
                order_params = [
                    normalized_query,
                    f"{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                    f"%{normalized_query}%",
                ]
                rows = conn.execute(sql, params + order_params).fetchall()
        else:
            rows = []

        result_item_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
        if not result_item_ids:
            return []

        all_stock = get_items_with_stock(
            snapshot_date,
            external_conn=conn,
            item_ids=result_item_ids,
        )
        stock_map = {s["id"]: s["current_stock"] for s in all_stock}

        pending_rows = conn.execute("""
            SELECT
                pi.item_id,
                SUM(
                    CASE
                        WHEN COALESCE(pi.purchase_mode, 'PIECE') = 'PIECE'
                        THEN pi.quantity_ordered - pi.quantity_received
                        ELSE 0
                    END
                ) AS pending_stock,
                SUM(
                    CASE
                        WHEN COALESCE(pi.purchase_mode, 'PIECE') = 'BOX'
                        THEN pi.quantity_ordered - pi.quantity_received
                        ELSE 0
                    END
                ) AS pending_box_quantity
            FROM po_items pi
            JOIN purchase_orders po ON po.id = pi.po_id
            WHERE po.status IN ('PENDING', 'PARTIAL')
              AND pi.quantity_ordered > pi.quantity_received
              AND pi.item_id = ANY(%s)
            GROUP BY pi.item_id
        """, (result_item_ids,)).fetchall()
        pending_map = {row["item_id"]: row["pending_stock"] for row in pending_rows}
        pending_box_map = {row["item_id"]: row["pending_box_quantity"] for row in pending_rows}

        results = []
        for row in rows:
            d = dict(row)
            d["current_stock"] = stock_map.get(row["id"], 0)
            d["pending_stock"] = pending_map.get(row["id"], 0)
            d["pending_box_quantity"] = pending_box_map.get(row["id"], 0)
            results.append(d)

        attach_restock_recommendation(conn, results, item_id_key="id", category_key="category", current_stock_key="current_stock", snapshot_date=snapshot_date)
        attach_recent_stocktake_metadata(conn, results, item_id_key="id")
        return results
    finally:
        conn.close()

def get_vendor_recommended_items(vendor_id, limit=5, snapshot_date="2026-03-26"):
    conn = get_db()
    try:
        try:
            vendor_id = int(vendor_id)
        except (TypeError, ValueError):
            return []

        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 5

        rows = conn.execute(
            """
            SELECT
                i.id,
                i.name,
                i.cost_per_piece,
                COUNT(DISTINCT po.id) AS vendor_order_count,
                COALESCE(SUM(pi.quantity_ordered), 0) AS vendor_total_qty,
                MAX(po.created_at) AS last_ordered_at
            FROM purchase_orders po
            JOIN po_items pi ON pi.po_id = po.id
            JOIN items i ON i.id = pi.item_id
            WHERE po.vendor_id = %s
              AND po.status <> 'CANCELLED'
            GROUP BY i.id, i.name, i.cost_per_piece
            ORDER BY
                COUNT(DISTINCT po.id) DESC,
                COALESCE(SUM(pi.quantity_ordered), 0) DESC,
                MAX(po.created_at) DESC,
                i.name ASC
            LIMIT %s
            """,
            (vendor_id, limit),
        ).fetchall()

        if not rows:
            return []

        all_stock = get_items_with_stock(snapshot_date)
        stock_map = {s["id"]: s["current_stock"] for s in all_stock}

        pending_rows = conn.execute(
            """
            SELECT
                pi.item_id,
                SUM(
                    CASE
                        WHEN COALESCE(pi.purchase_mode, 'PIECE') = 'PIECE'
                        THEN pi.quantity_ordered - pi.quantity_received
                        ELSE 0
                    END
                ) AS pending_stock,
                SUM(
                    CASE
                        WHEN COALESCE(pi.purchase_mode, 'PIECE') = 'BOX'
                        THEN pi.quantity_ordered - pi.quantity_received
                        ELSE 0
                    END
                ) AS pending_box_quantity
            FROM po_items pi
            JOIN purchase_orders po ON po.id = pi.po_id
            WHERE po.status IN ('PENDING', 'PARTIAL')
              AND pi.quantity_ordered > pi.quantity_received
            GROUP BY pi.item_id
            """
        ).fetchall()
        pending_map = {row["item_id"]: row["pending_stock"] for row in pending_rows}
        pending_box_map = {row["item_id"]: row["pending_box_quantity"] for row in pending_rows}

        results = []
        for row in rows:
            item = dict(row)
            item["current_stock"] = stock_map.get(row["id"], 0)
            item["pending_stock"] = pending_map.get(row["id"], 0)
            item["pending_box_quantity"] = pending_box_map.get(row["id"], 0)
            results.append(item)

        return results
    finally:
        conn.close()

def get_unique_categories():
    conn = get_db()
    # DISTINCT ensures we don't get "Oil" five times if there are 5 oil items
    rows = conn.execute("SELECT DISTINCT category FROM items WHERE category IS NOT NULL AND category != ''").fetchall()
    conn.close()
    
    # Convert the list of row objects into a simple list of strings
    return [row['category'] for row in rows]

