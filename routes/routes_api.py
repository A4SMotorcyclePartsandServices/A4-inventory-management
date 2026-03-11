from flask import Blueprint, request, jsonify
from db.database import get_db

dashboard_api = Blueprint("dashboard_api", __name__)

@dashboard_api.route("/dashboard/stock-movement")
def stock_movement():
    days = request.args.get("days", default=30, type=int)

    conn = get_db()
    rows = conn.execute("""
        SELECT 
            DATE(transaction_date) AS date,
            SUM(
                CASE 
                    WHEN transaction_type = 'IN' THEN quantity
                    ELSE -quantity
                END
            ) AS net_change
        FROM inventory_transactions
        WHERE transaction_date >= (NOW() - (%s * INTERVAL '1 day'))
        GROUP BY DATE(transaction_date)
        ORDER BY DATE(transaction_date)
    """, (days,)).fetchall()

    conn.close()

    return {
        "labels": [row["date"] for row in rows],
        "values": [row["net_change"] for row in rows]
    }

@dashboard_api.route("/dashboard/item-movement")
def item_movement():
    item_id = request.args.get("item_id", type=int)
    days = request.args.get("days", default=30, type=int)

    conn = get_db()

    rows = conn.execute("""
        SELECT 
            DATE(transaction_date) AS date,
            SUM(
                CASE 
                    WHEN transaction_type = 'IN' THEN quantity
                    ELSE -quantity
                END
            ) AS net_change
        FROM inventory_transactions
        WHERE item_id = %s
        AND transaction_date >= (NOW() - (%s * INTERVAL '1 day'))
        GROUP BY DATE(transaction_date)
        ORDER BY DATE(transaction_date)
    """, (item_id, days)).fetchall()

    conn.close()

    return {
        "labels": [row["date"] for row in rows],
        "values": [row["net_change"] for row in rows]
    }

@dashboard_api.route("/dashboard/top-items")
def top_items_chart():
    days = request.args.get("days", default=30, type=int)
    conn = get_db()

    rows = conn.execute("""
        SELECT 
            items.name,
            SUM(inventory_transactions.quantity) AS total_out
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE inventory_transactions.transaction_type = 'OUT'
        AND inventory_transactions.transaction_date >= (NOW() - (%s * INTERVAL '1 day'))
        GROUP BY items.id
        ORDER BY total_out DESC
        LIMIT 5
    """, (days,)).fetchall()

    conn.close()

    return {
        "labels": [row["name"] for row in rows],
        "values": [row["total_out"] for row in rows]
    }

@dashboard_api.route("/api/search/services")
def search_services():
    query = request.args.get('q', '').strip()
    include_inactive = str(request.args.get('include_inactive', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    if not query:
        return jsonify({"services": []})

    # Split the query into words for forgiving search
    words = query.split()
    # Create multiple ILIKE clauses: WHERE name ILIKE %word1% AND name ILIKE %word2%...
    where_clause = " AND ".join(["(name ILIKE %s OR category ILIKE %s)" for _ in words])
    params = []
    for word in words:
        params.extend([f'%{word}%', f'%{word}%'])

    active_clause = "" if include_inactive else "AND is_active = 1"

    conn = get_db()
    cursor = conn.execute(f"""
        SELECT id, name, category, is_active
        FROM services 
        WHERE {where_clause}
        {active_clause}
        LIMIT 20
    """, params)
    
    services = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"services": services})
