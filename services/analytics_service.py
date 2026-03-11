from db.database import get_db

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

    low_stock_count = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT items.id,
            COALESCE(SUM(
                CASE 
                    WHEN inventory_transactions.transaction_type = 'IN'
                    THEN inventory_transactions.quantity
                    ELSE -inventory_transactions.quantity
                END
            ), 0) AS current_stock
            FROM items
            LEFT JOIN inventory_transactions
                ON items.id = inventory_transactions.item_id
            GROUP BY items.id
            HAVING COALESCE(SUM(
                CASE 
                    WHEN inventory_transactions.transaction_type = 'IN'
                    THEN inventory_transactions.quantity
                    ELSE -inventory_transactions.quantity
                END
            ), 0) <= items.reorder_level
        )
    """).fetchone()[0]

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
    rows = conn.execute("""
        SELECT 
            items.name,
            MAX(inventory_transactions.transaction_date) AS last_sold
        FROM items
        LEFT JOIN inventory_transactions 
            ON items.id = inventory_transactions.item_id
            AND inventory_transactions.transaction_type = 'OUT'
        GROUP BY items.id
        HAVING 
            MAX(inventory_transactions.transaction_date) IS NULL
            OR MAX(inventory_transactions.transaction_date) <= (NOW() - (%s * INTERVAL '1 day'))
    """, (days,)).fetchall()
    conn.close()
    return rows


def get_low_stock_items():
    conn = get_db()
    rows = conn.execute("""
        SELECT 
            items.name,
            items.reorder_level,
            COALESCE(SUM(
                CASE 
                    WHEN inventory_transactions.transaction_type = 'IN'
                    THEN inventory_transactions.quantity
                    ELSE -inventory_transactions.quantity
                END
            ), 0) AS current_stock
        FROM items
        LEFT JOIN inventory_transactions
            ON items.id = inventory_transactions.item_id
        GROUP BY items.id
        HAVING COALESCE(SUM(
            CASE 
                WHEN inventory_transactions.transaction_type = 'IN'
                THEN inventory_transactions.quantity
                ELSE -inventory_transactions.quantity
            END
        ), 0) <= items.reorder_level
        ORDER BY current_stock ASC
    """).fetchall()
    conn.close()
    return rows

