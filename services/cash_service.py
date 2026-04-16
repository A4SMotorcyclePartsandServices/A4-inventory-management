from db.database import get_db
from psycopg2 import errors as pg_errors
from utils.formatters import format_date
from datetime import timedelta
import re
from utils.cash_categories import normalize_cash_category_label
from utils.timezone import today_local

SYSTEM_KEY_CASH_IN_BANK_TRANSFER = "cash_in_bank_transfer"
SYSTEM_KEY_CASH_IN_EWALLET_TRANSFER = "cash_in_ewallet_transfer"
SYSTEM_KEY_CASH_OUT_MECHANIC_PAYOUT = "cash_out_mechanic_payout"
SYSTEM_KEY_CASH_OUT_UTILITIES = "cash_out_utilities"
DEFAULT_FLOATING_CASH_IN_SYSTEM_KEY = SYSTEM_KEY_CASH_IN_EWALLET_TRANSFER

# --- PHYSICAL CASH FILTER ---
# Only payment methods in this category count as physical cash in the drawer.
# If client later confirms GCash/PayMaya count too, add 'Online' here.
# One constant, affects the entire service automatically.
PHYSICAL_CASH_CATEGORIES = ('Cash',)
FLOATING_PAYMENT_CATEGORIES = ('Bank', 'Online')


def _money(value):
    """Normalize DB numeric/decimal values to float for calculations and JSON."""
    return round(float(value or 0), 2)


def _display_refund_label(refund_number):
    label = (refund_number or "").strip()
    return re.sub(r"^(RF-\d+-\d{4})-\d+$", r"\1", label)


def _display_exchange_refund_label(refund_number):
    label = _display_refund_label(refund_number)
    if label.startswith("RF-"):
        return "EX-" + label[3:]
    return label


def _refund_parenthetical_label(refund_number, exchange_refund=False):
    label = _display_exchange_refund_label(refund_number) if exchange_refund else _display_refund_label(refund_number)
    match = re.match(r"^((?:RF|EX)-\d+)-\d{4}$", label)
    if match:
        return match.group(1)
    return label


def get_cash_category_choices(include_inactive=False):
    conn = get_db()
    try:
        params = []
        query = """
            SELECT
                id,
                entry_type,
                label,
                system_key,
                requires_description,
                sort_order,
                is_active,
                is_system
            FROM cash_entry_categories
        """
        if not include_inactive:
            query += " WHERE is_active = TRUE"
        query += " ORDER BY entry_type ASC, LOWER(TRIM(label)) ASC, sort_order ASC"
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    grouped = {"CASH_IN": [], "CASH_OUT": []}
    for row in rows:
        grouped[row["entry_type"]].append({
            "id": int(row["id"]),
            "entry_type": row["entry_type"],
            "label": row["label"],
            "system_key": row["system_key"],
            "requires_description": bool(row["requires_description"]),
            "sort_order": int(row["sort_order"] or 0),
            "is_active": bool(row["is_active"]),
            "is_system": bool(row["is_system"]),
        })
    for entry_type in grouped:
        grouped[entry_type].sort(key=lambda category: (category["label"] or "").strip().lower())
    return grouped


def get_cash_category_admin_records():
    grouped = get_cash_category_choices(include_inactive=True)
    return grouped["CASH_IN"] + grouped["CASH_OUT"]


def _get_active_cash_category_by_id(conn, category_id, entry_type):
    try:
        normalized_id = int(category_id)
    except (TypeError, ValueError):
        return None

    return conn.execute(
        """
        SELECT
            id,
            entry_type,
            label,
            system_key,
            requires_description,
            is_active,
            is_system
        FROM cash_entry_categories
        WHERE id = %s
          AND entry_type = %s
          AND is_active = TRUE
        """,
        (normalized_id, entry_type),
    ).fetchone()


def _get_cash_category_by_system_key(conn, system_key):
    if not system_key:
        return None
    return conn.execute(
        """
        SELECT
            id,
            entry_type,
            label,
            system_key,
            requires_description,
            is_active,
            is_system
        FROM cash_entry_categories
        WHERE system_key = %s
        """,
        (system_key,),
    ).fetchone()


def _get_cash_category_label_by_system_key(conn, system_key, fallback_label):
    row = _get_cash_category_by_system_key(conn, system_key)
    if not row:
        return fallback_label
    return row["label"] or fallback_label


def _get_cash_category_id_by_system_key(conn, system_key):
    row = _get_cash_category_by_system_key(conn, system_key)
    if not row:
        return None
    return int(row["id"])


def _suggest_cash_in_category_system_key(payment_category):
    return SYSTEM_KEY_CASH_IN_BANK_TRANSFER if payment_category == "Bank" else SYSTEM_KEY_CASH_IN_EWALLET_TRANSFER


def add_cash_category_record(entry_type, label, requires_description=False, created_by=None):
    normalized_entry_type = str(entry_type or "").strip().upper()
    normalized_label = " ".join(str(label or "").strip().split())

    if normalized_entry_type not in {"CASH_IN", "CASH_OUT"}:
        return {"status": "invalid_entry_type"}
    if not normalized_label:
        return {"status": "missing_label"}

    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT id
            FROM cash_entry_categories
            WHERE entry_type = %s
              AND LOWER(TRIM(label)) = %s
            """,
            (normalized_entry_type, normalize_cash_category_label(normalized_label)),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "label": normalized_label}

        sort_row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) AS max_sort_order FROM cash_entry_categories WHERE entry_type = %s",
            (normalized_entry_type,),
        ).fetchone()
        next_sort = int(sort_row["max_sort_order"] or 0) + 10

        try:
            row = conn.execute(
                """
                INSERT INTO cash_entry_categories (
                    entry_type,
                    label,
                    requires_description,
                    sort_order,
                    is_active,
                    is_system,
                    created_by,
                    updated_by
                )
                VALUES (%s, %s, %s, %s, TRUE, FALSE, %s, %s)
                RETURNING id
                """,
                (
                    normalized_entry_type,
                    normalized_label,
                    bool(requires_description),
                    next_sort,
                    created_by,
                    created_by,
                ),
            ).fetchone()
        except pg_errors.UniqueViolation:
            conn.rollback()
            return {"status": "duplicate", "label": normalized_label}
        conn.commit()
        return {
            "status": "ok",
            "id": int(row["id"]),
            "label": normalized_label,
            "entry_type": normalized_entry_type,
        }
    finally:
        conn.close()


def toggle_cash_category_active_status(category_id, updated_by=None):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT id, label, is_active, is_system
            FROM cash_entry_categories
            WHERE id = %s
            """,
            (category_id,),
        ).fetchone()
        if not row:
            return {"status": "missing"}
        if row["is_system"]:
            return {"status": "system_locked", "label": row["label"]}

        new_status = not bool(row["is_active"])
        conn.execute(
            """
            UPDATE cash_entry_categories
            SET is_active = %s,
                updated_at = NOW(),
                updated_by = %s
            WHERE id = %s
            """,
            (new_status, updated_by, category_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "label": row["label"],
            "new_status": new_status,
        }
    finally:
        conn.close()


def _get_non_cash_paid_sales(conn, date_from=None, date_to=None):
    params = [list(FLOATING_PAYMENT_CATEGORIES)]

    query = """
        SELECT
            s.id AS sale_id,
            s.sales_number,
            s.customer_name,
            SUM(sp.amount) AS amount,
            s.transaction_date,
            STRING_AGG(DISTINCT pm.name, ' + ' ORDER BY pm.name) AS payment_method_name,
            CASE
                WHEN COUNT(DISTINCT pm.category) > 1 THEN 'Mixed'
                ELSE MAX(pm.category)
            END AS payment_method_category,
            u.username AS recorded_by
        FROM sale_payments sp
        JOIN sales s ON s.id = sp.sale_id
        JOIN payment_methods pm ON pm.id = sp.payment_method_id
        LEFT JOIN users u ON u.id = s.user_id
        WHERE s.status = 'Paid'
          AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
          AND pm.category = ANY(%s)
    """

    if date_from:
        query += " AND DATE(s.transaction_date) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(s.transaction_date) <= %s"
        params.append(date_to)

    query += " GROUP BY s.id, s.sales_number, s.customer_name, s.transaction_date, u.username ORDER BY s.transaction_date ASC, s.id ASC"
    return conn.execute(query, params).fetchall()


def _get_non_cash_debt_payments(conn, date_from=None, date_to=None):
    params = [list(FLOATING_PAYMENT_CATEGORIES)]

    query = """
        SELECT
            dp.id AS debt_payment_id,
            dp.sale_id,
            s.sales_number,
            s.customer_name,
            dp.amount_paid AS amount,
            dp.paid_at AS transaction_date,
            pm.name AS payment_method_name,
            pm.category AS payment_method_category,
            u.username AS recorded_by
        FROM debt_payments dp
        JOIN sales s ON s.id = dp.sale_id
        JOIN payment_methods pm ON pm.id = dp.payment_method_id
        LEFT JOIN users u ON u.id = dp.paid_by
        WHERE COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
          AND pm.category = ANY(%s)
    """

    if date_from:
        query += " AND DATE(dp.paid_at) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(dp.paid_at) <= %s"
        params.append(date_to)

    query += " ORDER BY dp.paid_at ASC, dp.id ASC"
    return conn.execute(query, params).fetchall()


def _get_active_float_claimed_sale_ids(conn, sale_ids, claimed_on_or_before=None):
    normalized_ids = [int(sale_id) for sale_id in (sale_ids or []) if sale_id is not None]
    if not normalized_ids:
        return set()

    params = [normalized_ids]
    query = """
        SELECT DISTINCT cfc.sale_id
        FROM cash_float_claims cfc
        JOIN cash_entries ce ON ce.id = cfc.cash_entry_id
        WHERE cfc.sale_id = ANY(%s)
          AND COALESCE(ce.is_deleted, FALSE) = FALSE
    """

    if claimed_on_or_before:
        query += " AND DATE(ce.created_at) <= %s"
        params.append(claimed_on_or_before)

    rows = conn.execute(query, params).fetchall()
    return {int(row['sale_id']) for row in rows}


def _get_active_float_claimed_debt_payment_ids(conn, debt_payment_ids, claimed_on_or_before=None):
    normalized_ids = [int(payment_id) for payment_id in (debt_payment_ids or []) if payment_id is not None]
    if not normalized_ids:
        return set()

    params = [normalized_ids]
    query = """
        SELECT DISTINCT cdpc.debt_payment_id
        FROM cash_debt_payment_claims cdpc
        JOIN cash_entries ce ON ce.id = cdpc.cash_entry_id
        WHERE cdpc.debt_payment_id = ANY(%s)
          AND COALESCE(ce.is_deleted, FALSE) = FALSE
    """

    if claimed_on_or_before:
        query += " AND DATE(ce.created_at) <= %s"
        params.append(claimed_on_or_before)

    rows = conn.execute(query, params).fetchall()
    return {int(row['debt_payment_id']) for row in rows}


# ─────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────

def _get_sales_cash(conn, branch_id=1, date_from=None, date_to=None):
    """
    [Source 1] Direct cash sales that are fully Paid.
    Always CASH_IN — never appears when filtering for CASH_OUT.
    """
    params = [list(PHYSICAL_CASH_CATEGORIES)]

    query = """
        SELECT
            s.id AS reference_id,
            s.sales_number,
            s.customer_name,
            SUM(sp.amount) AS amount,
            s.transaction_date AS created_at,
            u.username AS recorded_by,
            se.exchange_number
        FROM sale_payments sp
        JOIN sales s ON s.id = sp.sale_id
        JOIN payment_methods pm ON pm.id = sp.payment_method_id
        LEFT JOIN users u ON u.id = s.user_id
        LEFT JOIN sale_exchanges se ON se.replacement_sale_id = s.id
        WHERE pm.category = ANY(%s)
        AND s.status = 'Paid'
        AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
    """

    if date_from:
        query += " AND DATE(s.transaction_date) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(s.transaction_date) <= %s"
        params.append(date_to)

    query += " GROUP BY s.id, s.sales_number, s.customer_name, s.transaction_date, u.username, se.exchange_number ORDER BY s.transaction_date ASC, s.id ASC"
    return conn.execute(query, params).fetchall()


def _get_debt_cash_payments(conn, branch_id=1, date_from=None, date_to=None):
    """
    [Source 2] Cash payments that settled Utang balances.
    Always CASH_IN — never appears when filtering for CASH_OUT.
    """
    params = [list(PHYSICAL_CASH_CATEGORIES)]

    query = """
        SELECT
            dp.id           AS reference_id,
            s.sales_number,
            s.customer_name,
            dp.amount_paid  AS amount,
            dp.paid_at      AS created_at,
            u.username      AS recorded_by
        FROM debt_payments dp
        JOIN sales s            ON s.id  = dp.sale_id
        JOIN payment_methods pm ON pm.id = dp.payment_method_id
        LEFT JOIN users u       ON u.id  = dp.paid_by
        WHERE pm.category = ANY(%s)
    """

    if date_from:
        query += " AND DATE(dp.paid_at) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(dp.paid_at) <= %s"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _get_sale_refunds_cash(conn, branch_id=1, date_from=None, date_to=None):
    """
    [Source 3] Sale refunds.
    Refunds always reduce physical cash on hand because cash leaves the drawer.
    """
    params = []
    query = """
        SELECT
            sr.id AS reference_id,
            sr.refund_number,
            s.sales_number,
            s.customer_name,
            sr.refund_amount AS amount,
            sr.refund_date AS created_at,
            sr.refunded_by_username AS recorded_by,
            se.exchange_number
        FROM sale_refunds sr
        JOIN sales s ON s.id = sr.sale_id
        LEFT JOIN sale_exchanges se ON se.refund_id = sr.id
        WHERE 1 = 1
    """

    if date_from:
        query += " AND DATE(sr.refund_date) >= %s"
        params.append(date_from)
    if date_to:
        query += " AND DATE(sr.refund_date) <= %s"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _get_manual_entries(conn, branch_id=1, date_from=None, date_to=None, entry_type=None, deleted_state='active'):
    """
    [Sources 3 & 4] Manual petty cash entries.
    Supports optional entry_type filter ('CASH_IN' or 'CASH_OUT').
    """
    if deleted_state not in {'active', 'deleted', 'all'}:
        raise ValueError("Invalid deleted state.")

    params = [branch_id]
    query = """
        SELECT
            ce.id,
            ce.entry_type,
            ce.amount,
            ce.category,
            ce.description,
            ce.reference_type,
            ce.created_at,
            ce.deleted_at,
            u.username AS recorded_by,
            du.username AS deleted_by_username
        FROM cash_entries ce
        LEFT JOIN users u ON u.id = ce.user_id
        LEFT JOIN users du ON du.id = ce.deleted_by
        WHERE ce.branch_id = %s
        AND ce.reference_type IN ('MANUAL', 'MECHANIC_PAYOUT', 'FLOAT_COLLECTION')
    """

    if deleted_state == 'active':
        query += " AND COALESCE(ce.is_deleted, FALSE) = FALSE"
    elif deleted_state == 'deleted':
        query += " AND COALESCE(ce.is_deleted, FALSE) = TRUE"

    if entry_type:
        query += " AND ce.entry_type = %s"
        params.append(entry_type)
    if date_from:
        date_column = "ce.deleted_at" if deleted_state == 'deleted' else "ce.created_at"
        query += f" AND DATE({date_column}) >= %s"
        params.append(date_from)
    if date_to:
        date_column = "ce.deleted_at" if deleted_state == 'deleted' else "ce.created_at"
        query += f" AND DATE({date_column}) <= %s"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _build_unified(sales_rows, debt_rows, refund_rows, manual_rows):
    """
    Merges all 3 sources into a single normalized list sorted newest first.
    Each row has the same shape regardless of source — the HTML never needs
    to know where a row came from.
    """
    unified = []

    for row in sales_rows:
        customer = row['customer_name'] or 'Walk-in'
        unified.append({
            'entry_type':  'CASH_IN',
            'amount':      _money(row['amount']),
            'category':    'Exchange/Replacement' if row['exchange_number'] else 'Cash Sale',
            'description': f"{row['sales_number']} — {customer}" + (f" ({row['exchange_number']})" if row['exchange_number'] else ""),
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'sale',
            '_raw_date':   row['created_at'] or '',
        })

    for row in debt_rows:
        customer = row['customer_name'] or 'Walk-in'
        unified.append({
            'entry_type':  'CASH_IN',
            'amount':      _money(row['amount']),
            'category':    'Debt Payment',
            'description': f"{row['sales_number']} — {customer}",
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'debt_payment',
            '_raw_date':   row['created_at'] or '',
        })

    for row in refund_rows:
        customer = row['customer_name'] or 'Walk-in'
        is_exchange_refund = bool(row['exchange_number'])
        label = (
            _display_exchange_refund_label(row['refund_number'])
            if is_exchange_refund
            else _display_refund_label(row['refund_number'])
        ) or f"Refund #{row['reference_id']}"
        refund_reference = _refund_parenthetical_label(
            row['refund_number'],
            exchange_refund=is_exchange_refund,
        )
        unified.append({
            'entry_type':  'CASH_OUT',
            'amount':      _money(row['amount']),
            'category':    'Exchange/Refund' if is_exchange_refund else 'Sale Refund',
            'description': f"{label} - {customer}" + (f" ({refund_reference})" if is_exchange_refund and refund_reference else ""),
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '-',
            'source':      'refund',
            '_raw_date':   row['created_at'] or '',
        })

    for row in manual_rows:
        unified.append({
            'id':          row['id'],
            'entry_type':  row['entry_type'],
            'amount':      _money(row['amount']),
            'category':    row['category'],
            'description': row['description'] or '—',
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'float_collection' if row.get('reference_type') == 'FLOAT_COLLECTION' else 'manual',
            '_raw_date':   row['created_at'] or '',
        })

    manual_offset = len(unified) - len(manual_rows)
    for index, row in enumerate(manual_rows):
        if not row['deleted_at']:
            continue
        entry = unified[manual_offset + index]
        entry['deleted_at'] = format_date(row['deleted_at'], show_time=True)
        entry['deleted_by'] = row['deleted_by_username'] or '-'
        entry['purge_at'] = format_date(row['deleted_at'] + timedelta(days=30), show_time=True)
        entry['_raw_date'] = row['deleted_at']

    unified.sort(key=lambda x: x['_raw_date'], reverse=True)

    for row in unified:
        del row['_raw_date']

    return unified


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

def get_cash_summary(branch_id=1):
    """
    Full cash on hand from all 4 sources.
    Summary always ignores entry_type filter — it must always show
    the real total regardless of what the ledger table is filtered to.
    """
    conn = get_db()
    sales_rows  = _get_sales_cash(conn, branch_id)
    debt_rows   = _get_debt_cash_payments(conn, branch_id)
    refund_rows = _get_sale_refunds_cash(conn, branch_id)
    manual_rows = _get_manual_entries(conn, branch_id, deleted_state='active')
    pending_float_rows = _get_non_cash_paid_sales(conn)
    pending_float_debt_rows = _get_non_cash_debt_payments(conn)
    claimed_float_ids = _get_active_float_claimed_sale_ids(
        conn,
        [row['sale_id'] for row in pending_float_rows],
    )
    claimed_float_debt_ids = _get_active_float_claimed_debt_payment_ids(
        conn,
        [row['debt_payment_id'] for row in pending_float_debt_rows],
    )
    conn.close()

    total_in  = 0.0
    total_out = 0.0

    for row in sales_rows:
        total_in += _money(row['amount'])
    for row in debt_rows:
        total_in += _money(row['amount'])
    for row in refund_rows:
        total_out += _money(row['amount'])
    for row in manual_rows:
        if row['entry_type'] == 'CASH_IN':
            total_in  += _money(row['amount'])
        else:
            total_out += _money(row['amount'])

    total_in  = round(total_in,  2)
    total_out = round(total_out, 2)
    floating_total = round(
        sum(_money(row['amount']) for row in pending_float_rows if int(row['sale_id']) not in claimed_float_ids)
        + sum(
            _money(row['amount'])
            for row in pending_float_debt_rows
            if int(row['debt_payment_id']) not in claimed_float_debt_ids
        ),
        2,
    )

    return {
        'total_in':     total_in,
        'total_out':    total_out,
        'cash_on_hand': round(total_in - total_out, 2),
        'floating_total': floating_total,
    }


def get_cash_balance_as_of(date_to, branch_id=1):
    """
    True cumulative cash on hand as of the given date.
    This ignores any report-range start date and totals all active cash movement
    through the selected end date so balancing matches the real drawer state.
    """
    conn = get_db()
    try:
        sales_rows = _get_sales_cash(conn, branch_id, date_to=date_to)
        debt_rows = _get_debt_cash_payments(conn, branch_id, date_to=date_to)
        refund_rows = _get_sale_refunds_cash(conn, branch_id, date_to=date_to)
        manual_rows = _get_manual_entries(
            conn,
            branch_id,
            date_to=date_to,
            deleted_state='active',
        )
    finally:
        conn.close()

    total_in = 0.0
    total_out = 0.0

    for row in sales_rows:
        total_in += _money(row['amount'])
    for row in debt_rows:
        total_in += _money(row['amount'])
    for row in refund_rows:
        total_out += _money(row['amount'])
    for row in manual_rows:
        if row['entry_type'] == 'CASH_IN':
            total_in += _money(row['amount'])
        else:
            total_out += _money(row['amount'])

    return round(total_in - total_out, 2)


def get_cash_entry_count(branch_id=1, entry_type=None, start_date=None, end_date=None, ledger_view='active'):
    """
    Total number of unified ledger rows matching the given filters.
    Used by the route to calculate total_pages before fetching the page slice.

    Why not just len(get_cash_entries(...))?
    Because get_cash_entries fetches and formats every row just to count them.
    This is cheaper — build unified list without formatting, just count it.
    At current scale it barely matters, but it's the right habit.
    """
    conn = get_db()

    # Sales/debt are always CASH_IN. Refunds are always CASH_OUT.
    if ledger_view == 'deleted':
        sales_rows = []
        debt_rows = []
        refund_rows = []
    elif entry_type == 'CASH_IN':
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows = _get_debt_cash_payments(conn, branch_id, start_date, end_date)
        refund_rows = []
    elif entry_type == 'CASH_OUT':
        sales_rows = []
        debt_rows  = []
        refund_rows = _get_sale_refunds_cash(conn, branch_id, start_date, end_date)
    else:
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows  = _get_debt_cash_payments(conn, branch_id, start_date, end_date)
        refund_rows = _get_sale_refunds_cash(conn, branch_id, start_date, end_date)

    deleted_state = 'deleted' if ledger_view == 'deleted' else 'active'
    manual_rows = _get_manual_entries(conn, branch_id, start_date, end_date, entry_type, deleted_state=deleted_state)
    conn.close()

    return len(sales_rows) + len(debt_rows) + len(refund_rows) + len(manual_rows)


def get_cash_entries(branch_id=1, limit=None, offset=None,
                    entry_type=None, start_date=None, end_date=None, ledger_view='active'):
    """
    Unified ledger with optional pagination and filtering.

    entry_type  : 'CASH_IN', 'CASH_OUT', or None (all)
    start_date  : 'YYYY-MM-DD' or None
    end_date    : 'YYYY-MM-DD' or None
    limit       : page size
    offset      : how many rows to skip (for pagination)
    """
    conn = get_db()

    # Sales/debt are always CASH_IN. Refunds are always CASH_OUT.
    if ledger_view == 'deleted':
        sales_rows = []
        debt_rows = []
        refund_rows = []
    elif entry_type == 'CASH_IN':
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows = _get_debt_cash_payments(conn, branch_id, start_date, end_date)
        refund_rows = []
    elif entry_type == 'CASH_OUT':
        sales_rows = []
        debt_rows  = []
        refund_rows = _get_sale_refunds_cash(conn, branch_id, start_date, end_date)
    else:
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows  = _get_debt_cash_payments(conn, branch_id, start_date, end_date)
        refund_rows = _get_sale_refunds_cash(conn, branch_id, start_date, end_date)

    deleted_state = 'deleted' if ledger_view == 'deleted' else 'active'
    manual_rows = _get_manual_entries(conn, branch_id, start_date, end_date, entry_type, deleted_state=deleted_state)
    conn.close()

    unified = _build_unified(sales_rows, debt_rows, refund_rows, manual_rows)

    # Apply pagination after merge+sort so ordering is always correct
    if offset:
        unified = unified[offset:]
    if limit:
        unified = unified[:limit]

    return unified


# ─────────────────────────────────────────────
# MECHANIC PAYOUT HELPERS

def get_already_paid_mechanic_identifiers(date, branch_id=1):
    """
    Returns paid identifiers for Mechanic Payout entries on the given date.
    New rows are matched by mechanic reference_id, with fallback to legacy
    description matching for existing records.
    """
    return get_already_paid_mechanic_identifiers_for_dates([date], branch_id=branch_id).get(
        date,
        {"mechanic_ids": set(), "mechanic_names": set()},
    )


def get_already_paid_mechanic_identifiers_for_dates(dates, branch_id=1):
    """
    Batched paid mechanic lookup keyed by payout date.
    Returns:
      {
        'YYYY-MM-DD': {
          'mechanic_ids': set(...),
          'mechanic_names': set(...)
        }
      }
    """
    normalized_dates = sorted({str(d) for d in (dates or []) if d})
    if not normalized_dates:
        return {}

    result = {
        day: {"mechanic_ids": set(), "mechanic_names": set()}
        for day in normalized_dates
    }

    conn = get_db()
    mechanic_rows = conn.execute("""
        SELECT
            COALESCE(payout_for_date, DATE(created_at)) AS payout_date,
            reference_id
        FROM cash_entries
        WHERE branch_id = %s
          AND entry_type = 'CASH_OUT'
          AND category = 'Mechanic Payout'
          AND reference_type = 'MECHANIC_PAYOUT'
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND COALESCE(payout_for_date, DATE(created_at)) = ANY(%s::date[])
          AND reference_id IS NOT NULL
    """, [branch_id, normalized_dates]).fetchall()

    legacy_rows = conn.execute("""
        SELECT
            COALESCE(payout_for_date, DATE(created_at)) AS payout_date,
            description
        FROM cash_entries
        WHERE branch_id = %s
          AND entry_type = 'CASH_OUT'
          AND category = 'Mechanic Payout'
          AND reference_type IN ('MANUAL', 'MECHANIC_PAYOUT')
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND COALESCE(payout_for_date, DATE(created_at)) = ANY(%s::date[])
    """, [branch_id, normalized_dates]).fetchall()
    conn.close()

    for row in mechanic_rows:
        day = str(row["payout_date"])
        if day in result and row["reference_id"] is not None:
            result[day]["mechanic_ids"].add(int(row["reference_id"]))

    for row in legacy_rows:
        day = str(row["payout_date"])
        if day in result and row["description"]:
            result[day]["mechanic_names"].add(row["description"])

    return result


def get_already_paid_mechanic_names(date, branch_id=1):
    """
    Backward-compatible helper for legacy callsites.
    """
    return get_already_paid_mechanic_identifiers(date, branch_id=branch_id)["mechanic_names"]
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────

def get_pending_non_cash_collections(branch_id=1, limit_groups=None):
    conn = get_db()
    try:
        sale_rows = _get_non_cash_paid_sales(conn)
        debt_payment_rows = _get_non_cash_debt_payments(conn)
        claimed_sale_ids = _get_active_float_claimed_sale_ids(
            conn,
            [row['sale_id'] for row in sale_rows],
        )
        claimed_debt_payment_ids = _get_active_float_claimed_debt_payment_ids(
            conn,
            [row['debt_payment_id'] for row in debt_payment_rows],
        )
        groups = []
        total_amount = 0.0
        total_sales = 0

        for row in sale_rows:
            sale_id = int(row['sale_id'])
            if sale_id in claimed_sale_ids:
                continue

            sales_number = (row['sales_number'] or '').strip()
            customer_name = (row['customer_name'] or '').strip() or 'Walk-in'
            transaction_date = str(row['transaction_date'])[:10]
            payment_method_name = row['payment_method_name'] or 'Non-cash'
            payment_category = row['payment_method_category'] or 'Online'
            amount = _money(row['amount'])

            suggested_key = _suggest_cash_in_category_system_key(payment_category)
            groups.append({
                'transaction_date': transaction_date,
                'date_display': format_date(transaction_date),
                'payment_method_name': payment_method_name,
                'payment_method_category': payment_category,
                'cash_in_category_key': suggested_key,
                'cash_in_category': _get_cash_category_label_by_system_key(conn, suggested_key, 'From Gcash/E-Wallet Account'),
                'sale_ids': [sale_id],
                'debt_payment_ids': [],
                'sales_count': 1,
                'total_amount': amount,
                'auto_description': f"{sales_number} — {customer_name}" if sales_number else customer_name,
                'display_source_label': f"From {sales_number}" if sales_number else f"From {payment_method_name}",
            })

            total_amount = round(total_amount + amount, 2)
            total_sales += 1

        for row in debt_payment_rows:
            debt_payment_id = int(row['debt_payment_id'])
            if debt_payment_id in claimed_debt_payment_ids:
                continue

            sales_number = (row['sales_number'] or '').strip()
            customer_name = (row['customer_name'] or '').strip() or 'Walk-in'
            transaction_date = str(row['transaction_date'])[:10]
            payment_method_name = row['payment_method_name'] or 'Non-cash'
            payment_category = row['payment_method_category'] or 'Online'
            amount = _money(row['amount'])

            suggested_key = _suggest_cash_in_category_system_key(payment_category)
            groups.append({
                'transaction_date': transaction_date,
                'date_display': format_date(transaction_date),
                'payment_method_name': payment_method_name,
                'payment_method_category': payment_category,
                'cash_in_category_key': suggested_key,
                'cash_in_category': _get_cash_category_label_by_system_key(conn, suggested_key, 'From Gcash/E-Wallet Account'),
                'sale_ids': [],
                'debt_payment_ids': [debt_payment_id],
                'sales_count': 1,
                'total_amount': amount,
                'auto_description': f"{sales_number} — {customer_name}" if sales_number else customer_name,
                'display_source_label': f"From {sales_number}" if sales_number else f"From {payment_method_name}",
            })

            total_amount = round(total_amount + amount, 2)
            total_sales += 1

        groups.sort(key=lambda row: (row['transaction_date'], row['payment_method_name'].lower(), row['display_source_label'].lower()))

        if limit_groups is not None:
            groups = groups[:max(0, int(limit_groups))]

        return {
            'groups': groups,
            'total_amount': round(total_amount, 2),
            'total_sales': total_sales,
        }
    finally:
        conn.close()


def get_pending_non_cash_collection_count(branch_id=1):
    conn = get_db()
    try:
        sale_rows = _get_non_cash_paid_sales(conn)
        debt_payment_rows = _get_non_cash_debt_payments(conn)
        claimed_sale_ids = _get_active_float_claimed_sale_ids(
            conn,
            [row['sale_id'] for row in sale_rows],
        )
        claimed_debt_payment_ids = _get_active_float_claimed_debt_payment_ids(
            conn,
            [row['debt_payment_id'] for row in debt_payment_rows],
        )
    finally:
        conn.close()

    sale_count = sum(1 for row in sale_rows if int(row['sale_id']) not in claimed_sale_ids)
    debt_count = sum(
        1 for row in debt_payment_rows
        if int(row['debt_payment_id']) not in claimed_debt_payment_ids
    )
    return sale_count + debt_count


def add_cash_entry(
    entry_type,
    amount,
    category_id,
    description,
    reference_id,
    payout_for_date,
    user_id,
    branch_id=1,
    claim_sale_ids=None,
    claim_debt_payment_ids=None,
):
    """
    Records a single manual petty cash movement only.
    Sales and debt cash is calculated live — never written here.
    """
    if entry_type not in ('CASH_IN', 'CASH_OUT'):
        raise ValueError("Invalid entry type.")

    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        raise ValueError("Invalid amount.")

    if amount <= 0:
        raise ValueError("Amount must be greater than zero.")

    normalized_description = (description or "").strip()

    normalized_reference_id = None
    if reference_id not in (None, ""):
        try:
            normalized_reference_id = int(reference_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid mechanic reference.")

    normalized_claim_sale_ids = []
    for sale_id in (claim_sale_ids or []):
        try:
            normalized_claim_sale_ids.append(int(sale_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid floating collection reference.")
    normalized_claim_sale_ids = sorted(set(normalized_claim_sale_ids))

    normalized_claim_debt_payment_ids = []
    for debt_payment_id in (claim_debt_payment_ids or []):
        try:
            normalized_claim_debt_payment_ids.append(int(debt_payment_id))
        except (TypeError, ValueError):
            raise ValueError("Invalid debt floating collection reference.")
    normalized_claim_debt_payment_ids = sorted(set(normalized_claim_debt_payment_ids))

    conn = get_db()
    try:
        category_row = _get_active_cash_category_by_id(conn, category_id, entry_type)
        if not category_row:
            raise ValueError(f"Invalid category for {entry_type}.")

        selected_category_id = int(category_row["id"])
        category_label = category_row["label"]
        category_system_key = category_row["system_key"]

        if bool(category_row["requires_description"]) and not normalized_description:
            if category_system_key == SYSTEM_KEY_CASH_OUT_UTILITIES:
                raise ValueError("Please indicate which utility this is for.")
            raise ValueError(f"Description is required when category is {category_label}.")

        reference_type = 'MANUAL'
        if category_system_key == SYSTEM_KEY_CASH_OUT_MECHANIC_PAYOUT and normalized_reference_id is not None:
            reference_type = 'MECHANIC_PAYOUT'
            if payout_for_date in ("", None):
                payout_for_date = today_local().isoformat()
        else:
            payout_for_date = None

        if normalized_claim_sale_ids or normalized_claim_debt_payment_ids:
            if entry_type != 'CASH_IN':
                raise ValueError("Floating collections must be recorded as cash in.")
            if category_system_key not in {SYSTEM_KEY_CASH_IN_BANK_TRANSFER, SYSTEM_KEY_CASH_IN_EWALLET_TRANSFER}:
                raise ValueError("Floating collections must use a bank or e-wallet cash-in category.")
            if normalized_reference_id is not None:
                raise ValueError("Floating collections cannot use a mechanic reference.")
            reference_type = 'FLOAT_COLLECTION'

        claimable_total = 0.0

        if normalized_claim_sale_ids:
            placeholders = ','.join(['%s'] * len(normalized_claim_sale_ids))
            claimable_rows = conn.execute(
                f"""
                SELECT
                    s.id AS sale_id,
                    SUM(sp.amount) AS amount
                FROM sale_payments sp
                JOIN sales s ON s.id = sp.sale_id
                JOIN payment_methods pm ON pm.id = sp.payment_method_id
                LEFT JOIN cash_float_claims cfc ON cfc.sale_id = s.id
                LEFT JOIN cash_entries ce
                    ON ce.id = cfc.cash_entry_id
                   AND COALESCE(ce.is_deleted, FALSE) = FALSE
                WHERE s.id IN ({placeholders})
                  AND s.status = 'Paid'
                  AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
                  AND pm.category IN ({','.join(['%s'] * len(FLOATING_PAYMENT_CATEGORIES))})
                  AND ce.id IS NULL
                GROUP BY s.id
                """,
                normalized_claim_sale_ids + list(FLOATING_PAYMENT_CATEGORIES),
            ).fetchall()

            if len(claimable_rows) != len(normalized_claim_sale_ids):
                raise ValueError("One or more floating collections were already claimed or are no longer eligible.")

            claimable_total = round(
                claimable_total + sum(_money(row['amount']) for row in claimable_rows),
                2,
            )

        if normalized_claim_debt_payment_ids:
            placeholders = ','.join(['%s'] * len(normalized_claim_debt_payment_ids))
            claimable_rows = conn.execute(
                f"""
                SELECT
                    dp.id AS debt_payment_id,
                    dp.amount_paid AS amount,
                    pm.category AS payment_method_category
                FROM debt_payments dp
                JOIN sales s ON s.id = dp.sale_id
                JOIN payment_methods pm ON pm.id = dp.payment_method_id
                LEFT JOIN cash_debt_payment_claims cdpc ON cdpc.debt_payment_id = dp.id
                LEFT JOIN cash_entries ce
                    ON ce.id = cdpc.cash_entry_id
                   AND COALESCE(ce.is_deleted, FALSE) = FALSE
                WHERE dp.id IN ({placeholders})
                  AND COALESCE(s.transaction_class, 'NEW_SALE') <> 'MECHANIC_SUPPLY'
                  AND pm.category IN ({','.join(['%s'] * len(FLOATING_PAYMENT_CATEGORIES))})
                  AND ce.id IS NULL
                """,
                normalized_claim_debt_payment_ids + list(FLOATING_PAYMENT_CATEGORIES),
            ).fetchall()

            if len(claimable_rows) != len(normalized_claim_debt_payment_ids):
                raise ValueError("One or more debt floating collections were already claimed or are no longer eligible.")

            claimable_total = round(
                claimable_total + sum(_money(row['amount']) for row in claimable_rows),
                2,
            )

        if normalized_claim_sale_ids or normalized_claim_debt_payment_ids:
            if claimable_total != amount:
                raise ValueError("Claim amount does not match the pending floating total.")

        insert_row = conn.execute("""
            INSERT INTO cash_entries
                (branch_id, entry_type, amount, cash_category_id, category, description,
                reference_type, reference_id, payout_for_date, user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            branch_id,
            entry_type,
            amount,
            selected_category_id,
            category_label,
            normalized_description or None,
            reference_type,
            normalized_reference_id,
            payout_for_date,
            user_id
        )).fetchone()

        entry_id = int(insert_row['id'])

        if normalized_claim_sale_ids:
            for sale_id in normalized_claim_sale_ids:
                conn.execute(
                    """
                    INSERT INTO cash_float_claims (sale_id, cash_entry_id)
                    VALUES (%s, %s)
                    """,
                    (sale_id, entry_id),
                )
        if normalized_claim_debt_payment_ids:
            for debt_payment_id in normalized_claim_debt_payment_ids:
                conn.execute(
                    """
                    INSERT INTO cash_debt_payment_claims (debt_payment_id, cash_entry_id)
                    VALUES (%s, %s)
                    """,
                    (debt_payment_id, entry_id),
                )
        conn.commit()
        return entry_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_cash_entry(entry_id, user_id, branch_id=1):
    """
    Soft deletes a manual cash entry.
    reference_type in ('MANUAL', 'MECHANIC_PAYOUT', 'FLOAT_COLLECTION') guard means sales and debt rows
    can never be deleted through this path even if called directly.
    Admin-only enforced at route level.
    """
    conn = get_db()
    try:
        result = conn.execute("""
            UPDATE cash_entries
            SET is_deleted = TRUE,
                deleted_at = NOW(),
                deleted_by = %s
            WHERE id = %s
              AND branch_id = %s
              AND reference_type IN ('MANUAL', 'MECHANIC_PAYOUT', 'FLOAT_COLLECTION')
              AND COALESCE(is_deleted, FALSE) = FALSE
        """, (user_id, entry_id, branch_id))

        if result.rowcount == 0:
            raise ValueError("Entry not found or cannot be deleted.")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_cash_entry(entry_id, branch_id=1):
    """
    Restores a soft-deleted manual cash entry back into the active ledger.
    """
    conn = get_db()
    try:
        result = conn.execute("""
            UPDATE cash_entries
            SET is_deleted = FALSE,
                deleted_at = NULL,
                deleted_by = NULL
            WHERE id = %s
              AND branch_id = %s
              AND reference_type IN ('MANUAL', 'MECHANIC_PAYOUT', 'FLOAT_COLLECTION')
              AND COALESCE(is_deleted, FALSE) = TRUE
        """, (entry_id, branch_id))

        if result.rowcount == 0:
            raise ValueError("Deleted entry not found or cannot be restored.")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def purge_deleted_cash_entries(branch_id=None):
    """
    Permanently removes soft-deleted cash entries after 30 days.
    """
    conn = get_db()
    try:
        params = []
        query = """
            DELETE FROM cash_entries
            WHERE COALESCE(is_deleted, FALSE) = TRUE
              AND deleted_at IS NOT NULL
              AND deleted_at < (NOW() - INTERVAL '30 days')
        """

        if branch_id is not None:
            query += " AND branch_id = %s"
            params.append(branch_id)

        result = conn.execute(query, params)
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# REPORT HELPER
# ─────────────────────────────────────────────

def get_cash_entries_for_report(date_from, date_to, branch_id=1, entry_type=None, ledger_view='active'):
    """
    Full unified ledger for a date range — used by the sales report PDF.
    Sorted oldest first so the PDF reads chronologically.
    """
    conn = get_db()

    if ledger_view == 'deleted':
        sales_rows = []
        debt_rows = []
        refund_rows = []
    elif entry_type == 'CASH_IN':
        sales_rows = _get_sales_cash(conn, branch_id, date_from, date_to)
        debt_rows = _get_debt_cash_payments(conn, branch_id, date_from, date_to)
        refund_rows = []
    elif entry_type == 'CASH_OUT':
        sales_rows = []
        debt_rows = []
        refund_rows = _get_sale_refunds_cash(conn, branch_id, date_from, date_to)
    else:
        sales_rows = _get_sales_cash(conn, branch_id, date_from, date_to)
        debt_rows = _get_debt_cash_payments(conn, branch_id, date_from, date_to)
        refund_rows = _get_sale_refunds_cash(conn, branch_id, date_from, date_to)

    deleted_state = 'deleted' if ledger_view == 'deleted' else 'active'
    manual_rows = _get_manual_entries(
        conn,
        branch_id,
        date_from,
        date_to,
        entry_type=entry_type,
        deleted_state=deleted_state,
    )
    conn.close()

    unified = _build_unified(sales_rows, debt_rows, refund_rows, manual_rows)

    # Reverse to oldest-first for PDF reading order
    unified.reverse()

    total_in  = sum(r['amount'] for r in unified if r['entry_type'] == 'CASH_IN')
    total_out = sum(r['amount'] for r in unified if r['entry_type'] == 'CASH_OUT')
    ending_cash_on_hand = None
    if ledger_view == 'active':
        ending_cash_on_hand = get_cash_balance_as_of(date_to, branch_id=branch_id)

    return {
        'entries':      unified,
        'total_in':     round(total_in,  2),
        'total_out':    round(total_out, 2),
        'cash_on_hand': round(total_in - total_out, 2),
        'ending_cash_on_hand': ending_cash_on_hand,
    }


