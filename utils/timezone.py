import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Manila"
PH_TIMEZONE_FALLBACK = timezone(timedelta(hours=8), name="Asia/Manila")


def get_app_timezone_name():
    return (os.environ.get("APP_TIMEZONE") or os.environ.get("TZ") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def get_app_timezone():
    tz_name = get_app_timezone_name()
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        # Windows/local environments may not have IANA tzdata installed.
        # For Philippine time, a fixed UTC+8 fallback is safe because PH has no DST.
        if tz_name == DEFAULT_TIMEZONE:
            return PH_TIMEZONE_FALLBACK
        return timezone.utc


def configure_process_timezone():
    tz_name = get_app_timezone_name()
    os.environ["TZ"] = tz_name
    tzset = getattr(time, "tzset", None)
    if callable(tzset):
        tzset()
    return tz_name


def now_local():
    return datetime.now(get_app_timezone())


def now_local_naive():
    return now_local().replace(tzinfo=None)


def now_local_str():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


def today_local():
    return now_local().date()


def to_local_datetime(value):
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    else:
        raw = str(value).strip()
        if not raw:
            return None

        normalized = raw.replace("T", " ")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(raw[:19] if pattern.endswith("%S") else raw[:10], pattern)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return None

    if dt.tzinfo is not None:
        return dt.astimezone(get_app_timezone())
    return dt
