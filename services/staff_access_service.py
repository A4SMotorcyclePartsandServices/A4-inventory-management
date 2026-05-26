from datetime import date, time

from db.database import get_db
from utils.formatters import format_date
from utils.timezone import now_local, today_local


DEFAULT_WORK_START = time(8, 0)
DEFAULT_WORK_END = time(18, 0)
DEFAULT_TIMEZONE_NAME = "Asia/Manila"
WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _time_value(value, default=None):
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            try:
                hour, minute = raw[:5].split(":")
                return time(int(hour), int(minute))
            except (TypeError, ValueError):
                pass
    return default


def _time_display(value):
    parsed = _time_value(value)
    return parsed.strftime("%H:%M") if parsed else ""


def _in_time_window(current_time, start_time, end_time):
    if start_time == end_time:
        return True
    if start_time < end_time:
        return start_time <= current_time < end_time
    return current_time >= start_time or current_time < end_time


def _schedule_from_row(row):
    if not row:
        return {
            "schedule_enabled": 1,
            "work_start_time": DEFAULT_WORK_START,
            "work_end_time": DEFAULT_WORK_END,
            "timezone_name": DEFAULT_TIMEZONE_NAME,
        }
    return {
        "schedule_enabled": int(row["schedule_enabled"] or 0),
        "work_start_time": _time_value(row["work_start_time"], DEFAULT_WORK_START),
        "work_end_time": _time_value(row["work_end_time"], DEFAULT_WORK_END),
        "timezone_name": row["timezone_name"] or DEFAULT_TIMEZONE_NAME,
    }


def ensure_staff_access_schedule(conn, user_id):
    conn.execute(
        """
        INSERT INTO staff_access_schedules (user_id)
        VALUES (%s)
        ON CONFLICT (user_id) DO NOTHING
        """,
        (user_id,),
    )


def get_staff_access_state(user_id, *, conn=None, user_role="staff", at_datetime=None):
    if user_role != "staff":
        return {"allowed": True, "reason": "Admin accounts bypass staff access schedules."}

    owns_conn = conn is None
    conn = conn or get_db()
    try:
        ensure_staff_access_schedule(conn, user_id)
        if owns_conn:
            conn.commit()
        schedule_row = conn.execute(
            """
            SELECT schedule_enabled, work_start_time, work_end_time, timezone_name
            FROM staff_access_schedules
            WHERE user_id = %s
            """,
            (user_id,),
        ).fetchone()
        schedule = _schedule_from_row(schedule_row)

        if not schedule["schedule_enabled"]:
            return {
                "allowed": True,
                "reason": "Schedule enforcement is disabled for this staff account.",
                "schedule": schedule,
            }

        current_dt = at_datetime or now_local()
        current_date = current_dt.date()
        current_time = current_dt.time().replace(microsecond=0)
        current_weekday = current_dt.weekday()

        override = conn.execute(
            """
            SELECT id, allow_start_time, allow_end_time, reason
            FROM staff_access_overrides
            WHERE user_id = %s
              AND override_date = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (user_id, current_date),
        ).fetchone()
        if override:
            override_start = _time_value(override["allow_start_time"], DEFAULT_WORK_START)
            override_end = _time_value(override["allow_end_time"], DEFAULT_WORK_END)
            if _in_time_window(current_time, override_start, override_end):
                return {
                    "allowed": True,
                    "reason": "Allowed by extra-shift override.",
                    "override_id": override["id"],
                    "schedule": schedule,
                }

        off_day = conn.execute(
            """
            SELECT 1
            FROM staff_access_off_days
            WHERE user_id = %s
              AND weekday = %s
            """,
            (user_id, current_weekday),
        ).fetchone()
        if off_day:
            return {
                "allowed": False,
                "reason": f"Access is blocked on scheduled day off: {WEEKDAY_LABELS[current_weekday]}.",
                "schedule": schedule,
            }

        if not _in_time_window(current_time, schedule["work_start_time"], schedule["work_end_time"]):
            return {
                "allowed": False,
                "reason": (
                    "Access is allowed only from "
                    f"{_time_display(schedule['work_start_time'])} to {_time_display(schedule['work_end_time'])} PHT."
                ),
                "schedule": schedule,
            }

        return {"allowed": True, "reason": "Within scheduled staff access hours.", "schedule": schedule}
    finally:
        if owns_conn:
            conn.close()


def update_staff_access_schedule(user_id, *, schedule_enabled=True, work_start_time=None, work_end_time=None, off_days=None):
    start_time = _time_value(work_start_time, DEFAULT_WORK_START)
    end_time = _time_value(work_end_time, DEFAULT_WORK_END)
    normalized_off_days = sorted({int(day) for day in (off_days or []) if str(day).strip() in {str(i) for i in range(7)}})

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, role, username FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("Staff user not found.")
        if user["role"] != "staff":
            raise ValueError("Only staff accounts can have access schedules.")

        ensure_staff_access_schedule(conn, user_id)
        conn.execute(
            """
            UPDATE staff_access_schedules
            SET schedule_enabled = %s,
                work_start_time = %s,
                work_end_time = %s,
                timezone_name = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (1 if schedule_enabled else 0, start_time.strftime("%H:%M"), end_time.strftime("%H:%M"), DEFAULT_TIMEZONE_NAME, user_id),
        )
        conn.execute("DELETE FROM staff_access_off_days WHERE user_id = %s", (user_id,))
        for weekday in normalized_off_days:
            conn.execute(
                """
                INSERT INTO staff_access_off_days (user_id, weekday)
                VALUES (%s, %s)
                ON CONFLICT (user_id, weekday) DO NOTHING
                """,
                (user_id, weekday),
            )
        conn.commit()
        return {"username": user["username"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_staff_access_override(user_id, *, override_date, allow_start_time, allow_end_time, reason, created_by):
    start_time = _time_value(allow_start_time, DEFAULT_WORK_START)
    end_time = _time_value(allow_end_time, DEFAULT_WORK_END)
    try:
        normalized_date = date.fromisoformat(str(override_date or "").strip())
    except ValueError:
        raise ValueError("Override date is required.")

    conn = get_db()
    try:
        user = conn.execute(
            "SELECT id, role, username FROM users WHERE id = %s",
            (user_id,),
        ).fetchone()
        if not user:
            raise ValueError("Staff user not found.")
        if user["role"] != "staff":
            raise ValueError("Only staff accounts can have access overrides.")

        conn.execute(
            """
            INSERT INTO staff_access_overrides (
                user_id, override_date, allow_start_time, allow_end_time, reason, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                user_id,
                normalized_date,
                start_time.strftime("%H:%M"),
                end_time.strftime("%H:%M"),
                str(reason or "").strip()[:300] or None,
                created_by,
            ),
        )
        conn.commit()
        return {"username": user["username"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_staff_access_override(override_id):
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT sao.id, u.username
            FROM staff_access_overrides sao
            JOIN users u ON u.id = sao.user_id
            WHERE sao.id = %s
            """,
            (override_id,),
        ).fetchone()
        if not row:
            raise ValueError("Override not found.")
        conn.execute("DELETE FROM staff_access_overrides WHERE id = %s", (override_id,))
        conn.commit()
        return {"username": row["username"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attach_staff_access_admin_data(users):
    staff_ids = [int(user["id"]) for user in users if user.get("role") == "staff"]
    if not staff_ids:
        return users

    conn = get_db()
    try:
        for user_id in staff_ids:
            ensure_staff_access_schedule(conn, user_id)
        conn.commit()

        schedule_rows = conn.execute(
            """
            SELECT user_id, schedule_enabled, work_start_time, work_end_time, timezone_name
            FROM staff_access_schedules
            WHERE user_id = ANY(%s)
            """,
            (staff_ids,),
        ).fetchall()
        schedules = {int(row["user_id"]): _schedule_from_row(row) for row in schedule_rows}

        off_rows = conn.execute(
            """
            SELECT user_id, weekday
            FROM staff_access_off_days
            WHERE user_id = ANY(%s)
            ORDER BY weekday ASC
            """,
            (staff_ids,),
        ).fetchall()
        off_days = {}
        for row in off_rows:
            off_days.setdefault(int(row["user_id"]), []).append(int(row["weekday"]))

        override_rows = conn.execute(
            """
            SELECT id, user_id, override_date, allow_start_time, allow_end_time, reason
            FROM staff_access_overrides
            WHERE user_id = ANY(%s)
              AND override_date >= %s
            ORDER BY override_date ASC, allow_start_time ASC, id ASC
            """,
            (staff_ids, today_local()),
        ).fetchall()
        overrides = {}
        for row in override_rows:
            overrides.setdefault(int(row["user_id"]), []).append(
                {
                    "id": row["id"],
                    "override_date": row["override_date"].isoformat(),
                    "override_date_display": format_date(row["override_date"]),
                    "allow_start_time": _time_display(row["allow_start_time"]),
                    "allow_end_time": _time_display(row["allow_end_time"]),
                    "reason": row["reason"] or "",
                }
            )

        for user in users:
            if user.get("role") != "staff":
                continue
            user_id = int(user["id"])
            schedule = schedules.get(user_id) or _schedule_from_row(None)
            current_state = get_staff_access_state(user_id, conn=conn, user_role="staff")
            user_off_days = off_days.get(user_id, [])
            user["access_schedule"] = {
                "schedule_enabled": int(schedule["schedule_enabled"]),
                "work_start_time": _time_display(schedule["work_start_time"]),
                "work_end_time": _time_display(schedule["work_end_time"]),
                "timezone_name": schedule["timezone_name"],
                "off_days": user_off_days,
                "off_day_labels": [WEEKDAY_LABELS[day] for day in user_off_days],
                "state": current_state,
                "overrides": overrides.get(user_id, []),
            }
        return users
    finally:
        conn.close()
