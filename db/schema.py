from db.database import get_db, get_cursor

def init_db():
    conn = get_db()
    cur = get_cursor(conn)

    # 1. USERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id              SERIAL PRIMARY KEY,
        username        TEXT NOT NULL UNIQUE,
        password_hash   TEXT NOT NULL,
        role            TEXT CHECK(role IN ('admin', 'staff')) NOT NULL,
        is_active       INTEGER DEFAULT 1,
        created_at      TIMESTAMP DEFAULT NOW(),
        created_by      INTEGER REFERENCES users(id)
    )
    """)

    # 2. MECHANICS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mechanics (
        id              SERIAL PRIMARY KEY,
        name            TEXT NOT NULL UNIQUE,
        commission_rate NUMERIC(5,2) DEFAULT 0.80,
        phone           TEXT,
        is_active       INTEGER DEFAULT 1
    )
    """)

    # 3. ITEMS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS items (
        id                  SERIAL PRIMARY KEY,
        name                TEXT NOT NULL UNIQUE,
        description         TEXT,
        category            TEXT,
        pack_size           TEXT,
        vendor_price        NUMERIC(12,2),
        cost_per_piece      NUMERIC(12,2),
        a4s_selling_price   NUMERIC(12,2),
        markup              NUMERIC(12,2),
        reorder_level       INTEGER DEFAULT 0,
        vendor              TEXT,
        mechanic            TEXT
    )
    """)

    # 4. PAYMENT METHODS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_methods (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT NOT NULL,
        is_active   INTEGER DEFAULT 1
    )
    """)

    # 5. CUSTOMERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id              SERIAL PRIMARY KEY,
        customer_no     TEXT NOT NULL UNIQUE,
        customer_name   TEXT NOT NULL,
        is_active       INTEGER DEFAULT 1,
        created_at      TIMESTAMP DEFAULT NOW()
    )
    """)

    # 6. VEHICLES TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vehicles (
        id          SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(id),
        vehicle_name TEXT NOT NULL,
        is_active   INTEGER DEFAULT 1,
        created_at  TIMESTAMP DEFAULT NOW(),
        updated_at  TIMESTAMP DEFAULT NOW()
    )
    """)

    # 7. SALES TABLE
    # customer_id, vehicle_id, mechanic_id, service_fee, paid_at
    # are included directly here — no migrations needed on fresh DB
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id                  SERIAL PRIMARY KEY,
        sales_number        TEXT,
        customer_name       TEXT,
        total_amount        NUMERIC(12,2) NOT NULL,
        payment_method_id   INTEGER REFERENCES payment_methods(id),
        reference_no        TEXT,
        status              TEXT CHECK(status IN ('Paid', 'Unresolved', 'Partial')) NOT NULL,
        notes               TEXT,
        user_id             INTEGER REFERENCES users(id),
        transaction_date    TIMESTAMP DEFAULT NOW(),
        customer_id         INTEGER REFERENCES customers(id),
        vehicle_id          INTEGER REFERENCES vehicles(id),
        mechanic_id         INTEGER REFERENCES mechanics(id),
        service_fee         NUMERIC(12,2) DEFAULT 0,
        paid_at             TIMESTAMP
    )
    """)

    # 8. INVENTORY TRANSACTIONS
    # reference_id replaces sale_id (The "Universal Key")
    # reference_type tells us if reference_id points to a Sale, PO, or Swap
    # change_reason is machine-readable code (BONUS_STOCK, PO_ARRIVAL, etc.)
    # notes is the free-text field staff fills in to explain why
    cur.execute("""
    CREATE TABLE IF NOT EXISTS inventory_transactions (
        id                  SERIAL PRIMARY KEY,
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity            INTEGER NOT NULL,
        transaction_type    TEXT CHECK(transaction_type IN ('IN', 'OUT', 'ORDER')),
        transaction_date    TIMESTAMP DEFAULT NOW(),
        user_id             INTEGER REFERENCES users(id),
        user_name           TEXT,
        unit_price          NUMERIC(12,2),
        reference_id        INTEGER,
        reference_type      TEXT,
        change_reason       TEXT,
        notes               TEXT
    )
    """)

    # 9. SERVICES TABLE (The Master List of Labor Types)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT DEFAULT 'Labor',
        is_active   INTEGER DEFAULT 1
    )
    """)

    # 10. SALES SERVICES TABLE (The "Labor" Ledger)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_services (
        id          SERIAL PRIMARY KEY,
        sale_id     INTEGER NOT NULL REFERENCES sales(id),
        service_id  INTEGER NOT NULL REFERENCES services(id),
        price       NUMERIC(12,2) NOT NULL
    )
    """)

    # 11. SALES ITEMS TABLE (Item-level sales & discounts)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_items (
        id                  SERIAL PRIMARY KEY,
        sale_id             INTEGER NOT NULL REFERENCES sales(id),
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity            INTEGER NOT NULL,
        original_unit_price NUMERIC(12,2) NOT NULL,
        discount_percent    NUMERIC(5,2) DEFAULT 0,
        discount_amount     NUMERIC(12,2) DEFAULT 0,
        final_unit_price    NUMERIC(12,2) NOT NULL,
        discounted_by       INTEGER REFERENCES users(id),
        created_at          TIMESTAMP DEFAULT NOW()
    )
    """)

    # 12. PURCHASE ORDERS (The Header)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id              SERIAL PRIMARY KEY,
        po_number       TEXT UNIQUE,
        vendor_name     TEXT,
        status          TEXT CHECK(status IN ('PENDING', 'PARTIAL', 'COMPLETED', 'CANCELLED')) DEFAULT 'PENDING',
        total_amount    NUMERIC(12,2) DEFAULT 0,
        created_at      TIMESTAMP DEFAULT NOW(),
        received_at     TIMESTAMP,
        created_by      INTEGER REFERENCES users(id),
        notes           TEXT
    )
    """)

    # 13. PURCHASE ORDER ITEMS (The Details)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS po_items (
        id                  SERIAL PRIMARY KEY,
        po_id               INTEGER NOT NULL REFERENCES purchase_orders(id),
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity_ordered    INTEGER NOT NULL,
        quantity_received   INTEGER DEFAULT 0,
        unit_cost           NUMERIC(12,2)
    )
    """)

    # 14. LOYALTY PROGRAMS TABLE
    # program_type: 'SERVICE' = stamps earned per qualifying service visit
    #               'ITEM'    = stamps earned per qualifying item purchase
    #
    # qualifying_id: points to services.id (SERVICE type) or items.id (ITEM type)
    #   - enforced at app level; no composite FK at DB level
    #
    # reward_type options:
    #   FREE_SERVICE     → reward_value = services.id of the free service
    #   FREE_ITEM        → reward_value = items.id of the free item
    #   DISCOUNT_PERCENT → reward_value = percent off (e.g. 10 = 10%)
    #   DISCOUNT_AMOUNT  → reward_value = flat peso off
    #
    # branch_id: NULL means the program applies to ALL branches (global)
    #   When Branch 2 opens, set branch_id = that branch's ID for branch-specific promos.
    #
    # stamps_expire_with_period: enforced at query level (stamp must be within period dates).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loyalty_programs (
        id                  SERIAL PRIMARY KEY,
        name                TEXT NOT NULL,
        program_type        TEXT NOT NULL CHECK(program_type IN ('SERVICE', 'ITEM')),
        qualifying_id       INTEGER NOT NULL,
        threshold           INTEGER NOT NULL DEFAULT 10,
        reward_type         TEXT NOT NULL CHECK(reward_type IN (
                                'FREE_SERVICE', 'FREE_ITEM',
                                'DISCOUNT_PERCENT', 'DISCOUNT_AMOUNT'
                            )),
        reward_value        NUMERIC(12,2) NOT NULL DEFAULT 0,
        reward_description  TEXT,
        period_start        DATE NOT NULL,
        period_end          DATE NOT NULL,
        branch_id           INTEGER DEFAULT NULL,
        is_active           INTEGER DEFAULT 1,
        created_at          TIMESTAMP DEFAULT NOW(),
        created_by          INTEGER REFERENCES users(id)
    )
    """)

    # 15. LOYALTY STAMPS TABLE
    # One row = one qualifying transaction earned toward a program.
    # redemption_id = NULL means the stamp is unconsumed / still active.
    # redemption_id = set  means the stamp was consumed in that redemption.
    #
    # Eligibility count = COUNT(*) WHERE redemption_id IS NULL
    #                     AND stamped_at BETWEEN program.period_start AND program.period_end
    #
    # The period date filter is what implements "stamps expire with the period."
    # No backfilling to the next period is possible without a new stamp row.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loyalty_stamps (
        id              SERIAL PRIMARY KEY,
        customer_id     INTEGER NOT NULL REFERENCES customers(id),
        program_id      INTEGER NOT NULL REFERENCES loyalty_programs(id),
        sale_id         INTEGER NOT NULL REFERENCES sales(id),
        redemption_id   INTEGER DEFAULT NULL,
        stamped_at      TIMESTAMP DEFAULT NOW()
    )
    """)

    # 16. LOYALTY REDEMPTIONS TABLE
    # One row = one reward granted to a customer.
    # reward_snapshot: frozen JSON of the reward at time of redemption.
    #   Critical for history accuracy — program config can change later.
    #   Using JSONB for better storage and querying vs plain TEXT.
    # applied_on_sale_id: the sale where the reward was applied (discount/free item).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loyalty_redemptions (
        id                  SERIAL PRIMARY KEY,
        customer_id         INTEGER NOT NULL REFERENCES customers(id),
        program_id          INTEGER NOT NULL REFERENCES loyalty_programs(id),
        applied_on_sale_id  INTEGER NOT NULL REFERENCES sales(id),
        redeemed_by         INTEGER REFERENCES users(id),
        reward_snapshot     JSONB NOT NULL,
        stamps_consumed     INTEGER NOT NULL,
        redeemed_at         TIMESTAMP DEFAULT NOW()
    )
    """)

    # 17. DEBT PAYMENTS TABLE
    # service_portion tracks how much of a payment went toward services vs items
    cur.execute("""
    CREATE TABLE IF NOT EXISTS debt_payments (
        id                  SERIAL PRIMARY KEY,
        sale_id             INTEGER NOT NULL REFERENCES sales(id),
        amount_paid         NUMERIC(12,2) NOT NULL,
        payment_method_id   INTEGER REFERENCES payment_methods(id),
        reference_no        TEXT,
        notes               TEXT,
        paid_by             INTEGER REFERENCES users(id),
        paid_at             TIMESTAMP DEFAULT NOW(),
        service_portion     NUMERIC(12,2) DEFAULT 0
    )
    """)

    # 18. CASH ENTRIES (Petty Cash Ledger)
    # branch_id: DEFAULT 1 = main branch. When Branch 2 opens, entries will use that branch's ID.
    # reference_type: 'MANUAL' for staff entries, 'MECHANIC_PAYOUT' for auto-generated payouts
    # payout_for_date: the date the payout is for (used for mechanic payout reconciliation)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_entries (
        id              SERIAL PRIMARY KEY,
        branch_id       INTEGER NOT NULL DEFAULT 1,
        entry_type      TEXT CHECK(entry_type IN ('CASH_IN', 'CASH_OUT')) NOT NULL,
        amount          NUMERIC(12,2) NOT NULL,
        category        TEXT NOT NULL,
        description     TEXT,
        payout_for_date DATE,
        reference_type  TEXT NOT NULL DEFAULT 'MANUAL',
        reference_id    INTEGER,
        user_id         INTEGER REFERENCES users(id),
        created_at      TIMESTAMP DEFAULT NOW()
    )
    """)

    # --- SEEDING ---

    # 1. Seed Services (Only if empty)
    cur.execute("SELECT COUNT(*) FROM services")
    if cur.fetchone()['count'] == 0:
        initial_services = [
            ('Oil Change', 'Maintenance'),
            ('Tire Mounting', 'Labor'),
            ('Brake Cleaning', 'Maintenance'),
            ('Tune-up', 'Labor'),
            ('Chain Adjustment', 'Labor'),
            ('Engine Overhaul', 'Major Repair')
        ]
        cur.executemany("INSERT INTO services (name, category) VALUES (%s, %s)", initial_services)
        print("Services seeded successfully.")

    # 2. Seed Payment Methods (Only if empty)
    cur.execute("SELECT COUNT(*) FROM payment_methods")
    payment_data = [
        ('Cash', 'Cash'),
        ('GCash', 'Online'),
        ('PayMaya', 'Online'),
        ('Bank Transfer', 'Bank'),
        ('Others', 'Others'),
        ('BPI', 'Bank'),
        ('BDO', 'Bank'),
        ('Utang', 'Debt')
    ]

    if cur.fetchone()['count'] == 0:
        cur.executemany("INSERT INTO payment_methods (name, category) VALUES (%s, %s)", payment_data)
        print("Payment methods seeded successfully.")
    else:
        # If they already exist, keep categories in sync without burning IDs
        for name, cat in payment_data:
            cur.execute("UPDATE payment_methods SET category = %s WHERE name = %s", (cat, name))

    conn.commit()
    cur.close()
    conn.close()