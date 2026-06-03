from flask import Blueprint, request, jsonify
from datetime import date, timedelta
from db.database import get_db
from auth.utils import admin_required, login_required
from services.inventory_service import DEMAND_OUT_REASONS
from utils.timezone import today_local

dashboard_api = Blueprint("dashboard_api", __name__)


def _parse_items_analytics_date_range():
    today = today_local()
    default_start = today - timedelta(days=29)

    start_date = (request.args.get("start_date") or "").strip()
    end_date = (request.args.get("end_date") or "").strip()

    if start_date or end_date:
        try:
            start_obj = date.fromisoformat(start_date or end_date)
            end_obj = date.fromisoformat(end_date or start_date)
        except ValueError:
            start_obj = default_start
            end_obj = today
    else:
        days = request.args.get("days", default=30, type=int) or 30
        days = max(1, min(days, 3660))
        end_obj = today
        start_obj = today - timedelta(days=days - 1)

    if end_obj < start_obj:
        start_obj, end_obj = end_obj, start_obj

    return start_obj.isoformat(), end_obj.isoformat()

@dashboard_api.route("/items-analytics/stock-movement")
@admin_required
def stock_movement():
    start_date, end_date = _parse_items_analytics_date_range()

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
        WHERE DATE(transaction_date) BETWEEN %s AND %s
        GROUP BY DATE(transaction_date)
        ORDER BY DATE(transaction_date)
    """, (start_date, end_date)).fetchall()

    conn.close()

    return {
        "labels": [row["date"].strftime("%b %d") if row["date"] else "" for row in rows],
        "values": [row["net_change"] for row in rows]
    }

@dashboard_api.route("/items-analytics/item-movement")
@admin_required
def item_movement():
    item_id = request.args.get("item_id", type=int)
    start_date, end_date = _parse_items_analytics_date_range()

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
        AND DATE(transaction_date) BETWEEN %s AND %s
        GROUP BY DATE(transaction_date)
        ORDER BY DATE(transaction_date)
    """, (item_id, start_date, end_date)).fetchall()

    conn.close()

    return {
        "labels": [row["date"].strftime("%b %d") if row["date"] else "" for row in rows],
        "values": [row["net_change"] for row in rows]
    }

@dashboard_api.route("/items-analytics/top-items")
@admin_required
def top_items_chart():
    start_date, end_date = _parse_items_analytics_date_range()
    conn = get_db()

    rows = conn.execute("""
        SELECT 
            items.name,
            SUM(inventory_transactions.quantity) AS total_out
        FROM inventory_transactions
        JOIN items ON items.id = inventory_transactions.item_id
        WHERE inventory_transactions.transaction_type = 'OUT'
        AND inventory_transactions.change_reason = ANY(%s)
        AND DATE(inventory_transactions.transaction_date) BETWEEN %s AND %s
        GROUP BY items.id
        ORDER BY total_out DESC
        LIMIT 5
    """, (list(DEMAND_OUT_REASONS), start_date, end_date)).fetchall()

    conn.close()

    return {
        "labels": [row["name"] for row in rows],
        "values": [row["total_out"] for row in rows]
    }

@dashboard_api.route("/api/search/services")
@login_required
def search_services():
    query = request.args.get('q', '').strip()
    include_inactive = str(request.args.get('include_inactive', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    show_all = str(request.args.get('show_all', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    if not query and not show_all:
        return jsonify({"services": []})

    query_parts = [
        """
        SELECT id, name, category, is_active, COALESCE(mechanic_payout_exempt, 0) AS mechanic_payout_exempt
        FROM services
        WHERE 1=1
        """
    ]
    params = []

    if not include_inactive:
        query_parts.append("AND is_active = 1")

    if not show_all:
        words = query.split()
        where_clause = " AND ".join(["name ILIKE %s" for _ in words])
        query_parts.append("AND " + where_clause)
        for word in words:
            params.append(f'%{word}%')

    query_parts.append("ORDER BY category ASC, name ASC LIMIT 50")
    query_sql = "\n".join(query_parts)

    conn = get_db()
    cursor = conn.execute(query_sql, params)
    
    services = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({"services": services})


@dashboard_api.route("/api/search/items")
@login_required
def search_items():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({"items": []})

    normalized_query = query.strip()
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
    query_sql = """
        SELECT
            id,
            name,
            category,
            COALESCE(description, '') AS description,
            COALESCE(a4s_selling_price, 0) AS a4s_selling_price,
            COALESCE(cost_per_piece, 0) AS cost_per_piece
        FROM items
        WHERE """ + where_clause + """
        ORDER BY
            CASE
                WHEN LOWER(TRIM(name)) = LOWER(TRIM(%s)) THEN 0
                WHEN LOWER(TRIM(name)) LIKE LOWER(TRIM(%s)) THEN 1
                WHEN LOWER(name) LIKE LOWER(%s) THEN 2
                WHEN LOWER(COALESCE(description, '')) LIKE LOWER(%s) THEN 3
                WHEN LOWER(category) LIKE LOWER(%s) THEN 4
                ELSE 5
            END,
            name ASC,
            id DESC
        LIMIT 20
    """
    order_params = [
        normalized_query,
        f'{normalized_query}%',
        f'%{normalized_query}%',
        f'%{normalized_query}%',
        f'%{normalized_query}%',
    ]
    cursor = conn.execute(query_sql, params + order_params)

    items = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({"items": items})
