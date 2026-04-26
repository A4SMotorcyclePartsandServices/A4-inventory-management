import hashlib
import secrets

from db.database import get_db


AUTH_SESSION_TOKEN_KEY = "auth_session_token"


def _hash_token(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_auth_session(*, user_id, lifetime_seconds, user_agent=None, ip_address=None):
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    lifetime_seconds = max(60, int(lifetime_seconds or 0))

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO auth_sessions (
                user_id,
                token_hash,
                expires_at,
                user_agent,
                ip_address
            )
            VALUES (
                %s,
                %s,
                NOW() + (%s * INTERVAL '1 second'),
                %s,
                %s
            )
            """,
            (
                user_id,
                token_hash,
                lifetime_seconds,
                (user_agent or "")[:300] or None,
                (ip_address or "")[:64] or None,
            ),
        )
        conn.execute(
            """
            UPDATE auth_sessions
            SET
                revoked_at = NOW(),
                revoked_reason = 'expired_cleanup'
            WHERE expires_at < NOW()
              AND revoked_at IS NULL
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return token


def validate_auth_session(*, user_id, token, touch=True):
    if not user_id or not token:
        return {"valid": False, "reason": "missing_token"}

    token_hash = _hash_token(token)
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT
                id,
                user_id,
                expires_at,
                revoked_at,
                expires_at < NOW() AS is_expired
            FROM auth_sessions
            WHERE token_hash = %s
            """,
            (token_hash,),
        ).fetchone()

        if not row:
            return {"valid": False, "reason": "not_found"}

        if int(row["user_id"]) != int(user_id):
            return {"valid": False, "reason": "user_mismatch"}

        if row["revoked_at"]:
            return {"valid": False, "reason": "revoked"}

        if row["is_expired"]:
            conn.execute(
                """
                UPDATE auth_sessions
                SET
                    revoked_at = NOW(),
                    revoked_reason = 'expired'
                WHERE id = %s
                  AND revoked_at IS NULL
                """,
                (row["id"],),
            )
            conn.commit()
            return {"valid": False, "reason": "expired"}

        if touch:
            conn.execute(
                """
                UPDATE auth_sessions
                SET last_seen_at = NOW()
                WHERE id = %s
                """,
                (row["id"],),
            )
            conn.commit()
        else:
            conn.rollback()

        return {"valid": True, "reason": "active", "session_id": row["id"]}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_auth_session(*, user_id, token, reason="logout"):
    if not user_id or not token:
        return False

    token_hash = _hash_token(token)
    conn = get_db()
    try:
        result = conn.execute(
            """
            UPDATE auth_sessions
            SET
                revoked_at = NOW(),
                revoked_reason = %s
            WHERE user_id = %s
              AND token_hash = %s
              AND revoked_at IS NULL
            """,
            (reason, user_id, token_hash),
        )
        revoked = result.rowcount > 0
        conn.commit()
        return revoked
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
