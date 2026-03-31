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
        phone_no        TEXT,
        role            TEXT CHECK(role IN ('admin', 'staff')) NOT NULL,
        is_active       INTEGER DEFAULT 1,
        created_at      TIMESTAMP DEFAULT NOW(),
        created_by      INTEGER REFERENCES users(id)
    )
    """)
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_no TEXT")
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 0")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS login_attempts (
        id                  SERIAL PRIMARY KEY,
        username_normalized TEXT NOT NULL,
        ip_address          TEXT NOT NULL,
        attempted_at        TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup
    ON login_attempts (username_normalized, ip_address, attempted_at DESC)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_login_attempts_attempted_at
    ON login_attempts (attempted_at DESC)
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS mechanic_quota_topup_overrides (
        id                    SERIAL PRIMARY KEY,
        mechanic_id           INTEGER NOT NULL REFERENCES mechanics(id) ON DELETE CASCADE,
        quota_date            DATE NOT NULL,
        applies_quota_topup   INTEGER NOT NULL DEFAULT 1,
        created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_mechanic_quota_topup_override_unique
    ON mechanic_quota_topup_overrides (mechanic_id, quota_date)
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mechanic_quota_topup_override_date ON mechanic_quota_topup_overrides(quota_date DESC, mechanic_id)")

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
        markup              NUMERIC(12,4),
        reorder_level       INTEGER DEFAULT 0,
        vendor              TEXT,
        mechanic            TEXT
    )
    """)
    cur.execute("ALTER TABLE items ALTER COLUMN markup TYPE NUMERIC(12,4)")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS vendor_id INTEGER REFERENCES vendors(id)")
    cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_items_name_unique_normalized ON items ((LOWER(TRIM(name))))")
    cur.execute("""
        UPDATE items
        SET markup = CASE
            WHEN COALESCE(cost_per_piece, 0) > 0 AND COALESCE(a4s_selling_price, 0) > 0
            THEN ROUND((a4s_selling_price - cost_per_piece) / cost_per_piece, 4)
            ELSE 0
        END
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS item_edit_history (
        id                  SERIAL PRIMARY KEY,
        item_id             INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        changed_at          TIMESTAMP NOT NULL DEFAULT NOW(),
        changed_by          INTEGER REFERENCES users(id),
        changed_by_username TEXT,
        change_reason       TEXT NOT NULL,
        before_payload      JSONB NOT NULL,
        after_payload       JSONB NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_item_edit_history_item_id ON item_edit_history(item_id, changed_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_item_edit_history_changed_at ON item_edit_history(changed_at DESC)")

    # 5. PAYMENT METHODS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS payment_methods (
        id          SERIAL PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT NOT NULL,
        is_active   INTEGER DEFAULT 1
    )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_methods_name_unique_normalized ON payment_methods ((LOWER(TRIM(name))))")

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
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_customer_no_unique_normalized ON customers ((LOWER(TRIM(customer_no))))")

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
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_vehicles_customer_vehicle_unique_active
    ON vehicles (customer_id, (LOWER(TRIM(vehicle_name))))
    WHERE is_active = 1
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
        transaction_class   TEXT NOT NULL DEFAULT 'NEW_SALE'
                            CHECK(transaction_class IN ('QUICK_SALE', 'NEW_SALE', 'MECHANIC_SUPPLY')),
        service_fee         NUMERIC(12,2) DEFAULT 0,
        paid_at             TIMESTAMP
    )
    """)
    cur.execute("""
    ALTER TABLE sales
    ADD COLUMN IF NOT EXISTS transaction_class TEXT NOT NULL DEFAULT 'NEW_SALE'
    """)
    cur.execute("""
    UPDATE sales
    SET transaction_class = 'NEW_SALE'
    WHERE transaction_class IS NULL
    """)

    # 9. INVENTORY TRANSACTIONS
    # reference_id replaces sale_id (The "Universal Key")
    # reference_type tells us if reference_id points to a Sale, PO, or Swap
    # change_reason is machine-readable code (PO_ARRIVAL, PARTIAL_ARRIVAL, etc.)
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
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_services_name_unique_normalized ON services ((LOWER(TRIM(name))))")

    # 10b. BUNDLES TABLES (Admin-maintained bundle master)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bundles (
        id                  SERIAL PRIMARY KEY,
        name                TEXT NOT NULL,
        vehicle_category    TEXT NOT NULL,
        is_active           INTEGER DEFAULT 1,
        created_at          TIMESTAMP DEFAULT NOW()
    )
    """)
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_bundles_name_vehicle_unique
    ON bundles ((LOWER(TRIM(name))), (LOWER(TRIM(vehicle_category))))
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bundle_versions (
        id                  SERIAL PRIMARY KEY,
        bundle_id           INTEGER NOT NULL REFERENCES bundles(id) ON DELETE CASCADE,
        version_no          INTEGER NOT NULL,
        is_current          INTEGER NOT NULL DEFAULT 1,
        change_notes        TEXT,
        created_at          TIMESTAMP DEFAULT NOW(),
        created_by          INTEGER REFERENCES users(id),
        created_by_username TEXT
    )
    """)
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_versions_unique_version
    ON bundle_versions (bundle_id, version_no)
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bundle_versions_bundle_current ON bundle_versions(bundle_id, is_current, version_no DESC)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bundle_version_variants (
        id                  SERIAL PRIMARY KEY,
        bundle_version_id   INTEGER NOT NULL REFERENCES bundle_versions(id) ON DELETE CASCADE,
        subcategory_name    TEXT NOT NULL,
        item_value_reference NUMERIC(12,2) NOT NULL DEFAULT 0,
        shop_share          NUMERIC(12,2) NOT NULL DEFAULT 0,
        mechanic_share      NUMERIC(12,2) NOT NULL DEFAULT 0,
        sale_price          NUMERIC(12,2) NOT NULL DEFAULT 0,
        sort_order          INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("ALTER TABLE bundle_version_variants ADD COLUMN IF NOT EXISTS item_value_reference NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE bundle_version_variants ADD COLUMN IF NOT EXISTS shop_share NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE bundle_version_variants ADD COLUMN IF NOT EXISTS mechanic_share NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_version_variants_unique_name
    ON bundle_version_variants (bundle_version_id, (LOWER(TRIM(subcategory_name))))
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bundle_version_variants_sort ON bundle_version_variants(bundle_version_id, sort_order ASC, id ASC)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bundle_version_services (
        id                  SERIAL PRIMARY KEY,
        bundle_version_id   INTEGER NOT NULL REFERENCES bundle_versions(id) ON DELETE CASCADE,
        service_id          INTEGER NOT NULL REFERENCES services(id),
        sort_order          INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_version_services_unique ON bundle_version_services(bundle_version_id, service_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bundle_version_services_sort ON bundle_version_services(bundle_version_id, sort_order ASC, id ASC)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bundle_version_items (
        id                  SERIAL PRIMARY KEY,
        bundle_version_id   INTEGER NOT NULL REFERENCES bundle_versions(id) ON DELETE CASCADE,
        item_id             INTEGER NOT NULL REFERENCES items(id),
        quantity            INTEGER NOT NULL DEFAULT 1,
        sort_order          INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_version_items_unique ON bundle_version_items(bundle_version_id, item_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bundle_version_items_sort ON bundle_version_items(bundle_version_id, sort_order ASC, id ASC)")

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
        cost_per_piece_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0,
        discounted_by       INTEGER REFERENCES users(id),
        created_at          TIMESTAMP DEFAULT NOW()
    )
    """)
    cur.execute("ALTER TABLE sales_items ADD COLUMN IF NOT EXISTS cost_per_piece_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("""
    UPDATE sales_items si
    SET cost_per_piece_snapshot = COALESCE(i.cost_per_piece, 0)
    FROM items i
    WHERE i.id = si.item_id
      AND COALESCE(si.cost_per_piece_snapshot, 0) = 0
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_bundles (
        id                          SERIAL PRIMARY KEY,
        sale_id                     INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        bundle_id                   INTEGER REFERENCES bundles(id),
        bundle_version_id           INTEGER REFERENCES bundle_versions(id),
        bundle_variant_id           INTEGER REFERENCES bundle_version_variants(id),
        bundle_name_snapshot        TEXT NOT NULL,
        vehicle_category_snapshot   TEXT,
        bundle_version_no_snapshot  INTEGER,
        subcategory_name_snapshot   TEXT NOT NULL,
        item_value_reference_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0,
        shop_share_snapshot         NUMERIC(12,2) NOT NULL DEFAULT 0,
        mechanic_share_snapshot     NUMERIC(12,2) NOT NULL DEFAULT 0,
        bundle_price_snapshot       NUMERIC(12,2) NOT NULL DEFAULT 0,
        created_at                  TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_bundles_sale_id ON sales_bundles(sale_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_bundles_bundle_id ON sales_bundles(bundle_id)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_bundle_services (
        id                      SERIAL PRIMARY KEY,
        sales_bundle_id         INTEGER NOT NULL REFERENCES sales_bundles(id) ON DELETE CASCADE,
        service_id              INTEGER REFERENCES services(id),
        service_name_snapshot   TEXT NOT NULL,
        sort_order              INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_bundle_services_bundle_id ON sales_bundle_services(sales_bundle_id)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales_bundle_items (
        id                      SERIAL PRIMARY KEY,
        sales_bundle_id         INTEGER NOT NULL REFERENCES sales_bundles(id) ON DELETE CASCADE,
        item_id                 INTEGER REFERENCES items(id),
        item_name_snapshot      TEXT NOT NULL,
        quantity                INTEGER NOT NULL DEFAULT 1,
        cost_per_piece_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0,
        selling_price_snapshot  NUMERIC(12,2) NOT NULL DEFAULT 0,
        line_total_snapshot     NUMERIC(12,2) NOT NULL DEFAULT 0,
        is_included             INTEGER NOT NULL DEFAULT 1,
        sort_order              INTEGER NOT NULL DEFAULT 0
    )
    """)
    cur.execute("ALTER TABLE sales_bundle_items ADD COLUMN IF NOT EXISTS cost_per_piece_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE sales_bundle_items ADD COLUMN IF NOT EXISTS selling_price_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE sales_bundle_items ADD COLUMN IF NOT EXISTS line_total_snapshot NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("""
    UPDATE sales_bundle_items sbi
    SET cost_per_piece_snapshot = COALESCE(i.cost_per_piece, 0)
    FROM items i
    WHERE i.id = sbi.item_id
      AND COALESCE(sbi.cost_per_piece_snapshot, 0) = 0
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_bundle_items_bundle_id ON sales_bundle_items(sales_bundle_id)")
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
        unit_cost           NUMERIC(12,2),
        purchase_mode       TEXT NOT NULL DEFAULT 'PIECE'
    )
    """)
    cur.execute("ALTER TABLE po_items ADD COLUMN IF NOT EXISTS purchase_mode TEXT NOT NULL DEFAULT 'PIECE'")
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE po_items DROP CONSTRAINT IF EXISTS po_items_purchase_mode_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE po_items
            ADD CONSTRAINT po_items_purchase_mode_check
            CHECK (purchase_mode IN ('PIECE', 'BOX'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
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
        purchase_mode       TEXT NOT NULL DEFAULT 'PIECE',
        stock_quantity_received INTEGER NOT NULL DEFAULT 0,
        effective_piece_cost NUMERIC(12,2) NOT NULL DEFAULT 0,
        notes               TEXT
    )
    """)
    cur.execute("ALTER TABLE po_receipt_items ADD COLUMN IF NOT EXISTS purchase_mode TEXT NOT NULL DEFAULT 'PIECE'")
    cur.execute("ALTER TABLE po_receipt_items ADD COLUMN IF NOT EXISTS stock_quantity_received INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE po_receipt_items ADD COLUMN IF NOT EXISTS effective_piece_cost NUMERIC(12,2) NOT NULL DEFAULT 0")
    cur.execute("""
    UPDATE po_receipt_items
    SET stock_quantity_received = quantity_received
    WHERE stock_quantity_received = 0
    """)
    cur.execute("""
    UPDATE po_receipt_items
    SET effective_piece_cost = unit_cost
    WHERE effective_piece_cost = 0
    """)
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE po_receipt_items DROP CONSTRAINT IF EXISTS po_receipt_items_purchase_mode_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE po_receipt_items
            ADD CONSTRAINT po_receipt_items_purchase_mode_check
            CHECK (purchase_mode IN ('PIECE', 'BOX'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
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
        created_at      TIMESTAMP DEFAULT NOW(),
        is_deleted      BOOLEAN NOT NULL DEFAULT FALSE,
        deleted_at      TIMESTAMP,
        deleted_by      INTEGER REFERENCES users(id)
    )
    """)
    cur.execute("ALTER TABLE cash_entries ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE")
    cur.execute("ALTER TABLE cash_entries ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
    cur.execute("ALTER TABLE cash_entries ADD COLUMN IF NOT EXISTS deleted_by INTEGER REFERENCES users(id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cash_entries_branch_created ON cash_entries(branch_id, created_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cash_entries_branch_deleted ON cash_entries(branch_id, is_deleted, deleted_at DESC)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_float_claims (
        id              SERIAL PRIMARY KEY,
        sale_id         INTEGER NOT NULL UNIQUE REFERENCES sales(id) ON DELETE CASCADE,
        cash_entry_id   INTEGER NOT NULL REFERENCES cash_entries(id) ON DELETE CASCADE,
        created_at      TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cash_float_claims_entry ON cash_float_claims(cash_entry_id)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cash_debt_payment_claims (
        id                  SERIAL PRIMARY KEY,
        debt_payment_id     INTEGER NOT NULL UNIQUE REFERENCES debt_payments(id) ON DELETE CASCADE,
        cash_entry_id       INTEGER NOT NULL REFERENCES cash_entries(id) ON DELETE CASCADE,
        created_at          TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cash_debt_payment_claims_entry ON cash_debt_payment_claims(cash_entry_id)")

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
    UPDATE payable_cheques
    SET due_date = cheque_date
    WHERE due_date IS DISTINCT FROM cheque_date
    """)
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
    cur.execute("""
    CREATE TEMP TABLE legacy_po_cleanup_batches AS
    SELECT DISTINCT
        reference_id AS po_id,
        transaction_date
    FROM inventory_transactions
    WHERE reference_type = 'PURCHASE_ORDER'
      AND transaction_type = 'IN'
      AND change_reason = 'BONUS_STOCK'
    """)
    cur.execute("""
    CREATE TEMP TABLE legacy_po_cleanup_receipts AS
    SELECT DISTINCT pr.id, pr.po_id, pr.received_at
    FROM po_receipts pr
    JOIN legacy_po_cleanup_batches b
      ON b.po_id = pr.po_id
     AND b.transaction_date = pr.received_at
    """)
    cur.execute("""
    CREATE TEMP TABLE legacy_po_cleanup_payables AS
    SELECT DISTINCT p.id
    FROM payables p
    JOIN legacy_po_cleanup_receipts r ON r.id = p.po_receipt_id
    """)
    cur.execute("""
    DELETE FROM payables_audit_log
    WHERE payable_id IN (SELECT id FROM legacy_po_cleanup_payables)
       OR po_receipt_id IN (SELECT id FROM legacy_po_cleanup_receipts)
    """)
    cur.execute("""
    DELETE FROM payables
    WHERE id IN (SELECT id FROM legacy_po_cleanup_payables)
    """)
    cur.execute("""
    DELETE FROM inventory_transactions t
    USING legacy_po_cleanup_batches b
    WHERE t.reference_type = 'PURCHASE_ORDER'
      AND t.reference_id = b.po_id
      AND t.transaction_type = 'IN'
      AND t.transaction_date = b.transaction_date
    """)
    cur.execute("""
    DELETE FROM po_receipts
    WHERE id IN (SELECT id FROM legacy_po_cleanup_receipts)
    """)
    cur.execute("""
    UPDATE po_items pi
    SET quantity_received = COALESCE(src.total_received, 0)
    FROM (
        SELECT
            pi2.id AS po_item_id,
            COALESCE(SUM(pri.quantity_received), 0) AS total_received
        FROM po_items pi2
        LEFT JOIN po_receipt_items pri
          ON pri.po_id = pi2.po_id
         AND pri.item_id = pi2.item_id
        WHERE pi2.po_id IN (SELECT DISTINCT po_id FROM legacy_po_cleanup_batches)
        GROUP BY pi2.id
    ) src
    WHERE pi.id = src.po_item_id
    """)
    cur.execute("""
    UPDATE purchase_orders po
    SET received_at = src.latest_received_at,
        status = CASE
            WHEN src.receipt_count = 0 THEN 'PENDING'
            WHEN src.completed_item_count = src.total_item_count AND src.total_item_count > 0 THEN 'COMPLETED'
            ELSE 'PARTIAL'
        END
    FROM (
        SELECT
            po2.id AS po_id,
            MAX(pr.received_at) AS latest_received_at,
            COUNT(DISTINCT pr.id) AS receipt_count,
            COUNT(pi.id) AS total_item_count,
            COUNT(pi.id) FILTER (WHERE pi.quantity_received >= pi.quantity_ordered) AS completed_item_count
        FROM purchase_orders po2
        LEFT JOIN po_receipts pr ON pr.po_id = po2.id
        LEFT JOIN po_items pi ON pi.po_id = po2.id
        WHERE po2.id IN (SELECT DISTINCT po_id FROM legacy_po_cleanup_batches)
        GROUP BY po2.id
    ) src
    WHERE po.id = src.po_id
    """)
    cur.execute("DROP TABLE IF EXISTS legacy_po_cleanup_payables")
    cur.execute("DROP TABLE IF EXISTS legacy_po_cleanup_receipts")
    cur.execute("DROP TABLE IF EXISTS legacy_po_cleanup_batches")
    cur.execute("ALTER TABLE po_receipt_items DROP COLUMN IF EXISTS is_over_receive")

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

    # 24. PASSWORD RESET REQUESTS TABLE
    cur.execute("""
    CREATE TABLE IF NOT EXISTS password_reset_requests (
        id                  SERIAL PRIMARY KEY,
        username_submitted  TEXT NOT NULL,
        user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
        status              TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'COMPLETED', 'REJECTED', 'CANCELLED')),
        request_note        TEXT,
        requested_by_ip     TEXT,
        requested_at        TIMESTAMP NOT NULL DEFAULT NOW(),
        repeat_request_count INTEGER NOT NULL DEFAULT 0,
        last_requested_at   TIMESTAMP,
        handled_by          INTEGER REFERENCES users(id),
        handled_at          TIMESTAMP,
        admin_note          TEXT
    )
    """)
    cur.execute("ALTER TABLE password_reset_requests ADD COLUMN IF NOT EXISTS repeat_request_count INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE password_reset_requests ADD COLUMN IF NOT EXISTS last_requested_at TIMESTAMP")
    cur.execute("""
        UPDATE password_reset_requests
        SET last_requested_at = COALESCE(last_requested_at, requested_at)
        WHERE last_requested_at IS NULL
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_requests_status_requested ON password_reset_requests(status, requested_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_requests_user_status ON password_reset_requests(user_id, status, requested_at DESC)")

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

    # 27. STOCKTAKE SESSIONS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stocktake_sessions (
        id                    SERIAL PRIMARY KEY,
        session_number        TEXT NOT NULL UNIQUE,
        status                TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'CONFIRMED', 'CANCELLED')),
        count_scope           TEXT NOT NULL DEFAULT 'PARTIAL',
        notes                 TEXT,
        item_count            INTEGER NOT NULL DEFAULT 0,
        variance_item_count   INTEGER NOT NULL DEFAULT 0,
        created_by            INTEGER REFERENCES users(id),
        created_by_username   TEXT,
        created_at            TIMESTAMP NOT NULL DEFAULT NOW(),
        confirmed_by          INTEGER REFERENCES users(id),
        confirmed_by_username TEXT,
        confirmed_at          TIMESTAMP,
        cancelled_by          INTEGER REFERENCES users(id),
        cancelled_by_username TEXT,
        cancelled_at          TIMESTAMP
    )
    """)
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS session_number TEXT")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'DRAFT'")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS count_scope TEXT NOT NULL DEFAULT 'PARTIAL'")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS notes TEXT")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS item_count INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS variance_item_count INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS created_by INTEGER REFERENCES users(id)")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS created_by_username TEXT")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS confirmed_by INTEGER REFERENCES users(id)")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS confirmed_by_username TEXT")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS cancelled_by INTEGER REFERENCES users(id)")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS cancelled_by_username TEXT")
    cur.execute("ALTER TABLE stocktake_sessions ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP")
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE stocktake_sessions DROP CONSTRAINT IF EXISTS stocktake_sessions_status_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE stocktake_sessions
            ADD CONSTRAINT stocktake_sessions_status_check
            CHECK (status IN ('DRAFT', 'CONFIRMED', 'CANCELLED'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_stocktake_sessions_number_unique ON stocktake_sessions(LOWER(session_number))")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_sessions_status_created ON stocktake_sessions(status, created_at DESC)")

    # 28. STOCKTAKE ITEMS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stocktake_items (
        id                     SERIAL PRIMARY KEY,
        session_id             INTEGER NOT NULL REFERENCES stocktake_sessions(id) ON DELETE CASCADE,
        item_id                INTEGER NOT NULL REFERENCES items(id),
        system_stock           INTEGER NOT NULL DEFAULT 0,
        active_system_stock    INTEGER NOT NULL DEFAULT 0,
        counted_stock          INTEGER,
        variance               INTEGER NOT NULL DEFAULT 0,
        baseline_mode          TEXT NOT NULL DEFAULT 'CAPTURED',
        baseline_refreshed_at  TIMESTAMP,
        baseline_refreshed_by  INTEGER,
        baseline_refreshed_by_username TEXT,
        adjustment_type        TEXT CHECK(adjustment_type IN ('IN', 'OUT')),
        adjustment_quantity    INTEGER NOT NULL DEFAULT 0,
        is_applied             INTEGER NOT NULL DEFAULT 0,
        applied_transaction_id INTEGER,
        notes                  TEXT,
        created_at             TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at             TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE (session_id, item_id)
    )
    """)
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS system_stock INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS active_system_stock INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS counted_stock INTEGER")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS variance INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS baseline_mode TEXT NOT NULL DEFAULT 'CAPTURED'")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS baseline_refreshed_at TIMESTAMP")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS baseline_refreshed_by INTEGER")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS baseline_refreshed_by_username TEXT")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS adjustment_type TEXT")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS adjustment_quantity INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS is_applied INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS applied_transaction_id INTEGER")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS notes TEXT")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()")
    cur.execute("ALTER TABLE stocktake_items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW()")
    cur.execute("""
        UPDATE stocktake_items
        SET active_system_stock = system_stock
        WHERE COALESCE(active_system_stock, 0) = 0
    """)
    cur.execute("""
        UPDATE stocktake_items
        SET baseline_mode = 'REFRESHED'
        WHERE COALESCE(active_system_stock, 0) <> COALESCE(system_stock, 0)
          AND COALESCE(baseline_mode, 'CAPTURED') = 'CAPTURED'
    """)
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE stocktake_items DROP CONSTRAINT IF EXISTS stocktake_items_adjustment_type_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE stocktake_items DROP CONSTRAINT IF EXISTS stocktake_items_baseline_mode_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE stocktake_items
            ADD CONSTRAINT stocktake_items_adjustment_type_check
            CHECK (adjustment_type IN ('IN', 'OUT') OR adjustment_type IS NULL);
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE stocktake_items
            ADD CONSTRAINT stocktake_items_baseline_mode_check
            CHECK (baseline_mode IN ('CAPTURED', 'REFRESHED'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_items_session ON stocktake_items(session_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_items_item ON stocktake_items(item_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS stocktake_item_baseline_history (
        id                         SERIAL PRIMARY KEY,
        stocktake_item_id          INTEGER NOT NULL REFERENCES stocktake_items(id) ON DELETE CASCADE,
        event_type                 TEXT NOT NULL,
        baseline_stock             INTEGER NOT NULL DEFAULT 0,
        previous_active_stock      INTEGER,
        live_stock                 INTEGER,
        counted_stock_snapshot     INTEGER,
        variance_snapshot          INTEGER NOT NULL DEFAULT 0,
        actor_user_id              INTEGER,
        actor_username             TEXT,
        created_at                 TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """)
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS stocktake_item_id INTEGER")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS event_type TEXT")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS baseline_stock INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS previous_active_stock INTEGER")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS live_stock INTEGER")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS counted_stock_snapshot INTEGER")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS variance_snapshot INTEGER NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS actor_user_id INTEGER")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS actor_username TEXT")
    cur.execute("ALTER TABLE stocktake_item_baseline_history ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW()")
    cur.execute("""
    DO $$
    BEGIN
        BEGIN
            ALTER TABLE stocktake_item_baseline_history DROP CONSTRAINT IF EXISTS stocktake_item_baseline_history_event_type_check;
        EXCEPTION WHEN undefined_table THEN
            NULL;
        END;

        BEGIN
            ALTER TABLE stocktake_item_baseline_history
            ADD CONSTRAINT stocktake_item_baseline_history_event_type_check
            CHECK (event_type IN ('CAPTURED', 'REFRESH'));
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END;
    END $$;
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_item_baseline_history_item ON stocktake_item_baseline_history(stocktake_item_id, created_at ASC)")
    cur.execute("""
        INSERT INTO stocktake_item_baseline_history (
            stocktake_item_id,
            event_type,
            baseline_stock,
            previous_active_stock,
            live_stock,
            counted_stock_snapshot,
            variance_snapshot,
            actor_user_id,
            actor_username,
            created_at
        )
        SELECT
            si.id,
            'CAPTURED',
            si.system_stock,
            NULL,
            si.system_stock,
            si.counted_stock,
            CASE
                WHEN si.counted_stock IS NULL THEN 0
                ELSE si.counted_stock - si.system_stock
            END,
            ss.created_by,
            ss.created_by_username,
            COALESCE(si.created_at, ss.created_at, NOW())
        FROM stocktake_items si
        JOIN stocktake_sessions ss ON ss.id = si.session_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM stocktake_item_baseline_history h
            WHERE h.stocktake_item_id = si.id
              AND h.event_type = 'CAPTURED'
        )
    """)
    cur.execute("""
        INSERT INTO stocktake_item_baseline_history (
            stocktake_item_id,
            event_type,
            baseline_stock,
            previous_active_stock,
            live_stock,
            counted_stock_snapshot,
            variance_snapshot,
            actor_user_id,
            actor_username,
            created_at
        )
        SELECT
            si.id,
            'REFRESH',
            si.active_system_stock,
            si.system_stock,
            si.active_system_stock,
            si.counted_stock,
            si.variance,
            si.baseline_refreshed_by,
            si.baseline_refreshed_by_username,
            COALESCE(si.baseline_refreshed_at, si.updated_at, NOW())
        FROM stocktake_items si
        WHERE COALESCE(si.active_system_stock, 0) <> COALESCE(si.system_stock, 0)
          AND NOT EXISTS (
            SELECT 1
            FROM stocktake_item_baseline_history h
            WHERE h.stocktake_item_id = si.id
              AND h.event_type = 'REFRESH'
          )
    """)

    # 29. STOCKTAKE ACCESS GRANTS
    cur.execute("""
    CREATE TABLE IF NOT EXISTS stocktake_access_grants (
        id                  SERIAL PRIMARY KEY,
        approval_request_id INTEGER NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
        user_id             INTEGER NOT NULL REFERENCES users(id),
        granted_by          INTEGER NOT NULL REFERENCES users(id),
        granted_at          TIMESTAMP NOT NULL DEFAULT NOW(),
        expires_at          TIMESTAMP NOT NULL,
        grant_notes         TEXT,
        revoked_at          TIMESTAMP,
        revoked_by          INTEGER REFERENCES users(id),
        revoke_notes        TEXT
    )
    """)
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS approval_request_id INTEGER")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS user_id INTEGER")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS granted_by INTEGER")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS granted_at TIMESTAMP NOT NULL DEFAULT NOW()")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS grant_notes TEXT")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS revoked_by INTEGER")
    cur.execute("ALTER TABLE stocktake_access_grants ADD COLUMN IF NOT EXISTS revoke_notes TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_access_grants_user_active ON stocktake_access_grants(user_id, revoked_at, expires_at DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_stocktake_access_grants_request ON stocktake_access_grants(approval_request_id)")

    # --- SEEDING ---

    # 1. Seed Services (Only if empty)
    cur.execute("SELECT COUNT(*) FROM services")
    if cur.fetchone()['count'] == 0:
        initial_services = [
            ('FI Cleaning', 'Labor'),
            ('Change Oil', 'Maintenance'),
            ('Tune-up', 'Maintenance'),
            ('Clean CVT', 'Maintenance'),
            ('Clean Carb', 'Maintenance'),
            ('Welding', 'Fabrication'),
            ('Top Overhaul', 'Major Repair'),
            ('Engine Overhaul', 'Major Repair'),
            ('Change Brake Pad', 'Maintenance'),
            ('Change Brake Shoe', 'Maintenance'),
            ('Change Tire', 'Labor'),
            ('Minor Electrical', 'Electrical'),
            ('Battery Charging', 'Maintenance'),
            ('Change Oil Seal', 'Labor'),
            ('Change Sprocket', 'Labor'),
            ('Change Stator', 'Major Repair'),
            ('Change Clutch Lining', 'Major Repair'),
            ('Change Bulb', 'Labor'),
            ('Change Cable', 'Labor'),
            ('Change Bearing', 'Labor'),
            ('Change Carbon Brush', 'Labor'),
            ('Change Filter', 'Maintenance'),
            ('Change Handle Grip', 'Labor'),
            ('Change Horn', 'Labor'),
            ('Hangin', 'Maintenance'),
            ('Maintenance', 'Maintenance'),
            ('Labor', 'Labor')
        ]
        cur.executemany(
            "INSERT INTO services (name, category) VALUES (%s, %s)",
            initial_services
        )
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
