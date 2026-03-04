from db.database import get_db
from utils.formatters import format_date

# --- CATEGORIES ---
# Hardcoded for now. Future: move to a DB table if client wants to manage them.
CASH_IN_CATEGORIES  = ['Petty Cash', 'Owner Deposit', 'Other Income']
CASH_OUT_CATEGORIES = ['Parts Purchase', 'Staff Expense', 'Utilities', 'Supplies', 'Other Expense']


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

def get_cash_summary(branch_id=1):
    """
    Returns total CASH_IN, total CASH_OUT, and computed cash on hand for a branch.

    branch_id defaults to 1 (current only branch).
    When multi-branch is needed, callers pass the correct branch_id.

    Future: when sales cash integration is confirmed by client,
    add a second query here for reference_type = 'SALE' and sum it in.
    """
    conn = get_db()

    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN entry_type = 'CASH_IN'  THEN amount ELSE 0 END), 0) AS total_in,
            COALESCE(SUM(CASE WHEN entry_type = 'CASH_OUT' THEN amount ELSE 0 END), 0) AS total_out
        FROM cash_entries
        WHERE branch_id = ?
        AND reference_type = 'MANUAL'
    """, (branch_id,)).fetchone()

    conn.close()

    total_in  = round(row['total_in'],  2)
    total_out = round(row['total_out'], 2)

    return {
        'total_in':     total_in,
        'total_out':    total_out,
        'cash_on_hand': round(total_in - total_out, 2),
    }


def get_cash_entries(branch_id=1, limit=None):
    """
    Returns all cash entries for a branch, newest first.
    Optional limit for dashboard previews.
    """
    conn = get_db()

    query = """
        SELECT
            ce.id,
            ce.entry_type,
            ce.amount,
            ce.category,
            ce.description,
            ce.reference_type,
            ce.reference_id,
            ce.created_at,
            u.username AS recorded_by
        FROM cash_entries ce
        LEFT JOIN users u ON u.id = ce.user_id
        WHERE ce.branch_id = ?
        ORDER BY ce.created_at DESC
    """

    params = (branch_id,)

    if limit:
        query += " LIMIT ?"
        params = (branch_id, limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d['created_at'] = format_date(d['created_at'], show_time=True)
        result.append(d)

    return result


# ─────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────

def add_cash_entry(entry_type, amount, category, description, user_id, branch_id=1):
    """
    Records a single petty cash movement (MANUAL only for now).

    entry_type  : 'CASH_IN' or 'CASH_OUT'
    amount      : positive float
    category    : must be in the appropriate category list
    description : free text, optional but encouraged
    user_id     : the logged-in user recording this
    branch_id   : defaults to 1, ready for multi-branch

    Raises ValueError for bad input — caller (route) handles the HTTP response.
    """
    # --- Validation ---
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

    # --- Insert ---
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO cash_entries
                (branch_id, entry_type, amount, category, description,
                reference_type, reference_id, user_id)
            VALUES (?, ?, ?, ?, ?, 'MANUAL', NULL, ?)
        """, (branch_id, entry_type, amount, category, description or None, user_id))

        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_cash_entry(entry_id, branch_id=1):
    """
    Hard delete a cash entry.

    branch_id guard prevents a user from deleting entries from another branch.
    Only admins should be able to call this — enforce that in the route, not here.

    Future: consider soft delete (is_deleted flag) before going live
    if the client wants an audit trail of deletions.
    """
    conn = get_db()
    try:
        result = conn.execute("""
            DELETE FROM cash_entries
            WHERE id = ? AND branch_id = ?
        """, (entry_id, branch_id))

        if result.rowcount == 0:
            raise ValueError("Entry not found or does not belong to this branch.")

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
    Returns entries and summary for a given date range.
    Used by the sales report PDF section.
    """
    conn = get_db()

    rows = conn.execute("""
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
        AND DATE(ce.created_at) BETWEEN DATE(?) AND DATE(?)
        ORDER BY ce.created_at ASC
    """, (branch_id, date_from, date_to)).fetchall()

    conn.close()

    entries = []
    total_in  = 0.0
    total_out = 0.0

    for row in rows:
        d = dict(row)
        d['created_at'] = format_date(d['created_at'], show_time=True)
        entries.append(d)

        if d['entry_type'] == 'CASH_IN':
            total_in  += d['amount']
        else:
            total_out += d['amount']

    return {
        'entries':      entries,
        'total_in':     round(total_in,  2),
        'total_out':    round(total_out, 2),
        'cash_on_hand': round(total_in - total_out, 2),
    }