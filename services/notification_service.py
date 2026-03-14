from datetime import datetime

import psycopg2.extras

from db.database import get_db
from utils.formatters import format_date


DEFAULT_NOTIFICATION_LIMIT = 10
MAX_NOTIFICATION_LIMIT = 50


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jsonb(value):
    return psycopg2.extras.Json(value or {})


def _normalize_limit(limit):
    try:
        parsed = int(limit or DEFAULT_NOTIFICATION_LIMIT)
    except (TypeError, ValueError):
        parsed = DEFAULT_NOTIFICATION_LIMIT
    return max(1, min(parsed, MAX_NOTIFICATION_LIMIT))


def _serialize_notification(row):
    if not row:
        return None

    data = dict(row)
    metadata = data.get("metadata")
    data["metadata"] = metadata if isinstance(metadata, dict) else (metadata or {})
    data["created_at"] = format_date(data.get("created_at"), show_time=True)
    data["read_at"] = format_date(data.get("read_at"), show_time=True)
    data["is_read"] = int(data.get("is_read") or 0)
    data["is_archived"] = int(data.get("is_archived") or 0)
    return data


def list_active_user_ids(role=None, external_conn=None):
    conn = external_conn if external_conn else get_db()
    params = []
    where_clauses = ["is_active = 1"]

    if role:
        where_clauses.append("role = %s")
        params.append(str(role).strip().lower())

    where_sql = " AND ".join(where_clauses)

    try:
        rows = conn.execute(
            f"""
            SELECT id
            FROM users
            WHERE {where_sql}
            ORDER BY id ASC
            """,
            params,
        ).fetchall()
        return [int(row["id"]) for row in rows]
    finally:
        if not external_conn:
            conn.close()


def create_notification(
    recipient_user_id,
    notification_type,
    title,
    message,
    *,
    category="general",
    entity_type=None,
    entity_id=None,
    action_url=None,
    created_by=None,
    metadata=None,
    external_conn=None,
):
    recipient_user_id = int(recipient_user_id)
    conn = external_conn if external_conn else get_db()

    try:
        if not external_conn:
            conn.execute("BEGIN")

        row = conn.execute(
            """
            INSERT INTO notifications (
                recipient_user_id,
                notification_type,
                category,
                title,
                message,
                entity_type,
                entity_id,
                action_url,
                is_read,
                read_at,
                is_archived,
                created_at,
                created_by,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, NULL, 0, %s, %s, %s)
            RETURNING *
            """,
            (
                recipient_user_id,
                str(notification_type or "").strip(),
                str(category or "general").strip(),
                str(title or "").strip(),
                str(message or "").strip(),
                str(entity_type or "").strip() or None,
                int(entity_id) if entity_id is not None else None,
                str(action_url or "").strip() or None,
                _now(),
                int(created_by) if created_by is not None else None,
                _jsonb(metadata),
            ),
        ).fetchone()

        if not external_conn:
            conn.commit()
        return _serialize_notification(row)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def create_notifications_for_users(
    recipient_user_ids,
    notification_type,
    title,
    message,
    *,
    category="general",
    entity_type=None,
    entity_id=None,
    action_url=None,
    created_by=None,
    metadata=None,
    external_conn=None,
):
    unique_recipient_ids = []
    seen_ids = set()
    for user_id in recipient_user_ids or []:
        try:
            normalized_id = int(user_id)
        except (TypeError, ValueError):
            continue
        if normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        unique_recipient_ids.append(normalized_id)

    if not unique_recipient_ids:
        return []

    conn = external_conn if external_conn else get_db()

    try:
        if not external_conn:
            conn.execute("BEGIN")

        rows = []
        for recipient_user_id in unique_recipient_ids:
            row = conn.execute(
                """
                INSERT INTO notifications (
                    recipient_user_id,
                    notification_type,
                    category,
                    title,
                    message,
                    entity_type,
                    entity_id,
                    action_url,
                    is_read,
                    read_at,
                    is_archived,
                    created_at,
                    created_by,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, NULL, 0, %s, %s, %s)
                RETURNING *
                """,
                (
                    recipient_user_id,
                    str(notification_type or "").strip(),
                    str(category or "general").strip(),
                    str(title or "").strip(),
                    str(message or "").strip(),
                    str(entity_type or "").strip() or None,
                    int(entity_id) if entity_id is not None else None,
                    str(action_url or "").strip() or None,
                    _now(),
                    int(created_by) if created_by is not None else None,
                    _jsonb(metadata),
                ),
            ).fetchone()
            rows.append(_serialize_notification(row))

        if not external_conn:
            conn.commit()
        return rows
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def archive_notifications(
    *,
    recipient_user_id=None,
    entity_type=None,
    entity_id=None,
    notification_types=None,
    external_conn=None,
):
    where_clauses = ["is_archived = 0"]
    params = []

    if recipient_user_id is not None:
        where_clauses.append("recipient_user_id = %s")
        params.append(int(recipient_user_id))
    if entity_type:
        where_clauses.append("entity_type = %s")
        params.append(str(entity_type).strip())
    if entity_id is not None:
        where_clauses.append("entity_id = %s")
        params.append(int(entity_id))
    if notification_types:
        normalized_types = [str(item).strip() for item in notification_types if str(item).strip()]
        if normalized_types:
            where_clauses.append("notification_type = ANY(%s)")
            params.append(normalized_types)

    conn = external_conn if external_conn else get_db()

    try:
        if not external_conn:
            conn.execute("BEGIN")

        cursor = conn.execute(
            f"""
            UPDATE notifications
            SET is_archived = 1
            WHERE {" AND ".join(where_clauses)}
            """,
            params,
        )

        if not external_conn:
            conn.commit()
        return cursor.rowcount
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def count_unread_notifications(recipient_user_id, include_archived=False, external_conn=None):
    conn = external_conn if external_conn else get_db()

    try:
        where_clauses = ["recipient_user_id = %s", "is_read = 0"]
        params = [int(recipient_user_id)]
        if not include_archived:
            where_clauses.append("is_archived = 0")

        row = conn.execute(
            f"""
            SELECT COUNT(*) AS unread_count
            FROM notifications
            WHERE {" AND ".join(where_clauses)}
            """,
            params,
        ).fetchone()
        return int(row["unread_count"] or 0)
    finally:
        if not external_conn:
            conn.close()


def list_notifications(recipient_user_id, limit=DEFAULT_NOTIFICATION_LIMIT, unread_only=False, external_conn=None):
    conn = external_conn if external_conn else get_db()
    normalized_limit = _normalize_limit(limit)
    where_clauses = ["recipient_user_id = %s", "is_archived = 0"]
    params = [int(recipient_user_id)]

    if unread_only:
        where_clauses.append("is_read = 0")

    try:
        rows = conn.execute(
            f"""
            SELECT *
            FROM notifications
            WHERE {" AND ".join(where_clauses)}
            ORDER BY is_read ASC, created_at DESC, id DESC
            LIMIT %s
            """,
            params + [normalized_limit],
        ).fetchall()
        return [_serialize_notification(row) for row in rows]
    finally:
        if not external_conn:
            conn.close()


def get_notification_summary(recipient_user_id, limit=5, external_conn=None):
    return {
        "unread_count": count_unread_notifications(recipient_user_id, external_conn=external_conn),
        "notifications": list_notifications(recipient_user_id, limit=limit, external_conn=external_conn),
    }


def mark_notification_read(notification_id, recipient_user_id, external_conn=None):
    conn = external_conn if external_conn else get_db()

    try:
        if not external_conn:
            conn.execute("BEGIN")

        row = conn.execute(
            """
            UPDATE notifications
            SET is_read = 1,
                read_at = COALESCE(read_at, %s)
            WHERE id = %s
              AND recipient_user_id = %s
            RETURNING *
            """,
            (_now(), int(notification_id), int(recipient_user_id)),
        ).fetchone()

        if not external_conn:
            conn.commit()
        return _serialize_notification(row)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def mark_all_notifications_read(recipient_user_id, external_conn=None):
    conn = external_conn if external_conn else get_db()

    try:
        if not external_conn:
            conn.execute("BEGIN")

        cursor = conn.execute(
            """
            UPDATE notifications
            SET is_read = 1,
                read_at = COALESCE(read_at, %s)
            WHERE recipient_user_id = %s
              AND is_archived = 0
              AND is_read = 0
            """,
            (_now(), int(recipient_user_id)),
        )

        if not external_conn:
            conn.commit()
        return cursor.rowcount
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()
