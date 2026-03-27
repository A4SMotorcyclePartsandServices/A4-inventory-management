from flask import Blueprint, request, jsonify
from db.database import get_db
from auth.utils import admin_required

dashboard_api = Blueprint("dashboard_api", __name__)

@dashboard_api.route("/items-analytics/stock-movement")
@admin_required
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

@dashboard_api.route("/items-analytics/item-movement")
@admin_required
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

@dashboard_api.route("/items-analytics/top-items")
@admin_required
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
    show_all = str(request.args.get('show_all', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    if not query and not show_all:
        return jsonify({"services": []})

    active_clause = "" if include_inactive else "AND is_active = 1"
    query_sql = f"""
        SELECT id, name, category, is_active
        FROM services
        WHERE 1=1
        {active_clause}
    """
    params = []

    if not show_all:
        words = query.split()
        where_clause = " AND ".join(["(name ILIKE %s OR category ILIKE %s)" for _ in words])
        query_sql += f" AND {where_clause}"
        for word in words:
            params.extend([f'%{word}%', f'%{word}%'])

    query_sql += " ORDER BY category ASC, name ASC LIMIT 50"

    conn = get_db()
    cursor = conn.execute(query_sql, params)
    
    services = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"services": services})


@dashboard_api.route("/api/search/items")
def search_items():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"items": []})

    words = query.split()
    where_clause = " AND ".join([
        "(name ILIKE %s OR category ILIKE %s OR COALESCE(description, '') ILIKE %s)"
        for _ in words
    ])
    params = []
    for word in words:
        pattern = f'%{word}%'
        params.extend([pattern, pattern, pattern])

    conn = get_db()
    cursor = conn.execute(f"""
        SELECT id, name, category, COALESCE(a4s_selling_price, 0) AS a4s_selling_price
        FROM items
        WHERE {where_clause}
        ORDER BY name ASC
        LIMIT 20
    """, params)

    items = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({"items": items})
