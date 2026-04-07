import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from psycopg2.extras import Json

from db.database import get_db


PROCESSING_STATUS = "PROCESSING"
COMPLETED_STATUS = "COMPLETED"
FAILED_STATUS = "FAILED"

IDEMPOTENCY_KEY_HEADER = "X-Idempotency-Key"
IDEMPOTENCY_KEY_FIELD = "idempotency_key"


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _make_json_safe(payload):
    return json.loads(json.dumps(payload or {}, default=_json_default))


def extract_idempotency_key(req):
    return (
        req.headers.get(IDEMPOTENCY_KEY_HEADER)
        or req.form.get(IDEMPOTENCY_KEY_FIELD)
        or ""
    ).strip()


def build_request_hash(payload):
    normalized = json.dumps(
        payload or {},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def begin_idempotent_request(*, scope, actor_user_id, idempotency_key, request_payload):
    if not scope:
        raise ValueError("Idempotency scope is required.")
    if not actor_user_id:
        raise ValueError("Authenticated user is required for idempotent requests.")
    if not idempotency_key:
        raise ValueError("Missing idempotency key.")

    request_hash = build_request_hash(request_payload)
    conn = get_db()
    try:
        inserted = conn.execute(
            """
            INSERT INTO idempotency_requests (
                scope,
                actor_user_id,
                idempotency_key,
                request_hash,
                status
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (scope, actor_user_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (scope, actor_user_id, idempotency_key, request_hash, PROCESSING_STATUS),
        ).fetchone()

        if inserted:
            conn.commit()
            return {"state": "new", "request_hash": request_hash}

        existing = conn.execute(
            """
            SELECT
                status,
                request_hash,
                response_code,
                response_body
            FROM idempotency_requests
            WHERE scope = %s
              AND actor_user_id = %s
              AND idempotency_key = %s
            FOR UPDATE
            """,
            (scope, actor_user_id, idempotency_key),
        ).fetchone()

        if not existing:
            conn.rollback()
            raise ValueError("Unable to load the existing idempotent request.")

        conn.execute(
            """
            UPDATE idempotency_requests
            SET last_seen_at = NOW()
            WHERE scope = %s
              AND actor_user_id = %s
              AND idempotency_key = %s
            """,
            (scope, actor_user_id, idempotency_key),
        )
        conn.commit()

        if existing["request_hash"] != request_hash:
            return {
                "state": "mismatch",
                "message": "This submission key was already used for a different request.",
            }

        if existing["status"] == PROCESSING_STATUS:
            return {
                "state": "processing",
                "message": "This submission is already being processed.",
            }

        return {
            "state": "replay",
            "response_code": int(existing["response_code"] or 200),
            "response_body": existing["response_body"] or {"status": "success"},
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finalize_idempotent_request(
    *,
    scope,
    actor_user_id,
    idempotency_key,
    status,
    response_code,
    response_body,
    resource_type=None,
    resource_id=None,
):
    if status not in {COMPLETED_STATUS, FAILED_STATUS}:
        raise ValueError("Invalid terminal idempotency status.")

    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE idempotency_requests
            SET
                status = %s,
                response_code = %s,
                response_body = %s,
                resource_type = %s,
                resource_id = %s,
                completed_at = NOW(),
                last_seen_at = NOW()
            WHERE scope = %s
              AND actor_user_id = %s
              AND idempotency_key = %s
            """,
            (
                status,
                int(response_code),
                Json(_make_json_safe(response_body)),
                resource_type,
                resource_id,
                scope,
                actor_user_id,
                idempotency_key,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
