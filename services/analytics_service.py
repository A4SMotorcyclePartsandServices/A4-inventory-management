import copy
import time

from db.database import get_db
from services.inventory_service import (
    attach_inventory_history_profile,
    attach_restock_recommendation,
    get_items_with_stock,
)

LOW_STOCK_CACHE_TTL_SECONDS = 20
_low_stock_cache = {}


def _restock_status_rank(status):
    if status == "critical":
        return 0
    if status == "warning":
        return 1
    if status == "healthy":
        return 2
    return 3


def _restock_confidence_rank(confidence):
    if confidence == "high":
        return 0
    if confidence == "low":
        return 1
    return 2


def _copy_low_stock_rows(rows):
    return [dict(item) for item in (rows or [])]


def _compute_low_stock_items(include_watchlist=False):
    conn = get_db()
    try:
        item_rows = conn.execute("SELECT * FROM items").fetchall()
        stock_rows = get_items_with_stock()
        stock_map = {row["id"]: row["current_stock"] for row in stock_rows}

        rows = []
        for row in item_rows:
            item = dict(row)
            item["current_stock"] = stock_map.get(row["id"], 0)
            rows.append(item)

        attach_restock_recommendation(conn, rows, item_id_key="id", category_key="category", current_stock_key="current_stock")
        rows = [
            item for item in rows
            if item.get("should_restock") or (include_watchlist and item.get("is_watchlist"))
        ]
        rows.sort(
            key=lambda item: (
                _restock_confidence_rank(item.get("restock_confidence")),
                _restock_status_rank(item.get("restock_status")),
                float(item.get("current_stock") or 0),
                item.get("name") or "",
            )
        )
        return rows
    finally:
        conn.close()


def get_low_stock_items(*, include_watchlist=False, use_cache=True):
    cache_key = bool(include_watchlist)

    if use_cache:
        cached_entry = _low_stock_cache.get(cache_key)
        if cached_entry:
            cached_at = float(cached_entry.get("cached_at") or 0)
            if (time.monotonic() - cached_at) < LOW_STOCK_CACHE_TTL_SECONDS:
                return _copy_low_stock_rows(cached_entry.get("rows"))

    rows = _compute_low_stock_items(include_watchlist=include_watchlist)
    _low_stock_cache[cache_key] = {
        "cached_at": time.monotonic(),
        "rows": copy.deepcopy(rows),
    }
    return _copy_low_stock_rows(rows)

def get_dashboard_stats():
    conn = get_db()

    total_items = conn.execute(
        "SELECT COUNT(*) FROM items"
    ).fetchone()[0]

    total_stock = conn.execute("""
        SELECT COALESCE(SUM(
            CASE 
                WHEN transaction_type = 'IN' THEN quantity
                ELSE -quantity
            END
        ), 0)
        FROM inventory_transactions
    """).fetchone()[0]

    inventory_rows = conn.execute("SELECT * FROM items").fetchall()
    stock_rows = get_items_with_stock()
    stock_map = {row["id"]: row["current_stock"] for row in stock_rows}
    inventory_items = []
    for row in inventory_rows:
        item = dict(row)
        item["current_stock"] = stock_map.get(row["id"], 0)
        inventory_items.append(item)

    attach_restock_recommendation(conn, inventory_items, item_id_key="id", category_key="category", current_stock_key="current_stock")
    low_stock_count = sum(1 for item in inventory_items if item.get("should_restock"))

    top_item = conn.execute("""
        SELECT items.name, SUM(inventory_transactions.quantity) AS total_sold
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE inventory_transactions.transaction_type = 'OUT'
        AND inventory_transactions.transaction_date >= (NOW() - INTERVAL '30 days')
        GROUP BY items.id
        ORDER BY total_sold DESC
        LIMIT 1
    """).fetchone()

    items = conn.execute("SELECT id, name FROM items").fetchall()

    conn.close()

    return total_items, total_stock, low_stock_count, top_item, items

def get_hot_items(limit=5):
    conn = get_db()
    rows = conn.execute("""
        SELECT 
            items.name,
            SUM(inventory_transactions.quantity) AS total_sold_last_30_days
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE inventory_transactions.transaction_type = 'OUT'
        AND inventory_transactions.transaction_date >= (NOW() - INTERVAL '30 days')
        GROUP BY items.id
        ORDER BY total_sold_last_30_days DESC
        LIMIT %s
    """, (limit,)).fetchall()
    conn.close()
    return rows


def get_dead_stock(days=60):
    conn = get_db()
    item_rows = conn.execute("SELECT * FROM items").fetchall()
    rows = [dict(row) for row in item_rows]
    attach_inventory_history_profile(conn, rows, item_id_key="id", category_key="category")
    rows = [item for item in rows if item.get("history_status") == "dead_stock"]
    rows.sort(key=lambda item: ((item.get("last_sold_at") is not None), item.get("last_sold_at") or "", item.get("name") or ""))
    conn.close()
    return rows


def get_low_stock_page(page=1, per_page=75, *, include_watchlist=False, rows=None):
    try:
        safe_page = max(1, int(page or 1))
    except (TypeError, ValueError):
        safe_page = 1

    try:
        safe_per_page = max(1, min(int(per_page or 75), 200))
    except (TypeError, ValueError):
        safe_per_page = 75

    source_rows = rows if rows is not None else get_low_stock_items(include_watchlist=include_watchlist)
    total_count = len(source_rows)
    total_pages = max(1, (total_count + safe_per_page - 1) // safe_per_page)
    safe_page = min(safe_page, total_pages)

    start_index = (safe_page - 1) * safe_per_page
    end_index = start_index + safe_per_page

    return {
        "items": source_rows[start_index:end_index],
        "page": safe_page,
        "per_page": safe_per_page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_prev": safe_page > 1,
        "has_next": safe_page < total_pages,
        "prev_page": safe_page - 1,
        "next_page": safe_page + 1,
        "start_index": start_index + 1 if total_count else 0,
        "end_index": min(end_index, total_count),
    }


def get_low_stock_page_for_item(item_id, per_page=75, *, include_watchlist=False, rows=None):
    try:
        target_item_id = int(item_id)
    except (TypeError, ValueError):
        target_item_id = 0

    if target_item_id <= 0:
        return None

    try:
        safe_per_page = max(1, min(int(per_page or 75), 200))
    except (TypeError, ValueError):
        safe_per_page = 75

    source_rows = rows if rows is not None else get_low_stock_items(include_watchlist=include_watchlist)
    for index, item in enumerate(source_rows):
        try:
            current_item_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            current_item_id = 0
        if current_item_id == target_item_id:
            return (index // safe_per_page) + 1

    return None


def get_low_stock_summary(limit=8, *, rows=None):
    source_rows = rows if rows is not None else get_low_stock_items(include_watchlist=False)

    try:
        safe_limit = max(1, min(int(limit or 8), 50))
    except (TypeError, ValueError):
        safe_limit = 8

    critical_count = sum(1 for item in source_rows if item.get("restock_status") == "critical")
    warning_count = sum(1 for item in source_rows if item.get("restock_status") == "warning")

    summary_items = []
    for item in source_rows[:safe_limit]:
        summary_items.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or "Item",
                "current_stock": float(item.get("current_stock") or 0),
                "suggested_restock_point": int(item.get("suggested_restock_point") or 0),
                "restock_status": item.get("restock_status") or "warning",
                "restock_basis": item.get("restock_basis") or "",
            }
        )

    return {
        "total_count": len(source_rows),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "items": summary_items,
    }


def get_restock_debug_items(offset=0, limit=None):
    conn = get_db()
    item_rows = conn.execute("SELECT * FROM items").fetchall()
    stock_rows = get_items_with_stock()
    stock_map = {row["id"]: row["current_stock"] for row in stock_rows}

    rows = []
    for row in item_rows:
        item = dict(row)
        item["current_stock"] = stock_map.get(row["id"], 0)
        rows.append(item)

    attach_restock_recommendation(conn, rows, item_id_key="id", category_key="category", current_stock_key="current_stock")
    rows.sort(
        key=lambda item: (
            0 if item.get("should_restock") else 1,
            _restock_status_rank(item.get("restock_status")),
            str(item.get("history_status") or ""),
            float(item.get("current_stock") or 0),
            item.get("name") or "",
        )
    )
    total_count = len(rows)
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    conn.close()
    return {"items": rows, "total_count": total_count}

