from db.database import get_db
from utils.formatters import format_date
from datetime import date as date_today

# --- CATEGORIES ---
CASH_IN_CATEGORIES  = ['Petty Cash', 'Owner Deposit', 'Other Income']
CASH_OUT_CATEGORIES = ['Parts Purchase', 'Staff Expense', 'Utilities', 'Supplies', 'Other Expense', 'Mechanic Payout']

# --- PHYSICAL CASH FILTER ---
# Only payment methods in this category count as physical cash in the drawer.
# If client later confirms GCash/PayMaya count too, add 'Online' here.
# One constant, affects the entire service automatically.
PHYSICAL_CASH_CATEGORIES = ('Cash',)


# ─────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────

def _get_sales_cash(conn, branch_id=1, date_from=None, date_to=None):
    """
    [Source 1] Direct cash sales that are fully Paid.
    Always CASH_IN — never appears when filtering for CASH_OUT.
    """
    placeholders = ','.join('?' * len(PHYSICAL_CASH_CATEGORIES))
    params = list(PHYSICAL_CASH_CATEGORIES)

    query = f"""
        SELECT
            s.id            AS reference_id,
            s.sales_number,
            s.customer_name,
            s.total_amount  AS amount,
            s.transaction_date AS created_at,
            u.username      AS recorded_by
        FROM sales s
        JOIN payment_methods pm ON pm.id = s.payment_method_id
        LEFT JOIN users u       ON u.id  = s.user_id
        WHERE pm.category IN ({placeholders})
        AND s.status = 'Paid'
    """

    if date_from:
        query += " AND DATE(s.transaction_date) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(s.transaction_date) <= ?"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _get_debt_cash_payments(conn, branch_id=1, date_from=None, date_to=None):
    """
    [Source 2] Cash payments that settled Utang balances.
    Always CASH_IN — never appears when filtering for CASH_OUT.
    """
    placeholders = ','.join('?' * len(PHYSICAL_CASH_CATEGORIES))
    params = list(PHYSICAL_CASH_CATEGORIES)

    query = f"""
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
        WHERE pm.category IN ({placeholders})
    """

    if date_from:
        query += " AND DATE(dp.paid_at) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(dp.paid_at) <= ?"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _get_manual_entries(conn, branch_id=1, date_from=None, date_to=None, entry_type=None):
    """
    [Sources 3 & 4] Manual petty cash entries.
    Supports optional entry_type filter ('CASH_IN' or 'CASH_OUT').
    """
    params = [branch_id]
    query = """
        SELECT
            ce.id,
            ce.entry_type,
            ce.amount,
            ce.category,
            ce.description,
            ce.created_at,
            u.username AS recorded_by
        FROM cash_entries ce
        LEFT JOIN users u ON u.id = ce.user_id
        WHERE ce.branch_id = ?
        AND ce.reference_type IN ('MANUAL', 'MECHANIC_PAYOUT')
    """

    if entry_type:
        query += " AND ce.entry_type = ?"
        params.append(entry_type)
    if date_from:
        query += " AND DATE(ce.created_at) >= ?"
        params.append(date_from)
    if date_to:
        query += " AND DATE(ce.created_at) <= ?"
        params.append(date_to)

    return conn.execute(query, params).fetchall()


def _build_unified(sales_rows, debt_rows, manual_rows):
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
            'amount':      round(row['amount'], 2),
            'category':    'Cash Sale',
            'description': f"{row['sales_number']} — {customer}",
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'sale',
            '_raw_date':   row['created_at'] or '',
        })

    for row in debt_rows:
        customer = row['customer_name'] or 'Walk-in'
        unified.append({
            'entry_type':  'CASH_IN',
            'amount':      round(row['amount'], 2),
            'category':    'Debt Payment',
            'description': f"{row['sales_number']} — {customer}",
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'debt_payment',
            '_raw_date':   row['created_at'] or '',
        })

    for row in manual_rows:
        unified.append({
            'entry_type':  row['entry_type'],
            'amount':      round(row['amount'], 2),
            'category':    row['category'],
            'description': row['description'] or '—',
            'created_at':  format_date(row['created_at'], show_time=True),
            'recorded_by': row['recorded_by'] or '—',
            'source':      'manual',
            '_raw_date':   row['created_at'] or '',
        })

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
    manual_rows = _get_manual_entries(conn, branch_id)
    conn.close()

    total_in  = 0.0
    total_out = 0.0

    for row in sales_rows:
        total_in += row['amount']
    for row in debt_rows:
        total_in += row['amount']
    for row in manual_rows:
        if row['entry_type'] == 'CASH_IN':
            total_in  += row['amount']
        else:
            total_out += row['amount']

    total_in  = round(total_in,  2)
    total_out = round(total_out, 2)

    return {
        'total_in':     total_in,
        'total_out':    total_out,
        'cash_on_hand': round(total_in - total_out, 2),
    }


def get_cash_entry_count(branch_id=1, entry_type=None, start_date=None, end_date=None):
    """
    Total number of unified ledger rows matching the given filters.
    Used by the route to calculate total_pages before fetching the page slice.

    Why not just len(get_cash_entries(...))?
    Because get_cash_entries fetches and formats every row just to count them.
    This is cheaper — build unified list without formatting, just count it.
    At current scale it barely matters, but it's the right habit.
    """
    conn = get_db()

    # Sales and debt are always CASH_IN — skip them entirely if filtering for CASH_OUT
    if entry_type == 'CASH_OUT':
        sales_rows = []
        debt_rows  = []
    else:
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows  = _get_debt_cash_payments(conn, branch_id, start_date, end_date)

    manual_rows = _get_manual_entries(conn, branch_id, start_date, end_date, entry_type)
    conn.close()

    return len(sales_rows) + len(debt_rows) + len(manual_rows)


def get_cash_entries(branch_id=1, limit=None, offset=None,
                    entry_type=None, start_date=None, end_date=None):
    """
    Unified ledger with optional pagination and filtering.

    entry_type  : 'CASH_IN', 'CASH_OUT', or None (all)
    start_date  : 'YYYY-MM-DD' or None
    end_date    : 'YYYY-MM-DD' or None
    limit       : page size
    offset      : how many rows to skip (for pagination)
    """
    conn = get_db()

    # Sales and debt are always CASH_IN — skip entirely if filtering for CASH_OUT
    if entry_type == 'CASH_OUT':
        sales_rows = []
        debt_rows  = []
    else:
        sales_rows = _get_sales_cash(conn, branch_id, start_date, end_date)
        debt_rows  = _get_debt_cash_payments(conn, branch_id, start_date, end_date)

    manual_rows = _get_manual_entries(conn, branch_id, start_date, end_date, entry_type)
    conn.close()

    unified = _build_unified(sales_rows, debt_rows, manual_rows)

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
    conn = get_db()
    mechanic_rows = conn.execute("""
        SELECT reference_id
        FROM cash_entries
        WHERE branch_id = ?
        AND entry_type = 'CASH_OUT'
        AND category = 'Mechanic Payout'
        AND reference_type = 'MECHANIC_PAYOUT'
        AND COALESCE(payout_for_date, DATE(created_at)) = ?
        AND reference_id IS NOT NULL
    """, (branch_id, date)).fetchall()

    rows = conn.execute("""
        SELECT description
        FROM cash_entries
        WHERE branch_id = ?
        AND entry_type = 'CASH_OUT'
        AND category = 'Mechanic Payout'
        AND reference_type IN ('MANUAL', 'MECHANIC_PAYOUT')
        AND COALESCE(payout_for_date, DATE(created_at)) = ?
    """, (branch_id, date)).fetchall()
    conn.close()

    return {
        "mechanic_ids": {int(row["reference_id"]) for row in mechanic_rows if row["reference_id"] is not None},
        "mechanic_names": {row["description"] for row in rows if row["description"]},
    }


def get_already_paid_mechanic_names(date, branch_id=1):
    """
    Backward-compatible helper for legacy callsites.
    """
    return get_already_paid_mechanic_identifiers(date, branch_id=branch_id)["mechanic_names"]
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────

def add_cash_entry(entry_type, amount, category, description, reference_id, payout_for_date, user_id, branch_id=1):
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

    valid_categories = CASH_IN_CATEGORIES if entry_type == 'CASH_IN' else CASH_OUT_CATEGORIES
    if category not in valid_categories:
        raise ValueError(f"Invalid category for {entry_type}.")

    normalized_reference_id = None
    if reference_id not in (None, ""):
        try:
            normalized_reference_id = int(reference_id)
        except (TypeError, ValueError):
            raise ValueError("Invalid mechanic reference.")

    reference_type = 'MANUAL'
    if category == 'Mechanic Payout' and normalized_reference_id is not None:
        reference_type = 'MECHANIC_PAYOUT'
        if payout_for_date in ("", None):
            payout_for_date = date_today.today().isoformat()
    else:
        payout_for_date = None

    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO cash_entries
                (branch_id, entry_type, amount, category, description,
                reference_type, reference_id, payout_for_date, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            branch_id,
            entry_type,
            amount,
            category,
            description or None,
            reference_type,
            normalized_reference_id,
            payout_for_date,
            user_id
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_cash_entry(entry_id, branch_id=1):
    """
    Hard deletes a manual cash entry.
    reference_type in ('MANUAL', 'MECHANIC_PAYOUT') guard means sales and debt rows
    can never be deleted through this path even if called directly.
    Admin-only enforced at route level.
    """
    conn = get_db()
    try:
        result = conn.execute("""
            DELETE FROM cash_entries
            WHERE id = ? AND branch_id = ? AND reference_type IN ('MANUAL', 'MECHANIC_PAYOUT')
        """, (entry_id, branch_id))

        if result.rowcount == 0:
            raise ValueError("Entry not found or cannot be deleted.")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# REPORT HELPER
# ─────────────────────────────────────────────

def get_cash_entries_for_report(date_from, date_to, branch_id=1):
    """
    Full unified ledger for a date range — used by the sales report PDF.
    Sorted oldest first so the PDF reads chronologically.
    """
    conn = get_db()
    sales_rows  = _get_sales_cash(conn, branch_id, date_from, date_to)
    debt_rows   = _get_debt_cash_payments(conn, branch_id, date_from, date_to)
    manual_rows = _get_manual_entries(conn, branch_id, date_from, date_to)
    conn.close()

    unified = _build_unified(sales_rows, debt_rows, manual_rows)

    # Reverse to oldest-first for PDF reading order
    unified.reverse()

    total_in  = sum(r['amount'] for r in unified if r['entry_type'] == 'CASH_IN')
    total_out = sum(r['amount'] for r in unified if r['entry_type'] == 'CASH_OUT')

    return {
        'entries':      unified,
        'total_in':     round(total_in,  2),
        'total_out':    round(total_out, 2),
        'cash_on_hand': round(total_in - total_out, 2),
    }
