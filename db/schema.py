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

    # 3. VENDORS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vendors (
        id                  SERIAL PRIMARY KEY,
        vendor_name         TEXT NOT NULL,
        address             TEXT,
        contact_person      TEXT,
        contact_no          TEXT,
        email               TEXT,
        is_active           INTEGER DEFAULT 1,
        created_at          TIMESTAMP DEFAULT NOW(),
        updated_at          TIMESTAMP DEFAULT NOW()
    )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_vendors_name_unique ON vendors ((LOWER(TRIM(vendor_name))))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vendors_active_name ON vendors (is_active, vendor_name)")

    # 4. ITEMS TABLE
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
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS vendor_id INTEGER REFERENCES vendors(id)")

    # 5. PAYMENT METHODS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_methods (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT NOT NULL,
        is_active   INTEGER DEFAULT 1
    )
    """)

    # 6. CUSTOMERS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS customers (
        id              SERIAL PRIMARY KEY,
        customer_no     TEXT NOT NULL UNIQUE,
        customer_name   TEXT NOT NULL,
        is_active       INTEGER DEFAULT 1,
        created_at      TIMESTAMP DEFAULT NOW()
    )
    """)

    # 7. VEHICLES TABLE
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

    # 8. SALES TABLE
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

    # 9. INVENTORY TRANSACTIONS
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

    # 10. SERVICES TABLE (The Master List of Labor Types)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS services (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT DEFAULT 'Labor',
        is_active   INTEGER DEFAULT 1
    )
    """)

    # 11. SALES SERVICES TABLE (The "Labor" Ledger)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_services (
        id          SERIAL PRIMARY KEY,
        sale_id     INTEGER NOT NULL REFERENCES sales(id),
        service_id  INTEGER NOT NULL REFERENCES services(id),
        price       NUMERIC(12,2) NOT NULL
    )
    """)

    # 12. SALES ITEMS TABLE (Item-level sales & discounts)
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_refunds (
        id                    SERIAL PRIMARY KEY,
        sale_id               INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        refund_number         TEXT NOT NULL UNIQUE,
        refund_amount         NUMERIC(12,2) NOT NULL DEFAULT 0,
        reason                TEXT NOT NULL,
        notes                 TEXT,
        refunded_by           INTEGER REFERENCES users(id),
        refunded_by_username  TEXT,
        refund_date           TIMESTAMP NOT NULL DEFAULT NOW(),
        created_at            TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_refund_items (
        id                  SERIAL PRIMARY KEY,
        refund_id           INTEGER NOT NULL REFERENCES sale_refunds(id) ON DELETE CASCADE,
        sale_item_id        INTEGER NOT NULL REFERENCES sales_items(id),
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity            INTEGER NOT NULL CHECK(quantity > 0),
        unit_price          NUMERIC(12,2) NOT NULL DEFAULT 0,
        line_total          NUMERIC(12,2) NOT NULL DEFAULT 0
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_refunds_sale_id ON sale_refunds(sale_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_refunds_refund_date ON sale_refunds(refund_date DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_refund_items_refund_id ON sale_refund_items(refund_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_refund_items_sale_item_id ON sale_refund_items(sale_item_id)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sale_exchanges (
        id                       SERIAL PRIMARY KEY,
        exchange_number          TEXT NOT NULL UNIQUE,
        original_sale_id         INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        refund_id                INTEGER NOT NULL REFERENCES sale_refunds(id) ON DELETE CASCADE,
        replacement_sale_id      INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        exchange_type            TEXT NOT NULL CHECK(exchange_type IN ('EVEN', 'CUSTOMER_TOPUP', 'SHOP_CASH_OUT')),
        refunded_amount          NUMERIC(12,2) NOT NULL DEFAULT 0,
        replacement_amount       NUMERIC(12,2) NOT NULL DEFAULT 0,
        net_adjustment_amount    NUMERIC(12,2) NOT NULL DEFAULT 0,
        reason                   TEXT NOT NULL,
        notes                    TEXT,
        exchanged_by             INTEGER REFERENCES users(id),
        exchanged_by_username    TEXT,
        exchanged_at             TIMESTAMP NOT NULL DEFAULT NOW(),
        created_at               TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_exchanges_original_sale_id ON sale_exchanges(original_sale_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_exchanges_replacement_sale_id ON sale_exchanges(replacement_sale_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_exchanges_refund_id ON sale_exchanges(refund_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sale_exchanges_exchanged_at ON sale_exchanges(exchanged_at DESC)")

    # 13. PURCHASE ORDERS (The Header)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        id              SERIAL PRIMARY KEY,
        po_number       TEXT UNIQUE,
        vendor_name     TEXT,
        status          TEXT CHECK(status IN ('FOR_APPROVAL', 'PENDING', 'PARTIAL', 'COMPLETED', 'CANCELLED')) DEFAULT 'FOR_APPROVAL',
        total_amount    NUMERIC(12,2) DEFAULT 0,
        created_at      TIMESTAMP DEFAULT NOW(),
        received_at     TIMESTAMP,
        created_by      INTEGER REFERENCES users(id),
        notes           TEXT
    )
    """)
    cur.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS vendor_id INTEGER REFERENCES vendors(id)")
    cur.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS vendor_address TEXT")
    cur.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS vendor_contact_person TEXT")
    cur.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS vendor_contact_no TEXT")
    cur.execute("ALTER TABLE purchase_orders ADD COLUMN IF NOT EXISTS vendor_email TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_vendor_id ON purchase_orders(vendor_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_po_number_lower ON purchase_orders(LOWER(po_number))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_status_created_at ON purchase_orders(status, created_at DESC)")
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE purchase_orders DROP CONSTRAINT IF EXISTS purchase_orders_status_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE purchase_orders
            ADD CONSTRAINT purchase_orders_status_check
            CHECK (status IN ('FOR_APPROVAL', 'PENDING', 'PARTIAL', 'COMPLETED', 'CANCELLED'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)

    # 14. PURCHASE ORDER ITEMS (The Details)
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS po_receipts (
        id                   SERIAL PRIMARY KEY,
        po_id                INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
        received_at          TIMESTAMP NOT NULL DEFAULT NOW(),
        received_by          INTEGER REFERENCES users(id),
        received_by_username TEXT,
        notes                TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS po_receipt_items (
        id                  SERIAL PRIMARY KEY,
        receipt_id          INTEGER NOT NULL REFERENCES po_receipts(id) ON DELETE CASCADE,
        po_id               INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity_received   INTEGER NOT NULL CHECK(quantity_received > 0),
        unit_cost           NUMERIC(12,2) NOT NULL DEFAULT 0,
        line_total          NUMERIC(12,2) NOT NULL DEFAULT 0,
        is_over_receive     INTEGER NOT NULL DEFAULT 0,
        notes               TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_po_receipts_po_id_received_at ON po_receipts(po_id, received_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_po_receipt_items_receipt_id ON po_receipt_items(receipt_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_po_receipt_items_po_id ON po_receipt_items(po_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_po_receipt_items_item_id ON po_receipt_items(item_id)")

    # Backfill vendor master data from legacy free-text fields.
    cur.execute("""
        INSERT INTO vendors (vendor_name)
        SELECT DISTINCT TRIM(src.vendor_name)
        FROM (
            SELECT vendor AS vendor_name FROM items
            UNION ALL
            SELECT vendor_name FROM purchase_orders
        ) src
        WHERE COALESCE(TRIM(src.vendor_name), '') <> ''
        ON CONFLICT ((LOWER(TRIM(vendor_name)))) DO NOTHING
    """)
    cur.execute("""
        UPDATE items i
        SET vendor_id = v.id
        FROM vendors v
        WHERE i.vendor_id IS NULL
          AND COALESCE(TRIM(i.vendor), '') <> ''
          AND LOWER(TRIM(i.vendor)) = LOWER(TRIM(v.vendor_name))
    """)
    cur.execute("""
        UPDATE purchase_orders po
        SET vendor_id = v.id
        FROM vendors v
        WHERE po.vendor_id IS NULL
          AND COALESCE(TRIM(po.vendor_name), '') <> ''
          AND LOWER(TRIM(po.vendor_name)) = LOWER(TRIM(v.vendor_name))
    """)

    # 15. LOYALTY PROGRAMS TABLE
    # program_type: 'SERVICE' = stamps earned per qualifying service visit
    #               'ITEM'    = stamps earned per qualifying item purchase
    #
    # qualifying_id: points to services.id (SERVICE type) or items.id (ITEM type)
    #   - enforced at app level; no composite FK at DB level
    #
    # reward_type options:
    #   NONE             → earn-only campaign, no direct redemption payload
    #   FREE_SERVICE     → reward_value = services.id of the free service
    #   FREE_ITEM        → reward_value = items.id of the free item
    #   DISCOUNT_PERCENT → reward_value = percent off (e.g. 10 = 10%)
    #   DISCOUNT_AMOUNT  → reward_value = flat peso off
    #   RAFFLE_ENTRY     → reward_value = number of raffle entries granted
    #
    # reward_basis options:
    #   STAMPS           → redemption based on stamp threshold
    #   POINTS           → redemption based on points threshold
    #   STAMPS_OR_POINTS → redemption allowed if either threshold is reached
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
        points_threshold    INTEGER NOT NULL DEFAULT 0,
        reward_basis        TEXT NOT NULL DEFAULT 'STAMPS' CHECK(reward_basis IN (
                                'STAMPS', 'POINTS', 'STAMPS_OR_POINTS'
                            )),
        program_mode        TEXT NOT NULL DEFAULT 'REDEEMABLE' CHECK(program_mode IN ('REDEEMABLE', 'EARN_ONLY')),
        reward_type         TEXT NOT NULL CHECK(reward_type IN (
                                'NONE',
                                'FREE_SERVICE', 'FREE_ITEM',
                                'DISCOUNT_PERCENT', 'DISCOUNT_AMOUNT',
                                'RAFFLE_ENTRY'
                            )),
        reward_value        NUMERIC(12,2) NOT NULL DEFAULT 0,
        reward_description  TEXT,
        period_start        DATE NOT NULL,
        period_end          DATE NOT NULL,
        branch_id           INTEGER DEFAULT NULL,
        stamp_enabled       INTEGER NOT NULL DEFAULT 1,
        points_enabled      INTEGER NOT NULL DEFAULT 0,
        is_active           INTEGER DEFAULT 1,
        created_at          TIMESTAMP DEFAULT NOW(),
        created_by          INTEGER REFERENCES users(id)
    )
    """)
    # Backward-compatible upgrades for existing databases.
    cur.execute("ALTER TABLE loyalty_programs ADD COLUMN IF NOT EXISTS stamp_enabled INTEGER NOT NULL DEFAULT 1")
    cur.execute("ALTER TABLE loyalty_programs ADD COLUMN IF NOT EXISTS points_enabled INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE loyalty_programs ADD COLUMN IF NOT EXISTS points_threshold INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE loyalty_programs ADD COLUMN IF NOT EXISTS reward_basis TEXT NOT NULL DEFAULT 'STAMPS'")
    cur.execute("ALTER TABLE loyalty_programs ADD COLUMN IF NOT EXISTS program_mode TEXT NOT NULL DEFAULT 'REDEEMABLE'")
    # Ensure reward_type constraint includes RAFFLE_ENTRY for existing DBs.
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE loyalty_programs DROP CONSTRAINT IF EXISTS loyalty_programs_reward_type_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE loyalty_programs
            ADD CONSTRAINT loyalty_programs_reward_type_check
            CHECK (reward_type IN (
                'NONE',
                'FREE_SERVICE', 'FREE_ITEM',
                'DISCOUNT_PERCENT', 'DISCOUNT_AMOUNT',
                'RAFFLE_ENTRY'
            ));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)
    # Ensure program_mode constraint exists for existing DBs.
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE loyalty_programs DROP CONSTRAINT IF EXISTS loyalty_programs_program_mode_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE loyalty_programs
            ADD CONSTRAINT loyalty_programs_program_mode_check
            CHECK (program_mode IN ('REDEEMABLE', 'EARN_ONLY'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)
    # Ensure reward_basis constraint exists for existing DBs.
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE loyalty_programs DROP CONSTRAINT IF EXISTS loyalty_programs_reward_basis_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE loyalty_programs
            ADD CONSTRAINT loyalty_programs_reward_basis_check
            CHECK (reward_basis IN ('STAMPS', 'POINTS', 'STAMPS_OR_POINTS'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)

    # 16. LOYALTY STAMPS TABLE
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

    # 17. LOYALTY REDEMPTIONS TABLE
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

    # 18. LOYALTY POINT RULES TABLE
    # Rules are evaluated in priority order for each sale.
    # stop_on_match = 1 means stop evaluating next rules in that program after a match.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loyalty_point_rules (
        id                      SERIAL PRIMARY KEY,
        program_id              INTEGER NOT NULL REFERENCES loyalty_programs(id) ON DELETE CASCADE,
        rule_name               TEXT,
        points                  INTEGER NOT NULL CHECK(points >= 0),
        service_id              INTEGER REFERENCES services(id),
        item_id                 INTEGER REFERENCES items(id),
        requires_any_item       INTEGER NOT NULL DEFAULT 0,
        requires_any_service    INTEGER NOT NULL DEFAULT 0,
        priority                INTEGER NOT NULL DEFAULT 100,
        stop_on_match           INTEGER NOT NULL DEFAULT 0,
        is_active               INTEGER NOT NULL DEFAULT 1,
        created_at              TIMESTAMP DEFAULT NOW()
    )
    """)

    # 19. LOYALTY POINT LEDGER TABLE
    # Immutable earning ledger for auditability and future recalculation.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS loyalty_point_ledger (
        id              SERIAL PRIMARY KEY,
        customer_id     INTEGER NOT NULL REFERENCES customers(id),
        program_id      INTEGER NOT NULL REFERENCES loyalty_programs(id),
        rule_id         INTEGER REFERENCES loyalty_point_rules(id),
        sale_id         INTEGER NOT NULL REFERENCES sales(id),
        redemption_id   INTEGER REFERENCES loyalty_redemptions(id),
        points          INTEGER NOT NULL CHECK(points >= 0),
        awarded_at      TIMESTAMP DEFAULT NOW(),
        note            TEXT,
        UNIQUE (customer_id, program_id, sale_id, rule_id)
    )
    """)
    cur.execute("ALTER TABLE loyalty_point_ledger ADD COLUMN IF NOT EXISTS redemption_id INTEGER REFERENCES loyalty_redemptions(id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lpl_customer ON loyalty_point_ledger(customer_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lpl_program ON loyalty_point_ledger(program_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lpl_sale ON loyalty_point_ledger(sale_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lpr_program_active ON loyalty_point_rules(program_id, is_active, priority)")

    # 20. DEBT PAYMENTS TABLE
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

    # 21. CASH ENTRIES (Petty Cash Ledger)
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

    # 22. PAYABLES TABLES
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payables (
        id                           SERIAL PRIMARY KEY,
        source_type                  TEXT NOT NULL CHECK(source_type IN ('PO_DELIVERY', 'MANUAL')),
        po_id                        INTEGER REFERENCES purchase_orders(id) ON DELETE SET NULL,
        po_receipt_id                INTEGER REFERENCES po_receipts(id) ON DELETE SET NULL,
        vendor_id                    INTEGER REFERENCES vendors(id) ON DELETE SET NULL,
        vendor_name_snapshot         TEXT,
        po_number_snapshot           TEXT,
        po_created_at_snapshot       TIMESTAMP,
        delivery_received_at_snapshot TIMESTAMP,
        payee_name                   TEXT NOT NULL,
        description                  TEXT,
        reference_no                 TEXT,
        amount_due                   NUMERIC(12,2) NOT NULL DEFAULT 0,
        status                       TEXT NOT NULL CHECK(status IN ('OPEN', 'PARTIAL', 'FULLY_ISSUED', 'CANCELLED')) DEFAULT 'OPEN',
        created_by                   INTEGER REFERENCES users(id),
        created_by_username          TEXT,
        created_at                   TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at                   TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payable_cheques (
        id                          SERIAL PRIMARY KEY,
        payable_id                  INTEGER NOT NULL REFERENCES payables(id) ON DELETE CASCADE,
        cheque_no                   TEXT NOT NULL,
        cheque_date                 DATE NOT NULL,
        due_date                    DATE NOT NULL,
        cheque_amount               NUMERIC(12,2) NOT NULL DEFAULT 0,
        status                      TEXT NOT NULL CHECK(status IN ('ISSUED', 'CLEARED', 'CANCELLED', 'BOUNCED')) DEFAULT 'ISSUED',
        notes                       TEXT,
        reminded_due_minus_7        INTEGER NOT NULL DEFAULT 0,
        reminded_due_today          INTEGER NOT NULL DEFAULT 0,
        created_by                  INTEGER REFERENCES users(id),
        created_by_username         TEXT,
        created_at                  TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at                  TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_source_type ON payables(source_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_status ON payables(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_po_id ON payables(po_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_po_receipt_id ON payables(po_receipt_id)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payables_po_receipt_unique ON payables(po_receipt_id) WHERE po_receipt_id IS NOT NULL")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payable_cheques_payable_id ON payable_cheques(payable_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payable_cheques_due_date ON payable_cheques(due_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payable_cheques_status ON payable_cheques(status)")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payable_cheques_cheque_no_unique ON payable_cheques(cheque_no)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payables_audit_log (
        id                   SERIAL PRIMARY KEY,
        payable_id           INTEGER REFERENCES payables(id) ON DELETE SET NULL,
        cheque_id            INTEGER REFERENCES payable_cheques(id) ON DELETE SET NULL,
        event_type           TEXT NOT NULL,
        source_type          TEXT,
        po_id                INTEGER REFERENCES purchase_orders(id) ON DELETE SET NULL,
        po_receipt_id        INTEGER REFERENCES po_receipts(id) ON DELETE SET NULL,
        po_number_snapshot   TEXT,
        payee_name_snapshot  TEXT,
        cheque_no_snapshot   TEXT,
        amount_snapshot      NUMERIC(12,2),
        old_status           TEXT,
        new_status           TEXT,
        notes                TEXT,
        created_by           INTEGER REFERENCES users(id),
        created_by_username  TEXT,
        created_at           TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_audit_created_at ON payables_audit_log(created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_audit_event_type ON payables_audit_log(event_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_audit_source_type ON payables_audit_log(source_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_audit_payable_id ON payables_audit_log(payable_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_payables_audit_cheque_id ON payables_audit_log(cheque_id)")

    # 23. NOTIFICATIONS TABLE
    # One row per recipient user. This keeps unread/read state independent
    # even when the same business event is visible to multiple admins.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id                  SERIAL PRIMARY KEY,
        recipient_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        notification_type   TEXT NOT NULL,
        category            TEXT NOT NULL DEFAULT 'general',
        title               TEXT NOT NULL,
        message             TEXT NOT NULL,
        entity_type         TEXT,
        entity_id           INTEGER,
        action_url          TEXT,
        is_read             INTEGER NOT NULL DEFAULT 0,
        read_at             TIMESTAMP,
        is_archived         INTEGER NOT NULL DEFAULT 0,
        created_at          TIMESTAMP DEFAULT NOW(),
        created_by          INTEGER REFERENCES users(id),
        metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient_created ON notifications(recipient_user_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient_unread ON notifications(recipient_user_id, is_archived, is_read, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_entity ON notifications(entity_type, entity_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_type ON notifications(notification_type)")

    # 24. APPROVAL REQUESTS TABLE
    # Generic approval workflow table reusable by multiple business modules.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS approval_requests (
        id                  SERIAL PRIMARY KEY,
        approval_type       TEXT NOT NULL,
        entity_type         TEXT NOT NULL,
        entity_id           INTEGER NOT NULL,
        status              TEXT NOT NULL CHECK(status IN (
                                'PENDING',
                                'REVISIONS_NEEDED',
                                'APPROVED',
                                'CANCELLED'
                            )),
        requested_by        INTEGER NOT NULL REFERENCES users(id),
        requested_at        TIMESTAMP DEFAULT NOW(),
        last_submitted_at   TIMESTAMP DEFAULT NOW(),
        decision_by         INTEGER REFERENCES users(id),
        decision_at         TIMESTAMP,
        decision_notes      TEXT,
        is_locked           INTEGER NOT NULL DEFAULT 0,
        current_revision_no INTEGER NOT NULL DEFAULT 0,
        metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
        UNIQUE (approval_type, entity_type, entity_id)
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_requests_status ON approval_requests(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_requests_type ON approval_requests(approval_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_requests_requester ON approval_requests(requested_by)")

    # 24. APPROVAL ACTIONS TABLE
    # Immutable history of workflow actions for auditability.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS approval_actions (
        id                  SERIAL PRIMARY KEY,
        approval_request_id INTEGER NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
        action_type         TEXT NOT NULL CHECK(action_type IN (
                                'SUBMITTED',
                                'AUTO_APPROVED',
                                'APPROVED',
                                'REVISIONS_REQUESTED',
                                'RESUBMITTED',
                                'EDITED_AFTER_APPROVAL',
                                'REOPENED_AFTER_EDIT',
                                'CANCELLED_BY_REQUESTER',
                                'CANCELLED_BY_ADMIN'
                            )),
        from_status         TEXT,
        to_status           TEXT,
        action_by           INTEGER REFERENCES users(id),
        action_at           TIMESTAMP DEFAULT NOW(),
        notes               TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_actions_request ON approval_actions(approval_request_id, action_at DESC)")
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE approval_actions DROP CONSTRAINT IF EXISTS approval_actions_action_type_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE approval_actions
            ADD CONSTRAINT approval_actions_action_type_check
            CHECK (action_type IN (
                'SUBMITTED',
                'AUTO_APPROVED',
                'APPROVED',
                'REVISIONS_REQUESTED',
                'RESUBMITTED',
                'EDITED_AFTER_APPROVAL',
                'REOPENED_AFTER_EDIT',
                'CANCELLED_BY_REQUESTER',
                'CANCELLED_BY_ADMIN'
            ));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)

    # 25. APPROVAL REVISION ITEMS
    # Structured per-item revision requests tied to a specific approval action.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS approval_revision_items (
        id                  SERIAL PRIMARY KEY,
        approval_request_id INTEGER NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
        approval_action_id  INTEGER NOT NULL REFERENCES approval_actions(id) ON DELETE CASCADE,
        item_id             INTEGER REFERENCES items(id),
        item_name           TEXT NOT NULL,
        quantity_ordered    INTEGER,
        quantity_received   INTEGER DEFAULT 0,
        revision_note       TEXT NOT NULL,
        created_at          TIMESTAMP DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_revision_items_request ON approval_revision_items(approval_request_id, approval_action_id)")

    # 26. APPROVAL RESUBMISSION CHANGES
    # Structured before/after diff captured whenever a requester resubmits.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS approval_resubmission_changes (
        id                  SERIAL PRIMARY KEY,
        approval_request_id INTEGER NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
        approval_action_id  INTEGER NOT NULL REFERENCES approval_actions(id) ON DELETE CASCADE,
        change_scope        TEXT NOT NULL CHECK(change_scope IN ('HEADER', 'ITEM')),
        item_id             INTEGER REFERENCES items(id),
        item_name           TEXT,
        field_name          TEXT NOT NULL,
        before_value        TEXT,
        after_value         TEXT,
        change_label        TEXT NOT NULL,
        created_at          TIMESTAMP DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_approval_resubmission_changes_request ON approval_resubmission_changes(approval_request_id, approval_action_id)")

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
