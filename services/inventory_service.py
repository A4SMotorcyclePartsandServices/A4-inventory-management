from db.database import get_db

def get_items_with_stock(snapshot_date=None):
    conn = get_db()

    if snapshot_date:
        query = """
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
        GROUP BY items.id;
        """
        items = conn.execute(query, (snapshot_date,)).fetchall()
    else:
        query = """
        SELECT 
            items.id,
            items.name,
            items.a4s_selling_price,
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
        GROUP BY items.id;
        """
        items = conn.execute(query).fetchall()

    conn.close()
    return items

def search_items_with_stock(search_query=None, snapshot_date="2026-01-18", item_id=None):
    from db.database import get_db
    conn = get_db()
    
    # 1. FETCH THE ROWS
    # Case A: We are looking for ONE specific item by ID (Redirect from Add Item)
    if item_id:
        sql = "SELECT * FROM items WHERE id = %s"
        rows = conn.execute(sql, (item_id,)).fetchall()
        
    # Case B: We are doing a general text search (Normal Search)
    elif search_query:
        words = search_query.split()
        if not words:
            rows = conn.execute("SELECT * FROM items ORDER BY id DESC LIMIT 75").fetchall()
        else:
            query_parts = []
            params = []
            for word in words:
                query_parts.append("(name ILIKE %s OR description ILIKE %s)")
                pattern = f"%{word}%"
                params.extend([pattern, pattern])
            
            where_clause = " AND ".join(query_parts)
            
            # Note: Changed ORDER BY to id DESC so new items show at the top
            sql = f"""
                SELECT * FROM items 
                WHERE {where_clause}
                ORDER BY id DESC
                LIMIT 100
            """
            rows = conn.execute(sql, params).fetchall()
    else:
        rows = []

    # 2. GET STOCK LEVELS
    from services.inventory_service import get_items_with_stock
    all_stock = get_items_with_stock(snapshot_date)
    stock_map = {s["id"]: s["current_stock"] for s in all_stock}

    # 3. GET PENDING STOCK
    # Only counts units still outstanding on PENDING or PARTIAL POs.
    # quantity_ordered - quantity_received = true remaining balance.
    # NOTE (future branches): add branch_id filter here when ready.
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
        GROUP BY pi.item_id
    """).fetchall()
    pending_map = {row["item_id"]: row["pending_stock"] for row in pending_rows}
    pending_box_map = {row["item_id"]: row["pending_box_quantity"] for row in pending_rows}

    conn.close()

    # 4. MERGE
    results = []
    for row in rows:
        d = dict(row)
        d["current_stock"] = stock_map.get(row["id"], 0)
        d["pending_stock"] = pending_map.get(row["id"], 0)
        d["pending_box_quantity"] = pending_box_map.get(row["id"], 0)
        results.append(d)

    return results

def get_vendor_recommended_items(vendor_id, limit=5, snapshot_date="2026-01-18"):
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

