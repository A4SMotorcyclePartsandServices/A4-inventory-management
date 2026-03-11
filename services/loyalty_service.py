import json
from datetime import date
from db.database import get_db
from utils.formatters import format_date


# ─────────────────────────────────────────────────────────────────────────────
# PROGRAM ADMIN
# ─────────────────────────────────────────────────────────────────────────────

def get_all_programs(branch_id=None):
    """
    Returns all loyalty programs.
    branch_id=None returns programs for all branches (global + branch-specific).
    Pass branch_id to filter for a specific branch (includes global programs).
    """
    conn = get_db()
    if branch_id is not None:
        rows = conn.execute("""
            SELECT * FROM loyalty_programs
            WHERE branch_id IS NULL OR branch_id = %s
            ORDER BY is_active DESC, period_end DESC
        """, (branch_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM loyalty_programs
            ORDER BY is_active DESC, period_end DESC
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_program(data, user_id):
    """
    Creates a new loyalty program.
    Validates: period_end must be after period_start,
               threshold must be >= 1,
               qualifying_id must exist in the correct table.
    """
    program_type  = (data.get("program_type") or "").strip().upper()
    qualifying_id = data.get("qualifying_id")
    threshold     = int(data.get("threshold") or 0)
    reward_type   = (data.get("reward_type") or "").strip().upper()
    reward_value  = float(data.get("reward_value") or 0)
    name          = (data.get("name") or "").strip()
    period_start  = data.get("period_start")
    period_end    = data.get("period_end")
    branch_id     = data.get("branch_id") or None  # None = global

    if not name:
        raise ValueError("Program name is required.")
    if program_type not in ("SERVICE", "ITEM"):
        raise ValueError("program_type must be SERVICE or ITEM.")
    if reward_type not in ("FREE_SERVICE", "FREE_ITEM", "DISCOUNT_PERCENT", "DISCOUNT_AMOUNT"):
        raise ValueError("Invalid reward_type.")
    if threshold < 1:
        raise ValueError("Threshold must be at least 1.")
    if not period_start or not period_end:
        raise ValueError("period_start and period_end are required.")
    if period_start >= period_end:
        raise ValueError("period_end must be after period_start.")

    conn = get_db()
    try:
        # Validate qualifying_id exists
        if program_type == "SERVICE":
            row = conn.execute(
                "SELECT id FROM services WHERE id = %s AND is_active = 1",
                (qualifying_id,)
            ).fetchone()
            if not row:
                raise ValueError("Invalid or inactive service selected.")
        else:
            row = conn.execute(
                "SELECT id FROM items WHERE id = %s",
                (qualifying_id,)
            ).fetchone()
            if not row:
                raise ValueError("Invalid item selected.")

        row = conn.execute("""
            INSERT INTO loyalty_programs (
                name, program_type, qualifying_id, threshold,
                reward_type, reward_value, reward_description,
                period_start, period_end, branch_id, is_active, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
            RETURNING id
        """, (
            name, program_type, qualifying_id, threshold,
            reward_type, reward_value, data.get("reward_description"),
            period_start, period_end, branch_id, user_id
        )).fetchone()
        conn.commit()
        return row["id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def toggle_program(program_id, is_active):
    """Activate or deactivate a loyalty program."""
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE loyalty_programs SET is_active = %s WHERE id = %s",
            (1 if is_active else 0, program_id)
        )
        if cursor.rowcount == 0:
            raise ValueError("Loyalty program not found.")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# STAMP LOGGING  (called from record_sale inside its transaction)
# ─────────────────────────────────────────────────────────────────────────────

def log_stamps_for_sale(sale_id, customer_id, service_ids, item_ids, sale_date, external_conn):
    """
    Called at the END of record_sale, inside the same DB transaction (external_conn).
    Finds all active programs whose qualifying_id matches a service or item in this sale,
    and whose period covers sale_date. Inserts one stamp row per matching program.

    Args:
        sale_id      (int)   : The newly created sale ID.
        customer_id  (int)   : Must be a registered customer (not a walk-in).
        service_ids  (list)  : List of service IDs on this sale.
        item_ids     (list)  : List of item IDs on this sale.
        sale_date    (str)   : ISO datetime string of the sale ("YYYY-MM-DD HH:MM:SS").
        external_conn        : The open DB connection from record_sale.

    Why external_conn:
        Stamps must live or die with the sale. If we used a separate connection,
        a crash after sale commit but before stamp commit would leave the customer
        missing a stamp they earned.
    """
    if not customer_id:
        return  # Walk-ins don't get stamps

    sale_date_only = sale_date[:10]  # "YYYY-MM-DD"

    # Find all active programs whose period covers today
    # and whose qualifying_id matches something in this sale
    service_placeholders = ",".join(["%s"] * len(service_ids)) if service_ids else "NULL"
    item_placeholders    = ",".join(["%s"] * len(item_ids))    if item_ids    else "NULL"

    params = []
    if service_ids:
        params.extend(service_ids)
    if item_ids:
        params.extend(item_ids)
    params.extend([sale_date_only, sale_date_only])

    query = f"""
        SELECT id, program_type, qualifying_id
        FROM loyalty_programs
        WHERE is_active = 1
          AND period_start <= %s
          AND period_end   >= %s
          AND (
                (program_type = 'SERVICE' AND qualifying_id IN ({service_placeholders if service_ids else 'SELECT NULL WHERE 0'}))
             OR (program_type = 'ITEM'    AND qualifying_id IN ({item_placeholders if item_ids else 'SELECT NULL WHERE 0'}))
          )
    """

    # Rebuild cleanly to avoid param order confusion
    if service_ids and item_ids:
        programs = external_conn.execute(f"""
            SELECT id, program_type, qualifying_id
            FROM loyalty_programs
            WHERE is_active = 1
              AND period_start <= %s
              AND period_end   >= %s
              AND (
                    (program_type = 'SERVICE' AND qualifying_id IN ({service_placeholders}))
                 OR (program_type = 'ITEM'    AND qualifying_id IN ({item_placeholders}))
              )
        """, [sale_date_only, sale_date_only] + service_ids + item_ids).fetchall()

    elif service_ids:
        programs = external_conn.execute(f"""
            SELECT id, program_type, qualifying_id
            FROM loyalty_programs
            WHERE is_active = 1
              AND period_start <= %s
              AND period_end   >= %s
              AND program_type = 'SERVICE'
              AND qualifying_id IN ({service_placeholders})
        """, [sale_date_only, sale_date_only] + service_ids).fetchall()

    elif item_ids:
        programs = external_conn.execute(f"""
            SELECT id, program_type, qualifying_id
            FROM loyalty_programs
            WHERE is_active = 1
              AND period_start <= %s
              AND period_end   >= %s
              AND program_type = 'ITEM'
              AND qualifying_id IN ({item_placeholders})
        """, [sale_date_only, sale_date_only] + item_ids).fetchall()

    else:
        return  # Nothing to stamp

    for prog in programs:
        external_conn.execute("""
            INSERT INTO loyalty_stamps (customer_id, program_id, sale_id, stamped_at)
            VALUES (%s, %s, %s, %s)
        """, (customer_id, prog["id"], sale_id, sale_date))
    # No commit here — caller (record_sale) owns the transaction


# ─────────────────────────────────────────────────────────────────────────────
# ELIGIBILITY CHECK  (called by OUT page banner via API)
# ─────────────────────────────────────────────────────────────────────────────

def get_customer_eligibility(customer_id, branch_id=None):
    """
    Returns a list of programs where the customer has earned enough stamps
    to redeem a reward AND hasn't redeemed yet in this period.

    Each result includes stamp count, threshold, and reward info for the banner.

    branch_id: pass the branch's ID to filter correctly.
               NULL-branch programs (global) are always included.
    """
    conn = get_db()
    today = date.today().isoformat()

    programs = conn.execute("""
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.threshold,
            lp.reward_type,
            lp.reward_value,
            lp.reward_description,
            lp.period_start,
            lp.period_end,
            lp.branch_id,
            CASE
                WHEN lp.program_type = 'SERVICE' THEN sv.name
                WHEN lp.program_type = 'ITEM' THEN it.name
                ELSE NULL
            END AS qualifying_name
        FROM loyalty_programs lp
        LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
        LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
        WHERE lp.is_active = 1
          AND lp.period_start <= %s
          AND lp.period_end   >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
    """, (today, today, branch_id)).fetchall()

    result = []
    for prog in programs:
        # Count unconsumed stamps within the program's period
        stamp_count = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM loyalty_stamps
            WHERE customer_id  = %s
              AND program_id   = %s
              AND redemption_id IS NULL
              AND stamped_at   >= %s
              AND stamped_at   < (%s::date + INTERVAL '1 day')
        """, (
            customer_id, prog["id"],
            prog["period_start"], prog["period_end"]
        )).fetchone()["cnt"]

        redemption_count = conn.execute("""
            SELECT COUNT(*) AS cnt
            FROM loyalty_redemptions
            WHERE customer_id = %s
              AND program_id = %s
              AND DATE(redeemed_at) >= %s
              AND DATE(redeemed_at) <= %s
        """, (
            customer_id, prog["id"],
            prog["period_start"], prog["period_end"]
        )).fetchone()["cnt"]

        result.append({
            "program_id":          prog["id"],
            "name":                prog["name"],
            "program_type":        prog["program_type"],
            "qualifying_id":       prog["qualifying_id"],
            "qualifying_name":     prog["qualifying_name"],
            "threshold":           prog["threshold"],
            "stamp_count":         stamp_count,
            "stamps_remaining":    max(0, prog["threshold"] - stamp_count),
            "is_eligible":         stamp_count >= prog["threshold"],
            "redemption_count":    redemption_count,
            "reward_type":         prog["reward_type"],
            "reward_value":        prog["reward_value"],
            "reward_description":  prog["reward_description"],
            "period_end":          prog["period_end"],
        })

    conn.close()
    # Put eligible programs first, then by stamps remaining ascending
    result.sort(key=lambda x: (not x["is_eligible"], x["stamps_remaining"]))
    return result


def get_customer_eligibility_bulk(customer_ids, branch_id=None):
    """
    Bulk variant of get_customer_eligibility used by customer list pages.
    Returns {customer_id: [program eligibility rows]}.
    """
    normalized_ids = sorted({int(cid) for cid in (customer_ids or []) if cid})
    if not normalized_ids:
        return {}

    conn = get_db()
    today = date.today().isoformat()

    programs = conn.execute("""
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.threshold,
            lp.reward_type,
            lp.reward_value,
            lp.reward_description,
            lp.period_start,
            lp.period_end,
            lp.branch_id,
            CASE
                WHEN lp.program_type = 'SERVICE' THEN sv.name
                WHEN lp.program_type = 'ITEM' THEN it.name
                ELSE NULL
            END AS qualifying_name
        FROM loyalty_programs lp
        LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
        LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
        WHERE lp.is_active = 1
          AND lp.period_start <= %s
          AND lp.period_end >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
    """, (today, today, branch_id)).fetchall()

    by_customer = {cid: [] for cid in normalized_ids}
    if not programs:
        conn.close()
        return by_customer

    for prog in programs:
        stamp_rows = conn.execute("""
            SELECT customer_id, COUNT(*) AS cnt
            FROM loyalty_stamps
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND redemption_id IS NULL
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            GROUP BY customer_id
        """, (
            normalized_ids, prog["id"],
            prog["period_start"], prog["period_end"]
        )).fetchall()
        stamp_map = {int(row["customer_id"]): int(row["cnt"]) for row in stamp_rows}

        redemption_rows = conn.execute("""
            SELECT customer_id, COUNT(*) AS cnt
            FROM loyalty_redemptions
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND DATE(redeemed_at) >= %s
              AND DATE(redeemed_at) <= %s
            GROUP BY customer_id
        """, (
            normalized_ids, prog["id"],
            prog["period_start"], prog["period_end"]
        )).fetchall()
        redemption_map = {int(row["customer_id"]): int(row["cnt"]) for row in redemption_rows}

        for customer_id in normalized_ids:
            stamp_count = stamp_map.get(customer_id, 0)
            by_customer[customer_id].append({
                "program_id": prog["id"],
                "name": prog["name"],
                "program_type": prog["program_type"],
                "qualifying_id": prog["qualifying_id"],
                "qualifying_name": prog["qualifying_name"],
                "threshold": prog["threshold"],
                "stamp_count": stamp_count,
                "stamps_remaining": max(0, prog["threshold"] - stamp_count),
                "is_eligible": stamp_count >= prog["threshold"],
                "redemption_count": redemption_map.get(customer_id, 0),
                "reward_type": prog["reward_type"],
                "reward_value": prog["reward_value"],
                "reward_description": prog["reward_description"],
                "period_end": prog["period_end"],
            })

    conn.close()

    for customer_id in normalized_ids:
        by_customer[customer_id].sort(key=lambda x: (not x["is_eligible"], x["stamps_remaining"]))

    return by_customer


# ─────────────────────────────────────────────────────────────────────────────
# REDEMPTION  (atomic — eligibility check + redemption in one transaction)
# ─────────────────────────────────────────────────────────────────────────────

def redeem_reward(customer_id, program_id, sale_id, user_id):
    """
    Atomically:
      1. Re-checks eligibility (inside the transaction — prevents race conditions).
      2. Inserts a loyalty_redemptions row.
      3. Marks all currently active stamps in that program period as consumed.

    Returns the redemption dict on success.
    Raises ValueError if not eligible (already redeemed, not enough stamps, etc).

    Why atomic:
        Two staff on different terminals could both see the eligibility banner.
        Without an atomic check+redeem, both could approve the reward.
        By re-checking inside BEGIN...COMMIT in one transaction, only one
        redemption can consume the same active stamps.
    """
    conn = get_db()
    today = date.today().isoformat()

    try:
        conn.execute("BEGIN")

        # 1. Re-check program is still active and in period
        prog = conn.execute("""
            SELECT id, name, threshold, reward_type, reward_value,
                   reward_description, period_start, period_end
            FROM loyalty_programs
            WHERE id = %s AND is_active = 1
              AND period_start <= %s AND period_end >= %s
        """, (program_id, today, today)).fetchone()

        if not prog:
            raise ValueError("This loyalty program is no longer active or has expired.")

        # 2. Re-count unconsumed stamps in period (the race-condition guard)
        eligible_stamps = conn.execute("""
            SELECT id FROM loyalty_stamps
            WHERE customer_id  = %s
              AND program_id   = %s
              AND redemption_id IS NULL
              AND stamped_at   >= %s
              AND stamped_at   < (%s::date + INTERVAL '1 day')
            ORDER BY stamped_at ASC
        """, (
            customer_id, program_id,
            prog["period_start"], prog["period_end"]
        )).fetchall()

        if len(eligible_stamps) < prog["threshold"]:
            raise ValueError(
                f"Not enough stamps to redeem. "
                f"Need {prog['threshold']}, found {len(eligible_stamps)}."
            )

        # 3. Insert redemption row first (need its ID to link stamps)
        reward_snapshot = json.dumps({
            "reward_type":        prog["reward_type"],
            "reward_value":       prog["reward_value"],
            "reward_description": prog["reward_description"],
            "program_name":       prog["name"],
            "redeemed_on":        today,
        })

        redemption_row = conn.execute("""
            INSERT INTO loyalty_redemptions (
                customer_id, program_id, applied_on_sale_id,
                redeemed_by, reward_snapshot, stamps_consumed
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            customer_id, program_id, sale_id,
            user_id, reward_snapshot, len(eligible_stamps)
        )).fetchone()
        redemption_id = redemption_row["id"]

        # 4. Mark all active stamps in this period as consumed (hard reset to zero)
        stamp_ids = [row["id"] for row in eligible_stamps]
        conn.executemany("""
            UPDATE loyalty_stamps SET redemption_id = %s WHERE id = %s
        """, [(redemption_id, sid) for sid in stamp_ids])

        conn.commit()
        return {
            "redemption_id":     redemption_id,
            "program_name":      prog["name"],
            "reward_type":       prog["reward_type"],
            "reward_value":      prog["reward_value"],
            "reward_description": prog["reward_description"],
            "stamps_consumed":   len(eligible_stamps),
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY  (for customer profile page)
# ─────────────────────────────────────────────────────────────────────────────

def get_customer_loyalty_summary(customer_id):
    """
    Returns stamp progress per active program + full redemption history.
    Used in the customer profile / customers_list detail panel.
    """
    eligibility = get_customer_eligibility(customer_id)

    conn = get_db()
    redemptions = conn.execute("""
        SELECT
            r.id,
            r.redeemed_at,
            r.stamps_consumed,
            r.reward_snapshot,
            lp.name AS program_name,
            s.sales_number
        FROM loyalty_redemptions r
        JOIN loyalty_programs lp ON lp.id = r.program_id
        JOIN sales s ON s.id = r.applied_on_sale_id
        WHERE r.customer_id = %s
        ORDER BY r.redeemed_at DESC
    """, (customer_id,)).fetchall()
    conn.close()

    history = []
    for r in redemptions:
        snapshot = {}
        try:
            snapshot = json.loads(r["reward_snapshot"])
        except Exception:
            pass
        history.append({
            "redemption_id":     r["id"],
            "program_name":      r["program_name"],
            "sales_number":      r["sales_number"],
            "redeemed_at":       r["redeemed_at"],
            "redeemed_at_display": format_date(r["redeemed_at"], show_time=True),
            "stamps_consumed":   r["stamps_consumed"],
            "reward_description": snapshot.get("reward_description", ""),
        })

    return {
        "programs": eligibility,
        "redemption_history": history,
    }



