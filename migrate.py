import sqlite3
import psycopg2
import psycopg2.extras
import os
from dotenv import load_dotenv

load_dotenv()

# --- CONNECTIONS ---
sqlite_conn = sqlite3.connect("inventory.db")
sqlite_conn.row_factory = sqlite3.Row

pg_conn = psycopg2.connect(
    host=os.environ["DB_HOST"],
    port=os.environ.get("DB_PORT", 5432),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"]
)
pg_cur = pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

def migrate_table(table_name, column_names):
    """
    Reads all rows from SQLite for a given table
    and inserts them into PostgreSQL preserving original IDs.
    """
    rows = sqlite_conn.execute(
        f"SELECT * FROM {table_name}"
    ).fetchall()

    if not rows:
        print(f"  {table_name}: empty, skipping.")
        return

    placeholders = ", ".join(["%s"] * len(column_names))
    columns      = ", ".join(column_names)

    inserted = 0
    skipped  = 0

    for row in rows:
        values = [row[col] for col in column_names]
        try:
            pg_cur.execute(
                f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                values
            )
            inserted += 1
        except Exception as e:
            print(f"  ⚠ Skipped row in {table_name}: {e}")
            pg_conn.rollback()
            skipped += 1
            continue

    pg_conn.commit()
    print(f"  {table_name}: {inserted} inserted, {skipped} skipped.")

def reset_sequence(table_name):
    """
    After migrating data with existing IDs, reset PostgreSQL's
    SERIAL counter so new inserts don't collide with migrated IDs.
    """
    pg_cur.execute(f"""
        SELECT setval(
            pg_get_serial_sequence('{table_name}', 'id'),
            COALESCE((SELECT MAX(id) FROM {table_name}), 1)
        )
    """)
    pg_conn.commit()

def migrate_sales():
    """
    Sales gets its own migration function because of orphaned
    payment_method_id references in SQLite. Instead of skipping
    the whole sale, we set payment_method_id to NULL so all child
    records (sales_items, sales_services, debt_payments) still migrate.
    """
    rows = sqlite_conn.execute("SELECT * FROM sales").fetchall()

    if not rows:
        print("  sales: empty, skipping.")
        return

    inserted = 0
    skipped  = 0

    for row in rows:
        # Check if payment_method_id exists in SQLite payment_methods.
        # If it's an orphaned ID (deleted payment method), set to NULL
        # rather than skipping the entire sale.
        pm_id = row["payment_method_id"]
        if pm_id:
            exists = sqlite_conn.execute(
                "SELECT id FROM payment_methods WHERE id = ?", (pm_id,)
            ).fetchone()
            if not exists:
                pm_id = None

        try:
            pg_cur.execute("""
                INSERT INTO sales (
                    id, sales_number, customer_name, total_amount,
                    payment_method_id, reference_no, status, notes,
                    user_id, transaction_date, customer_id, vehicle_id,
                    mechanic_id, service_fee, paid_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING
            """, (
                row["id"], row["sales_number"], row["customer_name"], row["total_amount"],
                pm_id, row["reference_no"], row["status"], row["notes"],
                row["user_id"], row["transaction_date"], row["customer_id"], row["vehicle_id"],
                row["mechanic_id"], row["service_fee"], row["paid_at"]
            ))
            pg_conn.commit()
            inserted += 1
        except Exception as e:
            print(f"  ⚠ Skipped sale id {row['id']}: {e}")
            pg_conn.rollback()
            skipped += 1

    print(f"  sales: {inserted} inserted, {skipped} skipped.")

# ============================================================
# MIGRATION ORDER — parent tables must come before child tables
# ============================================================
print("\n Starting migration...\n")

# 1. Users
migrate_table("users", [
    "id", "username", "password_hash", "role",
    "is_active", "created_at", "created_by"
])

# 2. Mechanics
migrate_table("mechanics", [
    "id", "name", "commission_rate", "phone", "is_active"
])

# 3. Items
migrate_table("items", [
    "id", "name", "description", "category", "pack_size",
    "vendor_price", "cost_per_piece", "a4s_selling_price",
    "markup", "reorder_level", "vendor", "mechanic"
])

# 4. Payment Methods
migrate_table("payment_methods", [
    "id", "name", "category", "is_active"
])

# 5. Customers
migrate_table("customers", [
    "id", "customer_no", "customer_name", "is_active", "created_at"
])

# 6. Vehicles
migrate_table("vehicles", [
    "id", "customer_id", "vehicle_name", "is_active",
    "created_at", "updated_at"
])

# 7. Sales (custom migration to handle orphaned payment_method_id)
migrate_sales()

# 8. Inventory Transactions
migrate_table("inventory_transactions", [
    "id", "item_id", "quantity", "transaction_type",
    "transaction_date", "user_id", "user_name", "unit_price",
    "reference_id", "reference_type", "change_reason", "notes"
])

# 9. Services
migrate_table("services", [
    "id", "name", "category", "is_active"
])

# 10. Sales Services
migrate_table("sales_services", [
    "id", "sale_id", "service_id", "price"
])

# 11. Sales Items
migrate_table("sales_items", [
    "id", "sale_id", "item_id", "quantity", "original_unit_price",
    "discount_percent", "discount_amount", "final_unit_price",
    "discounted_by", "created_at"
])

# 12. Purchase Orders
migrate_table("purchase_orders", [
    "id", "po_number", "vendor_name", "status", "total_amount",
    "created_at", "received_at", "created_by", "notes"
])

# 13. PO Items
migrate_table("po_items", [
    "id", "po_id", "item_id", "quantity_ordered",
    "quantity_received", "unit_cost"
])

# 14. Loyalty Programs
migrate_table("loyalty_programs", [
    "id", "name", "program_type", "qualifying_id", "threshold",
    "reward_type", "reward_value", "reward_description",
    "period_start", "period_end", "branch_id", "is_active",
    "created_at", "created_by"
])

# 15. Loyalty Stamps
migrate_table("loyalty_stamps", [
    "id", "customer_id", "program_id", "sale_id",
    "redemption_id", "stamped_at"
])

# 16. Loyalty Redemptions
# reward_snapshot was TEXT in SQLite — PostgreSQL expects JSONB.
# psycopg2 handles the cast automatically on insert.
migrate_table("loyalty_redemptions", [
    "id", "customer_id", "program_id", "applied_on_sale_id",
    "redeemed_by", "reward_snapshot", "stamps_consumed", "redeemed_at"
])

# 17. Debt Payments
migrate_table("debt_payments", [
    "id", "sale_id", "amount_paid", "payment_method_id",
    "reference_no", "notes", "paid_by", "paid_at", "service_portion"
])

# 18. Cash Entries
migrate_table("cash_entries", [
    "id", "branch_id", "entry_type", "amount", "category",
    "description", "payout_for_date", "reference_type",
    "reference_id", "user_id", "created_at"
])

# ============================================================
# RESET ALL SEQUENCES
# Must run after all data is inserted so new rows
# don't collide with migrated IDs
# ============================================================
print("\n Resetting sequences...\n")

tables_with_serial = [
    "users", "mechanics", "items", "payment_methods",
    "customers", "vehicles", "sales", "inventory_transactions",
    "services", "sales_services", "sales_items",
    "purchase_orders", "po_items", "loyalty_programs",
    "loyalty_stamps", "loyalty_redemptions",
    "debt_payments", "cash_entries"
]

for table in tables_with_serial:
    reset_sequence(table)
    print(f"  {table}: sequence reset.")

# --- CLOSE ---
sqlite_conn.close()
pg_cur.close()
pg_conn.close()

print("\n Migration complete!\n")