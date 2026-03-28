from db.database import get_db
from services.inventory_service import (
    attach_inventory_history_profile,
    attach_restock_recommendation,
    get_items_with_stock,
)


def _restock_status_rank(status):
    if status == "critical":
        return 0
    if status == "warning":
        return 1
    if status == "healthy":
        return 2
    return 3

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


def get_low_stock_items():
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
    rows = [item for item in rows if item.get("should_restock")]
    rows.sort(
        key=lambda item: (
            _restock_status_rank(item.get("restock_status")),
            float(item.get("current_stock") or 0),
            item.get("name") or "",
        )
    )
    conn.close()
    return rows


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

