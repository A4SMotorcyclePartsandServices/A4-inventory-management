import json
from datetime import date, datetime

from db.database import get_db
from utils.formatters import format_date
from utils.timezone import today_local

POINT_RULE_MODES = {"FIRST_MATCH", "STACK_ALL", "ADVANCED"}


def _expire_elapsed_programs(conn):
    conn.execute(
        """
        UPDATE loyalty_programs
        SET is_active = 0
        WHERE is_active = 1
          AND period_end < CURRENT_DATE
        """
    )


def _to_bool_int(value, default=False):
    if value is None:
        return 1 if default else 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if int(value) != 0 else 0
    text = str(value).strip().lower()
    return 1 if text in ("1", "true", "yes", "on") else 0


def _json_safe_reward_value(value):
    return float(value or 0)


def _normalize_point_rule_mode(value):
    mode = str(value or "ADVANCED").strip().upper()
    if mode not in POINT_RULE_MODES:
        raise ValueError("point_rule_mode must be FIRST_MATCH, STACK_ALL, or ADVANCED.")
    return mode


def _normalize_point_rules(raw_rules):
    if raw_rules is None:
        return []
    if not isinstance(raw_rules, list):
        raise ValueError("point_rules must be a list.")

    if len(raw_rules) > 50:
        raise ValueError("Too many point rules. Maximum is 50.")

    normalized = []
    for idx, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise ValueError("Each point rule must be an object.")

        try:
            points = int(raw.get("points") or 0)
        except (TypeError, ValueError):
            raise ValueError("Each point rule must have a valid integer points value.")
        if points <= 0:
            continue

        try:
            service_id = int(raw["service_id"]) if raw.get("service_id") not in (None, "") else None
            item_id = int(raw["item_id"]) if raw.get("item_id") not in (None, "") else None
            priority = int(raw.get("priority") or ((idx + 1) * 10))
        except (TypeError, ValueError):
            raise ValueError("Point rule IDs and priority must be valid integers.")

        if service_id is not None and service_id <= 0:
            raise ValueError("Point rule service_id must be greater than 0.")
        if item_id is not None and item_id <= 0:
            raise ValueError("Point rule item_id must be greater than 0.")
        if priority < 1 or priority > 10000:
            raise ValueError("Point rule priority must be between 1 and 10000.")

        rule = {
            "rule_name": (raw.get("rule_name") or "").strip() or None,
            "points": points,
            "service_id": service_id,
            "item_id": item_id,
            "requires_any_item": _to_bool_int(raw.get("requires_any_item"), default=False),
            "requires_any_service": _to_bool_int(raw.get("requires_any_service"), default=False),
            "priority": priority,
            "stop_on_match": _to_bool_int(raw.get("stop_on_match"), default=False),
        }

        has_condition = any([
            rule["service_id"] is not None,
            rule["item_id"] is not None,
            rule["requires_any_item"] == 1,
            rule["requires_any_service"] == 1,
        ])
        if not has_condition:
            raise ValueError("Each point rule must define at least one condition.")

        normalized.append(rule)

    return normalized


def get_all_programs(branch_id=None, include_rules=True):
    conn = get_db()
    _expire_elapsed_programs(conn)

    if branch_id is not None:
        rows = conn.execute(
            """
            SELECT
                lp.*,
                CASE WHEN lp.period_end < CURRENT_DATE THEN 1 ELSE 0 END AS is_expired,
                CASE lp.program_type
                    WHEN 'SERVICE' THEN sv.name
                    WHEN 'ITEM' THEN it.name
                    ELSE NULL
                END AS qualifying_name
            FROM loyalty_programs lp
            LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
            LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
            WHERE lp.branch_id IS NULL OR lp.branch_id = %s
            ORDER BY lp.is_active DESC, lp.period_end DESC
            """,
            (branch_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT
                lp.*,
                CASE WHEN lp.period_end < CURRENT_DATE THEN 1 ELSE 0 END AS is_expired,
                CASE lp.program_type
                    WHEN 'SERVICE' THEN sv.name
                    WHEN 'ITEM' THEN it.name
                    ELSE NULL
                END AS qualifying_name
            FROM loyalty_programs lp
            LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
            LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
            ORDER BY lp.is_active DESC, lp.period_end DESC
            """
        ).fetchall()

    programs = [dict(r) for r in rows]

    if include_rules and programs:
        program_ids = [int(p["id"]) for p in programs]
        rule_rows = conn.execute(
            """
            SELECT
                lpr.id,
                lpr.program_id,
                lpr.rule_name,
                lpr.points,
                lpr.service_id,
                lpr.item_id,
                lpr.requires_any_item,
                lpr.requires_any_service,
                lpr.priority,
                lpr.stop_on_match,
                lpr.is_active,
                sv.name AS service_name,
                it.name AS item_name
            FROM loyalty_point_rules lpr
            LEFT JOIN services sv ON sv.id = lpr.service_id
            LEFT JOIN items it ON it.id = lpr.item_id
            WHERE lpr.program_id = ANY(%s)
            ORDER BY priority ASC, id ASC
            """,
            (program_ids,),
        ).fetchall()

        rules_by_program = {pid: [] for pid in program_ids}
        for row in rule_rows:
            rules_by_program[int(row["program_id"])].append(dict(row))

        for p in programs:
            p["point_rules"] = rules_by_program.get(int(p["id"]), [])

    conn.close()
    return programs


def create_program(data, user_id):
    program_type = (data.get("program_type") or "").strip().upper()
    try:
        qualifying_id = int(data.get("qualifying_id"))
    except (TypeError, ValueError):
        raise ValueError("qualifying_id must be a valid integer.")

    try:
        threshold = int(data.get("threshold") or 0)
    except (TypeError, ValueError):
        raise ValueError("threshold must be a valid integer.")
    try:
        points_threshold = int(data.get("points_threshold") or 0)
    except (TypeError, ValueError):
        raise ValueError("points_threshold must be a valid integer.")

    reward_basis = (data.get("reward_basis") or "STAMPS").strip().upper()
    program_mode = (data.get("program_mode") or "REDEEMABLE").strip().upper()
    point_rule_mode = _normalize_point_rule_mode(data.get("point_rule_mode"))
    reward_type = (data.get("reward_type") or "DISCOUNT_AMOUNT").strip().upper()
    try:
        reward_value = float(data.get("reward_value") or 0)
    except (TypeError, ValueError):
        raise ValueError("reward_value must be numeric.")

    name = (data.get("name") or "").strip()
    period_start = data.get("period_start")
    period_end = data.get("period_end")
    branch_id_raw = data.get("branch_id")
    if branch_id_raw in (None, ""):
        branch_id = None
    else:
        try:
            branch_id = int(branch_id_raw)
        except (TypeError, ValueError):
            raise ValueError("branch_id must be a valid integer or null.")

    stamp_enabled = _to_bool_int(data.get("stamp_enabled"), default=True)
    points_enabled = _to_bool_int(data.get("points_enabled"), default=False)
    point_rules = _normalize_point_rules(data.get("point_rules"))

    if not name:
        raise ValueError("Program name is required.")
    if program_type not in ("SERVICE", "ITEM"):
        raise ValueError("program_type must be SERVICE or ITEM.")
    if qualifying_id <= 0:
        raise ValueError("qualifying_id must be greater than 0.")
    if reward_basis not in ("STAMPS", "POINTS", "STAMPS_OR_POINTS"):
        raise ValueError("reward_basis must be STAMPS, POINTS, or STAMPS_OR_POINTS.")
    if program_mode not in ("REDEEMABLE", "EARN_ONLY"):
        raise ValueError("program_mode must be REDEEMABLE or EARN_ONLY.")
    if reward_type not in ("NONE", "FREE_SERVICE", "FREE_ITEM", "DISCOUNT_PERCENT", "DISCOUNT_AMOUNT", "RAFFLE_ENTRY"):
        raise ValueError("Invalid reward_type.")
    if not period_start or not period_end:
        raise ValueError("period_start and period_end are required.")
    try:
        period_start_date = datetime.strptime(period_start, "%Y-%m-%d").date()
        period_end_date = datetime.strptime(period_end, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValueError("period_start and period_end must be valid YYYY-MM-DD dates.")
    if period_start_date >= period_end_date:
        raise ValueError("period_end must be after period_start.")
    if not stamp_enabled and not points_enabled:
        raise ValueError("Enable at least one earning mode: stamps or points.")
    if points_enabled and not point_rules:
        raise ValueError("At least one valid point rule is required when points are enabled.")
    if not points_enabled:
        point_rule_mode = "ADVANCED"
    if points_threshold < 0:
        raise ValueError("points_threshold cannot be negative.")
    if program_mode == "REDEEMABLE":
        if stamp_enabled and threshold < 1:
            raise ValueError("Threshold must be at least 1 when stamps are enabled.")
        if reward_basis in ("STAMPS", "STAMPS_OR_POINTS") and not stamp_enabled:
            raise ValueError("Stamp-based rewards require stamps mode to be enabled.")
        if reward_basis in ("POINTS", "STAMPS_OR_POINTS") and not points_enabled:
            raise ValueError("Points-based rewards require points mode to be enabled.")
        if reward_basis == "POINTS" and points_threshold < 1:
            raise ValueError("points_threshold must be at least 1 for POINTS reward basis.")
        if reward_basis == "STAMPS_OR_POINTS" and threshold < 1 and points_threshold < 1:
            raise ValueError("STAMPS_OR_POINTS requires at least one active threshold.")
        if reward_type == "NONE":
            raise ValueError("REDEEMABLE programs must have a reward type.")
    else:
        reward_type = "NONE"
        reward_value = 0
        if stamp_enabled and threshold < 0:
            raise ValueError("Threshold cannot be negative.")
        if points_enabled and points_threshold < 0:
            raise ValueError("points_threshold cannot be negative.")

    conn = get_db()
    try:
        if program_type == "SERVICE":
            row = conn.execute(
                "SELECT id FROM services WHERE id = %s AND is_active = 1",
                (qualifying_id,),
            ).fetchone()
            if not row:
                raise ValueError("Invalid or inactive service selected.")
        else:
            row = conn.execute(
                "SELECT id FROM items WHERE id = %s",
                (qualifying_id,),
            ).fetchone()
            if not row:
                raise ValueError("Invalid item selected.")

        for rule in point_rules:
            if rule["service_id"] is not None:
                svc = conn.execute(
                    "SELECT id FROM services WHERE id = %s AND is_active = 1",
                    (rule["service_id"],),
                ).fetchone()
                if not svc:
                    raise ValueError("One of the point rules references an invalid or inactive service.")

            if rule["item_id"] is not None:
                itm = conn.execute(
                    "SELECT id FROM items WHERE id = %s",
                    (rule["item_id"],),
                ).fetchone()
                if not itm:
                    raise ValueError("One of the point rules references an invalid item.")

        threshold_to_save = threshold if stamp_enabled else 0

        row = conn.execute(
            """
            INSERT INTO loyalty_programs (
                name, program_type, qualifying_id, threshold, points_threshold, reward_basis,
                point_rule_mode,
                program_mode,
                reward_type, reward_value, reward_description,
                period_start, period_end, branch_id,
                stamp_enabled, points_enabled,
                is_active, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
            RETURNING id
            """,
            (
                name,
                program_type,
                qualifying_id,
                threshold_to_save,
                points_threshold if points_enabled else 0,
                reward_basis,
                point_rule_mode,
                program_mode,
                reward_type,
                reward_value,
                data.get("reward_description"),
                period_start,
                period_end,
                branch_id,
                stamp_enabled,
                points_enabled,
                user_id,
            ),
        ).fetchone()
        new_program_id = int(row["id"])

        if points_enabled and point_rules:
            conn.executemany(
                """
                INSERT INTO loyalty_point_rules (
                    program_id, rule_name, points,
                    service_id, item_id,
                    requires_any_item, requires_any_service,
                    priority, stop_on_match, is_active
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)
                """,
                [
                    (
                        new_program_id,
                        r["rule_name"],
                        r["points"],
                        r["service_id"],
                        r["item_id"],
                        r["requires_any_item"],
                        r["requires_any_service"],
                        r["priority"],
                        r["stop_on_match"],
                    )
                    for r in point_rules
                ],
            )

        conn.commit()
        return new_program_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def toggle_program(program_id, is_active):
    conn = get_db()
    try:
        _expire_elapsed_programs(conn)

        row = conn.execute(
            """
            SELECT id, name, period_end, is_active
            FROM loyalty_programs
            WHERE id = %s
            """,
            (program_id,),
        ).fetchone()
        if not row:
            raise ValueError("Loyalty program not found.")

        if is_active and row["period_end"] and row["period_end"] < today_local():
            conn.execute(
                "UPDATE loyalty_programs SET is_active = 0 WHERE id = %s",
                (program_id,),
            )
            conn.commit()
            raise ValueError("Expired loyalty programs cannot be reactivated.")

        cursor = conn.execute(
            "UPDATE loyalty_programs SET is_active = %s WHERE id = %s",
            (1 if is_active else 0, program_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def extend_program_period(program_id, new_period_end):
    conn = get_db()
    try:
        _expire_elapsed_programs(conn)

        row = conn.execute(
            """
            SELECT id, name, period_start, period_end, is_active
            FROM loyalty_programs
            WHERE id = %s
            """,
            (program_id,),
        ).fetchone()
        if not row:
            raise ValueError("Loyalty program not found.")

        if not new_period_end:
            raise ValueError("New period end date is required.")

        try:
            new_end_date = datetime.strptime(str(new_period_end), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            raise ValueError("New period end date must be a valid YYYY-MM-DD date.")

        current_start = row["period_start"]
        current_end = row["period_end"]
        today = today_local()

        if current_end and current_end < today:
            raise ValueError("Expired loyalty programs can no longer be extended.")
        if current_start and new_end_date <= current_start:
            raise ValueError("New period end must be after the fixed period start date.")
        if current_end and new_end_date < current_end:
            raise ValueError("Period end can only be extended, not shortened.")
        if current_end and new_end_date == current_end:
            raise ValueError("Choose a later end date to extend this program.")

        conn.execute(
            """
            UPDATE loyalty_programs
            SET period_end = %s
            WHERE id = %s
            """,
            (new_end_date, program_id),
        )
        conn.commit()

        refreshed = conn.execute(
            """
            SELECT id, name, period_start, period_end
            FROM loyalty_programs
            WHERE id = %s
            """,
            (program_id,),
        ).fetchone()
        return dict(refreshed)
    finally:
        conn.close()


def _rule_matches(rule, service_ids_set, item_ids_set):
    if rule["service_id"] is not None and int(rule["service_id"]) not in service_ids_set:
        return False
    if rule["item_id"] is not None and int(rule["item_id"]) not in item_ids_set:
        return False
    if int(rule["requires_any_item"] or 0) == 1 and not item_ids_set:
        return False
    if int(rule["requires_any_service"] or 0) == 1 and not service_ids_set:
        return False
    return True


def _compute_progress(stamp_count, stamp_threshold, points_balance, points_threshold, reward_basis):
    reward_basis = (reward_basis or "STAMPS").upper()

    if reward_basis == "POINTS":
        current = points_balance
        threshold = points_threshold
        unit = "points"
    elif reward_basis == "STAMPS_OR_POINTS":
        stamp_ratio = (stamp_count / stamp_threshold) if stamp_threshold > 0 else 0
        points_ratio = (points_balance / points_threshold) if points_threshold > 0 else 0
        if points_ratio > stamp_ratio:
            current = points_balance
            threshold = points_threshold
            unit = "points"
        else:
            current = stamp_count
            threshold = stamp_threshold
            unit = "stamps"
    else:
        current = stamp_count
        threshold = stamp_threshold
        unit = "stamps"

    if threshold <= 0:
        return current, threshold, 0, unit

    remaining = max(0, threshold - current)
    return current, threshold, remaining, unit


def _is_eligible(stamp_count, stamp_threshold, points_balance, points_threshold, reward_basis, stamp_enabled, points_enabled):
    reward_basis = (reward_basis or "STAMPS").upper()
    stamps_ok = bool(stamp_enabled) and stamp_threshold > 0 and stamp_count >= stamp_threshold
    points_ok = bool(points_enabled) and points_threshold > 0 and points_balance >= points_threshold

    if reward_basis == "POINTS":
        return points_ok
    if reward_basis == "STAMPS_OR_POINTS":
        return stamps_ok or points_ok
    return stamps_ok


def log_stamps_for_sale(sale_id, customer_id, service_ids, item_ids, sale_date, external_conn):
    """
    Backward-compatible hook called by record_sale.
    It now processes BOTH stamps and points in one transaction.
    """
    if not customer_id:
        return

    service_ids = [int(sid) for sid in (service_ids or []) if sid]
    item_ids = [int(iid) for iid in (item_ids or []) if iid]

    if not service_ids and not item_ids:
        return

    service_ids_set = set(service_ids)
    item_ids_set = set(item_ids)
    sale_date_only = str(sale_date)[:10]

    programs = external_conn.execute(
        """
        SELECT
            id,
            program_type,
            qualifying_id,
            COALESCE(point_rule_mode, 'ADVANCED') AS point_rule_mode,
            COALESCE(stamp_enabled, 1) AS stamp_enabled,
            COALESCE(points_enabled, 0) AS points_enabled
        FROM loyalty_programs
        WHERE is_active = 1
          AND period_start <= %s
          AND period_end >= %s
        """,
        (sale_date_only, sale_date_only),
    ).fetchall()

    if not programs:
        return

    point_program_ids = [int(p["id"]) for p in programs if int(p["points_enabled"] or 0) == 1]
    rules_by_program = {pid: [] for pid in point_program_ids}

    if point_program_ids:
        rule_rows = external_conn.execute(
            """
            SELECT
                id,
                program_id,
                rule_name,
                points,
                service_id,
                item_id,
                requires_any_item,
                requires_any_service,
                priority,
                stop_on_match,
                is_active
            FROM loyalty_point_rules
            WHERE is_active = 1
              AND program_id = ANY(%s)
            ORDER BY priority ASC, id ASC
            """,
            (point_program_ids,),
        ).fetchall()

        for row in rule_rows:
            rules_by_program[int(row["program_id"])].append(dict(row))

    for program in programs:
        program_id = int(program["id"])
        point_rule_mode = str(program["point_rule_mode"] or "ADVANCED").upper()
        qualifies_on_stamp = False

        if int(program["stamp_enabled"] or 0) == 1:
            if program["program_type"] == "SERVICE":
                qualifies_on_stamp = int(program["qualifying_id"]) in service_ids_set
            elif program["program_type"] == "ITEM":
                qualifies_on_stamp = int(program["qualifying_id"]) in item_ids_set

            if qualifies_on_stamp:
                external_conn.execute(
                    """
                    INSERT INTO loyalty_stamps (customer_id, program_id, sale_id, stamped_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (customer_id, program_id, sale_id, sale_date),
                )

        if int(program["points_enabled"] or 0) != 1:
            continue

        for rule in rules_by_program.get(program_id, []):
            if not _rule_matches(rule, service_ids_set, item_ids_set):
                continue

            external_conn.execute(
                """
                INSERT INTO loyalty_point_ledger (
                    customer_id, program_id, rule_id, sale_id, points, awarded_at, note
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (customer_id, program_id, sale_id, rule_id) DO NOTHING
                """,
                (
                    customer_id,
                    program_id,
                    int(rule["id"]),
                    sale_id,
                    int(rule["points"]),
                    sale_date,
                    rule.get("rule_name") or "Rule match",
                ),
            )

            if point_rule_mode == "FIRST_MATCH" or (point_rule_mode == "ADVANCED" and int(rule.get("stop_on_match") or 0) == 1):
                break


def get_customer_eligibility(customer_id, branch_id=None):
    conn = get_db()
    today = today_local().isoformat()

    programs = conn.execute(
        """
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.threshold,
            lp.points_threshold,
            lp.reward_basis,
            lp.program_mode,
            COALESCE(lp.stamp_enabled, 1) AS stamp_enabled,
            COALESCE(lp.points_enabled, 0) AS points_enabled,
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
          AND COALESCE(lp.program_mode, 'REDEEMABLE') = 'REDEEMABLE'
          AND (COALESCE(lp.stamp_enabled, 1) = 1 OR COALESCE(lp.points_enabled, 0) = 1)
          AND lp.period_start <= %s
          AND lp.period_end >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
        """,
        (today, today, branch_id),
    ).fetchall()

    result = []
    for prog in programs:
        stamp_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM loyalty_stamps
            WHERE customer_id = %s
              AND program_id = %s
              AND redemption_id IS NULL
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            """,
            (customer_id, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchone()["cnt"]

        points_balance = conn.execute(
            """
            SELECT COALESCE(SUM(points), 0) AS total_points
            FROM loyalty_point_ledger
            WHERE customer_id = %s
              AND program_id = %s
              AND redemption_id IS NULL
              AND awarded_at >= %s
              AND awarded_at < (%s::date + INTERVAL '1 day')
            """,
            (customer_id, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchone()["total_points"]

        redemption_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM loyalty_redemptions
            WHERE customer_id = %s
              AND program_id = %s
              AND DATE(redeemed_at) >= %s
              AND DATE(redeemed_at) <= %s
            """,
            (customer_id, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchone()["cnt"]

        threshold = int(prog["threshold"] or 0)
        points_threshold = int(prog["points_threshold"] or 0)
        stamp_count = int(stamp_count or 0)
        points_balance = int(points_balance or 0)
        reward_basis = (prog["reward_basis"] or "STAMPS").upper()
        program_mode = (prog["program_mode"] or "REDEEMABLE").upper()
        stamp_enabled = int(prog["stamp_enabled"] or 0) == 1
        points_enabled = int(prog["points_enabled"] or 0) == 1
        is_eligible = False if program_mode == "EARN_ONLY" else _is_eligible(
            stamp_count=stamp_count,
            stamp_threshold=threshold,
            points_balance=points_balance,
            points_threshold=points_threshold,
            reward_basis=reward_basis,
            stamp_enabled=stamp_enabled,
            points_enabled=points_enabled,
        )
        progress_current, progress_threshold, progress_remaining, progress_unit = _compute_progress(
            stamp_count=stamp_count,
            stamp_threshold=threshold,
            points_balance=points_balance,
            points_threshold=points_threshold,
            reward_basis=reward_basis,
        )

        result.append(
            {
                "program_id": prog["id"],
                "name": prog["name"],
                "program_type": prog["program_type"],
                "qualifying_id": prog["qualifying_id"],
                "qualifying_name": prog["qualifying_name"],
                "threshold": threshold,
                "points_threshold": points_threshold,
                "reward_basis": reward_basis,
                "program_mode": program_mode,
                "stamp_enabled": stamp_enabled,
                "points_enabled": points_enabled,
                "stamp_count": stamp_count,
                "points_balance": points_balance,
                "stamps_remaining": max(0, threshold - stamp_count),
                "points_remaining": max(0, points_threshold - points_balance),
                "is_eligible": is_eligible,
                "progress_current": progress_current,
                "progress_threshold": progress_threshold,
                "progress_remaining": progress_remaining,
                "progress_unit": progress_unit,
                "redemption_count": int(redemption_count or 0),
                "reward_type": prog["reward_type"],
                "reward_value": _json_safe_reward_value(prog["reward_value"]),
                "reward_description": prog["reward_description"],
                "period_end": prog["period_end"],
            }
        )

    conn.close()
    result.sort(key=lambda x: (not x["is_eligible"], x["progress_remaining"], x["name"]))
    return result


def get_customer_eligibility_bulk(customer_ids, branch_id=None):
    normalized_ids = sorted({int(cid) for cid in (customer_ids or []) if cid})
    if not normalized_ids:
        return {}

    conn = get_db()
    today = today_local().isoformat()

    programs = conn.execute(
        """
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.threshold,
            lp.points_threshold,
            lp.reward_basis,
            lp.program_mode,
            COALESCE(lp.stamp_enabled, 1) AS stamp_enabled,
            COALESCE(lp.points_enabled, 0) AS points_enabled,
            lp.reward_type,
            lp.reward_value,
            lp.reward_description,
            lp.period_start,
            lp.period_end,
            CASE
                WHEN lp.program_type = 'SERVICE' THEN sv.name
                WHEN lp.program_type = 'ITEM' THEN it.name
                ELSE NULL
            END AS qualifying_name
        FROM loyalty_programs lp
        LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
        LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
        WHERE lp.is_active = 1
          AND COALESCE(lp.program_mode, 'REDEEMABLE') = 'REDEEMABLE'
          AND (COALESCE(lp.stamp_enabled, 1) = 1 OR COALESCE(lp.points_enabled, 0) = 1)
          AND lp.period_start <= %s
          AND lp.period_end >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
        """,
        (today, today, branch_id),
    ).fetchall()

    by_customer = {cid: [] for cid in normalized_ids}
    if not programs:
        conn.close()
        return by_customer

    for prog in programs:
        stamp_rows = conn.execute(
            """
            SELECT customer_id, COUNT(*) AS cnt
            FROM loyalty_stamps
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND redemption_id IS NULL
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            GROUP BY customer_id
            """,
            (normalized_ids, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchall()
        stamp_map = {int(row["customer_id"]): int(row["cnt"]) for row in stamp_rows}

        points_rows = conn.execute(
            """
            SELECT customer_id, COALESCE(SUM(points), 0) AS total_points
            FROM loyalty_point_ledger
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND redemption_id IS NULL
              AND awarded_at >= %s
              AND awarded_at < (%s::date + INTERVAL '1 day')
            GROUP BY customer_id
            """,
            (normalized_ids, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchall()
        points_map = {int(row["customer_id"]): int(row["total_points"] or 0) for row in points_rows}

        redemption_rows = conn.execute(
            """
            SELECT customer_id, COUNT(*) AS cnt
            FROM loyalty_redemptions
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND DATE(redeemed_at) >= %s
              AND DATE(redeemed_at) <= %s
            GROUP BY customer_id
            """,
            (normalized_ids, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchall()
        redemption_map = {int(row["customer_id"]): int(row["cnt"]) for row in redemption_rows}

        threshold = int(prog["threshold"] or 0)
        points_threshold = int(prog["points_threshold"] or 0)
        reward_basis = (prog["reward_basis"] or "STAMPS").upper()
        program_mode = (prog["program_mode"] or "REDEEMABLE").upper()
        stamp_enabled = int(prog["stamp_enabled"] or 0) == 1
        points_enabled = int(prog["points_enabled"] or 0) == 1
        for customer_id in normalized_ids:
            stamp_count = stamp_map.get(customer_id, 0)
            points_balance = points_map.get(customer_id, 0)
            is_eligible = False if program_mode == "EARN_ONLY" else _is_eligible(
                stamp_count=stamp_count,
                stamp_threshold=threshold,
                points_balance=points_balance,
                points_threshold=points_threshold,
                reward_basis=reward_basis,
                stamp_enabled=stamp_enabled,
                points_enabled=points_enabled,
            )
            progress_current, progress_threshold, progress_remaining, progress_unit = _compute_progress(
                stamp_count=stamp_count,
                stamp_threshold=threshold,
                points_balance=points_balance,
                points_threshold=points_threshold,
                reward_basis=reward_basis,
            )
            by_customer[customer_id].append(
                {
                    "program_id": prog["id"],
                    "name": prog["name"],
                    "program_type": prog["program_type"],
                    "qualifying_id": prog["qualifying_id"],
                    "qualifying_name": prog["qualifying_name"],
                    "threshold": threshold,
                    "points_threshold": points_threshold,
                    "reward_basis": reward_basis,
                    "program_mode": program_mode,
                    "stamp_enabled": stamp_enabled,
                    "points_enabled": points_enabled,
                    "stamp_count": stamp_count,
                    "points_balance": points_balance,
                    "stamps_remaining": max(0, threshold - stamp_count),
                    "points_remaining": max(0, points_threshold - points_balance),
                    "is_eligible": is_eligible,
                    "progress_current": progress_current,
                    "progress_threshold": progress_threshold,
                    "progress_remaining": progress_remaining,
                    "progress_unit": progress_unit,
                    "redemption_count": redemption_map.get(customer_id, 0),
                    "reward_type": prog["reward_type"],
                    "reward_value": _json_safe_reward_value(prog["reward_value"]),
                    "reward_description": prog["reward_description"],
                    "period_end": prog["period_end"],
                }
            )

    conn.close()

    for customer_id in normalized_ids:
        by_customer[customer_id].sort(key=lambda x: (not x["is_eligible"], x["progress_remaining"], x["name"]))

    return by_customer


def get_customer_earn_only(customer_id, branch_id=None):
    conn = get_db()
    today = today_local().isoformat()

    programs = conn.execute(
        """
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.period_start,
            lp.period_end,
            COALESCE(lp.stamp_enabled, 1) AS stamp_enabled,
            COALESCE(lp.points_enabled, 0) AS points_enabled,
            CASE
                WHEN lp.program_type = 'SERVICE' THEN sv.name
                WHEN lp.program_type = 'ITEM' THEN it.name
                ELSE NULL
            END AS qualifying_name
        FROM loyalty_programs lp
        LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
        LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
        WHERE lp.is_active = 1
          AND COALESCE(lp.program_mode, 'REDEEMABLE') = 'EARN_ONLY'
          AND (COALESCE(lp.stamp_enabled, 1) = 1 OR COALESCE(lp.points_enabled, 0) = 1)
          AND lp.period_start <= %s
          AND lp.period_end >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
        ORDER BY lp.name ASC
        """,
        (today, today, branch_id),
    ).fetchall()

    result = []
    for prog in programs:
        stamp_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM loyalty_stamps
            WHERE customer_id = %s
              AND program_id = %s
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            """,
            (customer_id, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchone()["cnt"]

        points_balance = conn.execute(
            """
            SELECT COALESCE(SUM(points), 0) AS total_points
            FROM loyalty_point_ledger
            WHERE customer_id = %s
              AND program_id = %s
              AND awarded_at >= %s
              AND awarded_at < (%s::date + INTERVAL '1 day')
            """,
            (customer_id, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchone()["total_points"]

        result.append(
            {
                "program_id": prog["id"],
                "name": prog["name"],
                "program_type": prog["program_type"],
                "qualifying_id": prog["qualifying_id"],
                "qualifying_name": prog["qualifying_name"],
                "program_mode": "EARN_ONLY",
                "stamp_enabled": int(prog["stamp_enabled"] or 0) == 1,
                "points_enabled": int(prog["points_enabled"] or 0) == 1,
                "stamp_count": int(stamp_count or 0),
                "points_balance": int(points_balance or 0),
                "period_end": prog["period_end"],
            }
        )

    conn.close()
    return result


def get_customer_earn_only_bulk(customer_ids, branch_id=None):
    normalized_ids = sorted({int(cid) for cid in (customer_ids or []) if cid})
    if not normalized_ids:
        return {}

    conn = get_db()
    today = today_local().isoformat()

    programs = conn.execute(
        """
        SELECT
            lp.id,
            lp.name,
            lp.program_type,
            lp.qualifying_id,
            lp.period_start,
            lp.period_end,
            COALESCE(lp.stamp_enabled, 1) AS stamp_enabled,
            COALESCE(lp.points_enabled, 0) AS points_enabled,
            CASE
                WHEN lp.program_type = 'SERVICE' THEN sv.name
                WHEN lp.program_type = 'ITEM' THEN it.name
                ELSE NULL
            END AS qualifying_name
        FROM loyalty_programs lp
        LEFT JOIN services sv ON lp.program_type = 'SERVICE' AND sv.id = lp.qualifying_id
        LEFT JOIN items it ON lp.program_type = 'ITEM' AND it.id = lp.qualifying_id
        WHERE lp.is_active = 1
          AND COALESCE(lp.program_mode, 'REDEEMABLE') = 'EARN_ONLY'
          AND (COALESCE(lp.stamp_enabled, 1) = 1 OR COALESCE(lp.points_enabled, 0) = 1)
          AND lp.period_start <= %s
          AND lp.period_end >= %s
          AND (lp.branch_id IS NULL OR lp.branch_id = %s)
        ORDER BY lp.name ASC
        """,
        (today, today, branch_id),
    ).fetchall()

    by_customer = {cid: [] for cid in normalized_ids}
    if not programs:
        conn.close()
        return by_customer

    for prog in programs:
        stamp_rows = conn.execute(
            """
            SELECT customer_id, COUNT(*) AS cnt
            FROM loyalty_stamps
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            GROUP BY customer_id
            """,
            (normalized_ids, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchall()
        stamp_map = {int(row["customer_id"]): int(row["cnt"] or 0) for row in stamp_rows}

        points_rows = conn.execute(
            """
            SELECT customer_id, COALESCE(SUM(points), 0) AS total_points
            FROM loyalty_point_ledger
            WHERE customer_id = ANY(%s)
              AND program_id = %s
              AND awarded_at >= %s
              AND awarded_at < (%s::date + INTERVAL '1 day')
            GROUP BY customer_id
            """,
            (normalized_ids, prog["id"], prog["period_start"], prog["period_end"]),
        ).fetchall()
        points_map = {int(row["customer_id"]): int(row["total_points"] or 0) for row in points_rows}

        for customer_id in normalized_ids:
            by_customer[customer_id].append(
                {
                    "program_id": prog["id"],
                    "name": prog["name"],
                    "program_type": prog["program_type"],
                    "qualifying_id": prog["qualifying_id"],
                    "qualifying_name": prog["qualifying_name"],
                    "program_mode": "EARN_ONLY",
                    "stamp_enabled": int(prog["stamp_enabled"] or 0) == 1,
                    "points_enabled": int(prog["points_enabled"] or 0) == 1,
                    "stamp_count": stamp_map.get(customer_id, 0),
                    "points_balance": points_map.get(customer_id, 0),
                    "period_end": prog["period_end"],
                }
            )

    conn.close()
    return by_customer


def get_customer_points_bulk(customer_ids, branch_id=None):
    normalized_ids = sorted({int(cid) for cid in (customer_ids or []) if cid})
    if not normalized_ids:
        return {}

    conn = get_db()

    if branch_id is None:
        rows = conn.execute(
            """
            SELECT customer_id, COALESCE(SUM(points), 0) AS total_points
            FROM loyalty_point_ledger
            WHERE customer_id = ANY(%s)
              AND redemption_id IS NULL
            GROUP BY customer_id
            """,
            (normalized_ids,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT lpl.customer_id, COALESCE(SUM(lpl.points), 0) AS total_points
            FROM loyalty_point_ledger lpl
            JOIN loyalty_programs lp ON lp.id = lpl.program_id
            WHERE lpl.customer_id = ANY(%s)
              AND lpl.redemption_id IS NULL
              AND (lp.branch_id IS NULL OR lp.branch_id = %s)
            GROUP BY lpl.customer_id
            """,
            (normalized_ids, branch_id),
        ).fetchall()

    conn.close()

    totals = {cid: 0 for cid in normalized_ids}
    for row in rows:
        totals[int(row["customer_id"])] = int(row["total_points"] or 0)
    return totals


def redeem_reward(customer_id, program_id, sale_id, user_id):
    conn = get_db()
    today = today_local().isoformat()

    try:
        conn.execute("BEGIN")

        prog = conn.execute(
            """
            SELECT
                id,
                name,
                threshold,
                points_threshold,
                reward_basis,
                program_mode,
                COALESCE(stamp_enabled, 1) AS stamp_enabled,
                COALESCE(points_enabled, 0) AS points_enabled,
                reward_type,
                reward_value,
                reward_description,
                period_start,
                period_end
            FROM loyalty_programs
            WHERE id = %s
              AND is_active = 1
              AND (COALESCE(stamp_enabled, 1) = 1 OR COALESCE(points_enabled, 0) = 1)
              AND period_start <= %s
              AND period_end >= %s
            """,
            (program_id, today, today),
        ).fetchone()

        if not prog:
            raise ValueError("This loyalty program is no longer active or has expired.")
        if (prog["program_mode"] or "REDEEMABLE").upper() == "EARN_ONLY":
            raise ValueError("This loyalty program is earn-only and cannot be redeemed.")

        eligible_stamps = conn.execute(
            """
            SELECT id
            FROM loyalty_stamps
            WHERE customer_id = %s
              AND program_id = %s
              AND redemption_id IS NULL
              AND stamped_at >= %s
              AND stamped_at < (%s::date + INTERVAL '1 day')
            ORDER BY stamped_at ASC
            """,
            (customer_id, program_id, prog["period_start"], prog["period_end"]),
        ).fetchall()

        eligible_points = conn.execute(
            """
            SELECT id, points
            FROM loyalty_point_ledger
            WHERE customer_id = %s
              AND program_id = %s
              AND redemption_id IS NULL
              AND awarded_at >= %s
              AND awarded_at < (%s::date + INTERVAL '1 day')
            ORDER BY awarded_at ASC, id ASC
            """,
            (customer_id, program_id, prog["period_start"], prog["period_end"]),
        ).fetchall()

        threshold = int(prog["threshold"] or 0)
        points_threshold = int(prog["points_threshold"] or 0)
        reward_basis = (prog["reward_basis"] or "STAMPS").upper()
        stamp_enabled = int(prog["stamp_enabled"] or 0) == 1
        points_enabled = int(prog["points_enabled"] or 0) == 1
        stamp_count = len(eligible_stamps)
        points_balance = int(sum(int(r["points"] or 0) for r in eligible_points))

        stamp_eligible = stamp_enabled and threshold > 0 and stamp_count >= threshold
        points_eligible = points_enabled and points_threshold > 0 and points_balance >= points_threshold
        is_eligible = _is_eligible(
            stamp_count=stamp_count,
            stamp_threshold=threshold,
            points_balance=points_balance,
            points_threshold=points_threshold,
            reward_basis=reward_basis,
            stamp_enabled=stamp_enabled,
            points_enabled=points_enabled,
        )
        if not is_eligible:
            raise ValueError("Customer is not yet eligible to redeem this reward.")

        consume_basis = "STAMPS"
        if reward_basis == "POINTS":
            consume_basis = "POINTS"
        elif reward_basis == "STAMPS_OR_POINTS":
            consume_basis = "STAMPS" if stamp_eligible else "POINTS"

        stamps_to_consume = []
        if consume_basis == "STAMPS":
            if not stamp_eligible:
                raise ValueError(f"Not enough stamps to redeem. Need {threshold}, found {stamp_count}.")
            stamps_to_consume = [row["id"] for row in eligible_stamps[:threshold]]

        points_to_consume = []
        points_consumed = 0
        if consume_basis == "POINTS":
            if not points_eligible:
                raise ValueError(f"Not enough points to redeem. Need {points_threshold}, found {points_balance}.")
            running = 0
            for row in eligible_points:
                points_to_consume.append(int(row["id"]))
                running += int(row["points"] or 0)
                if running >= points_threshold:
                    break
            points_consumed = running

        reward_snapshot = json.dumps(
            {
                "reward_type": prog["reward_type"],
                "reward_value": _json_safe_reward_value(prog["reward_value"]),
                "reward_description": prog["reward_description"],
                "program_name": prog["name"],
                "redeemed_on": today,
                "consumed_basis": consume_basis,
                "points_consumed": points_consumed,
            }
        )

        redemption_row = conn.execute(
            """
            INSERT INTO loyalty_redemptions (
                customer_id, program_id, applied_on_sale_id,
                redeemed_by, reward_snapshot, stamps_consumed
            ) VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (customer_id, program_id, sale_id, user_id, reward_snapshot, len(stamps_to_consume)),
        ).fetchone()
        redemption_id = redemption_row["id"]

        if stamps_to_consume:
            conn.executemany(
                "UPDATE loyalty_stamps SET redemption_id = %s WHERE id = %s",
                [(redemption_id, sid) for sid in stamps_to_consume],
            )
        if points_to_consume:
            conn.executemany(
                "UPDATE loyalty_point_ledger SET redemption_id = %s WHERE id = %s",
                [(redemption_id, pid) for pid in points_to_consume],
            )

        # A sale used to redeem a reward should not also keep newly-earned credit
        # for the same program on that same sale, or the customer appears to
        # "instantly earn back" the stamp/points they just spent.
        conn.execute(
            """
            UPDATE loyalty_stamps
            SET redemption_id = %s
            WHERE customer_id = %s
              AND program_id = %s
              AND sale_id = %s
              AND redemption_id IS NULL
            """,
            (redemption_id, customer_id, program_id, sale_id),
        )
        conn.execute(
            """
            UPDATE loyalty_point_ledger
            SET redemption_id = %s
            WHERE customer_id = %s
              AND program_id = %s
              AND sale_id = %s
              AND redemption_id IS NULL
            """,
            (redemption_id, customer_id, program_id, sale_id),
        )

        conn.commit()
        return {
            "redemption_id": redemption_id,
            "program_name": prog["name"],
            "reward_type": prog["reward_type"],
            "reward_value": _json_safe_reward_value(prog["reward_value"]),
            "reward_description": prog["reward_description"],
            "consumed_basis": consume_basis,
            "stamps_consumed": len(stamps_to_consume),
            "points_consumed": points_consumed,
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_customer_loyalty_summary(customer_id):
    eligibility = get_customer_eligibility(customer_id)
    earn_only_programs = get_customer_earn_only(customer_id)
    points_total = get_customer_points_bulk([customer_id]).get(int(customer_id), 0)

    conn = get_db()
    redemptions = conn.execute(
        """
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
        """,
        (customer_id,),
    ).fetchall()
    conn.close()

    history = []
    for row in redemptions:
        snapshot = {}
        try:
            snapshot = json.loads(row["reward_snapshot"])
        except Exception:
            snapshot = {}

        history.append(
            {
                "redemption_id": row["id"],
                "program_name": row["program_name"],
                "sales_number": row["sales_number"],
                "redeemed_at": row["redeemed_at"],
                "redeemed_at_display": format_date(row["redeemed_at"], show_time=True),
                "stamps_consumed": row["stamps_consumed"],
                "points_consumed": snapshot.get("points_consumed", 0),
                "consumed_basis": snapshot.get("consumed_basis", "STAMPS"),
                "reward_description": snapshot.get("reward_description", ""),
            }
        )

    return {
        "points_total": points_total,
        "programs": eligibility,
        "earn_only_programs": earn_only_programs,
        "redemption_history": history,
    }
