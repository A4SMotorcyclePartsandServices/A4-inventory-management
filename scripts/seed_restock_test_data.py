from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import get_db
from services.analytics_service import get_restock_debug_items


PREFIX = "TEST_RESTOCK"


def _dt(days_ago, hour=9):
    base = datetime.now() - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def cleanup():
    conn = get_db()
    try:
        item_rows = conn.execute(
            "SELECT id FROM items WHERE name LIKE %s",
            (f"{PREFIX}%",),
        ).fetchall()
        item_ids = [int(row["id"]) for row in item_rows]

        vendor_rows = conn.execute(
            "SELECT id FROM vendors WHERE vendor_name LIKE %s",
            (f"{PREFIX}%",),
        ).fetchall()
        vendor_ids = [int(row["id"]) for row in vendor_rows]

        if item_ids:
            conn.execute(
                "DELETE FROM inventory_transactions WHERE item_id = ANY(%s)",
                (item_ids,),
            )
            conn.execute(
                "DELETE FROM po_items WHERE item_id = ANY(%s)",
                (item_ids,),
            )
            conn.execute(
                "DELETE FROM items WHERE id = ANY(%s)",
                (item_ids,),
            )

        if vendor_ids:
            po_rows = conn.execute(
                "SELECT id FROM purchase_orders WHERE vendor_id = ANY(%s)",
                (vendor_ids,),
            ).fetchall()
            po_ids = [int(row["id"]) for row in po_rows]

            if po_ids:
                conn.execute("DELETE FROM po_receipt_items WHERE po_id = ANY(%s)", (po_ids,))
                conn.execute("DELETE FROM po_receipts WHERE po_id = ANY(%s)", (po_ids,))
                conn.execute("DELETE FROM po_items WHERE po_id = ANY(%s)", (po_ids,))
                conn.execute("DELETE FROM purchase_orders WHERE id = ANY(%s)", (po_ids,))

            conn.execute(
                "DELETE FROM vendors WHERE id = ANY(%s)",
                (vendor_ids,),
            )

        conn.commit()
        print("Cleaned existing restock test data.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_vendor(conn, name):
    return conn.execute(
        """
        INSERT INTO vendors (vendor_name, is_active, created_at, updated_at)
        VALUES (%s, 1, NOW(), NOW())
        RETURNING id
        """,
        (name,),
    ).fetchone()["id"]


def _insert_item(conn, name, category, vendor_id, reorder_level=0):
    return conn.execute(
        """
        INSERT INTO items (
            name, description, category, pack_size,
            vendor_price, cost_per_piece, a4s_selling_price,
            markup, reorder_level, vendor_id, mechanic
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            name,
            f"{name} description",
            category,
            "pc",
            100,
            100,
            130,
            0.3,
            reorder_level,
            vendor_id,
            "",
        ),
    ).fetchone()["id"]


def _insert_po(conn, vendor_id, vendor_name, created_at, received_at, item_id, po_number, qty=10, unit_cost=100):
    po_id = conn.execute(
        """
        INSERT INTO purchase_orders (
            po_number, vendor_id, vendor_name, status, total_amount,
            created_at, received_at
        )
        VALUES (%s, %s, %s, 'COMPLETED', %s, %s, %s)
        RETURNING id
        """,
        (po_number, vendor_id, vendor_name, qty * unit_cost, created_at, received_at),
    ).fetchone()["id"]

    conn.execute(
        """
        INSERT INTO po_items (po_id, item_id, quantity_ordered, quantity_received, unit_cost, purchase_mode)
        VALUES (%s, %s, %s, %s, %s, 'PIECE')
        """,
        (po_id, item_id, qty, qty, unit_cost),
    )
    return po_id


def _insert_tx(conn, item_id, qty, tx_type, when, reason, ref_id=None, ref_type=None):
    conn.execute(
        """
        INSERT INTO inventory_transactions (
            item_id, quantity, transaction_type, transaction_date,
            user_id, user_name, unit_price, reference_id, reference_type, change_reason, notes
        )
        VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s)
        """,
        (
            item_id,
            qty,
            tx_type,
            when,
            "restock-test",
            100,
            ref_id,
            ref_type,
            reason,
            PREFIX,
        ),
    )


def seed():
    cleanup()
    conn = get_db()
    try:
        fast_vendor_name = f"{PREFIX} FAST_VENDOR"
        slow_vendor_name = f"{PREFIX} SLOW_VENDOR"

        fast_vendor_id = _insert_vendor(conn, fast_vendor_name)
        slow_vendor_id = _insert_vendor(conn, slow_vendor_name)

        dead_zero_id = _insert_item(conn, f"{PREFIX} DEAD_ZERO", "PMS", fast_vendor_id)
        dead_positive_id = _insert_item(conn, f"{PREFIX} DEAD_POSITIVE", "PMS", fast_vendor_id)
        recovering_id = _insert_item(conn, f"{PREFIX} RECOVERING", "PMS", fast_vendor_id, reorder_level=99)
        active_fast_id = _insert_item(conn, f"{PREFIX} ACTIVE_FAST", "PMS", fast_vendor_id)
        active_slow_id = _insert_item(conn, f"{PREFIX} ACTIVE_SLOW", "Oil", slow_vendor_id)
        svc_item_id = _insert_item(conn, f"{PREFIX} SERVICE_EXCLUDED", "svc", fast_vendor_id)

        fast_pos = [(60, 53), (46, 39), (31, 24)]
        for idx, (created_days_ago, received_days_ago) in enumerate(fast_pos, start=1):
            created_at = _dt(created_days_ago)
            received_at = _dt(received_days_ago)
            po_id = _insert_po(
                conn,
                fast_vendor_id,
                fast_vendor_name,
                created_at,
                received_at,
                active_fast_id,
                f"{PREFIX}-FAST-{idx}",
            )
            _insert_tx(conn, active_fast_id, 10, "IN", received_at, "PO_ARRIVAL", po_id, "PURCHASE_ORDER")

        slow_pos = [(75, 60), (52, 37), (28, 13)]
        for idx, (created_days_ago, received_days_ago) in enumerate(slow_pos, start=1):
            created_at = _dt(created_days_ago)
            received_at = _dt(received_days_ago)
            po_id = _insert_po(
                conn,
                slow_vendor_id,
                slow_vendor_name,
                created_at,
                received_at,
                active_slow_id,
                f"{PREFIX}-SLOW-{idx}",
            )
            _insert_tx(conn, active_slow_id, 10, "IN", received_at, "PO_ARRIVAL", po_id, "PURCHASE_ORDER")

        _insert_tx(conn, dead_positive_id, 3, "IN", _dt(20), "MANUAL_TEST_IN")
        _insert_tx(conn, recovering_id, 2, "IN", _dt(20), "MANUAL_TEST_IN")
        _insert_tx(conn, recovering_id, 1, "OUT", _dt(1), "CUSTOMER_PURCHASE")
        _insert_tx(conn, dead_zero_id, 2, "IN", _dt(70), "MANUAL_TEST_IN")
        _insert_tx(conn, dead_zero_id, 2, "OUT", _dt(69), "CUSTOMER_PURCHASE")
        _insert_tx(conn, svc_item_id, 1, "IN", _dt(10), "MANUAL_TEST_IN")

        for days_ago in [18, 14, 10, 6, 2]:
            _insert_tx(conn, active_fast_id, 2, "OUT", _dt(days_ago), "CUSTOMER_PURCHASE")

        for qty, days_ago in [(4, 20), (6, 15), (6, 10), (6, 5)]:
            _insert_tx(conn, active_slow_id, qty, "OUT", _dt(days_ago), "CUSTOMER_PURCHASE")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("Seeded restock test data.")
    print_summary()


def print_summary():
    result = get_restock_debug_items()
    rows = [row for row in result["items"] if str(row.get("name") or "").startswith(PREFIX)]
    if not rows:
        print("No restock test rows found.")
        return

    print("")
    print("Expected highlights:")
    print("- DEAD_ZERO: dead_stock, critical, flagged YES")
    print("- DEAD_POSITIVE: dead_stock, healthy, flagged NO")
    print("- RECOVERING: recovering, warning, flagged YES when stock is 1")
    print("- ACTIVE_FAST: active, vendor lead time from completed PO history, healthy")
    print("- ACTIVE_SLOW: active, larger vendor lead time than FAST vendor, warning")
    print("- SERVICE_EXCLUDED: excluded, flagged NO")
    print("")
    for row in rows:
        print(
            f"{row['name']}: stock={row['current_stock']}, "
            f"history={row['history_status']}, "
            f"lead_days={row['effective_lead_time_days']}, "
            f"lead_source={row.get('lead_time_source')}, "
            f"samples={row.get('vendor_lead_time_sample_size')}, "
            f"restock_point={row.get('suggested_restock_point')}, "
            f"urgency={row.get('restock_status')}, "
            f"flagged={row.get('should_restock')}"
        )


if __name__ == "__main__":
    command = (sys.argv[1] if len(sys.argv) > 1 else "seed").strip().lower()
    if command == "seed":
        seed()
    elif command == "cleanup":
        cleanup()
    elif command == "summary":
        print_summary()
    else:
        raise SystemExit("Usage: python scripts/seed_restock_test_data.py [seed|cleanup|summary]")
