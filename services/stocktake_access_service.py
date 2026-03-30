from datetime import date, datetime, time, timedelta

import psycopg2.extras

from db.database import get_db
from services.approval_service import approve_request, cancel_request, create_approval_request, get_approval_request_by_entity
from services.notification_service import (
    archive_notifications,
    create_notification,
    create_notifications_for_users,
    list_active_user_ids,
)
from utils.formatters import format_date


STOCKTAKE_ACCESS_APPROVAL_TYPE = "STOCKTAKE_ACCESS"
STOCKTAKE_ACCESS_ENTITY_TYPE = "user"

STOCKTAKE_ACCESS_ADMIN_PENDING_NOTIFICATION_TYPES = {
    "STOCKTAKE_ACCESS_REQUESTED",
}
STOCKTAKE_ACCESS_REQUESTER_NOTIFICATION_TYPES = {
    "STOCKTAKE_ACCESS_GRANTED",
    "STOCKTAKE_ACCESS_REJECTED",
    "STOCKTAKE_ACCESS_REVOKED",
}


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jsonb(value):
    return psycopg2.extras.Json(value or {})


def _admin_stocktake_access_url():
    return "/users/audit?tab=stocktake-access-tab"


def _requester_stocktake_url():
    return "/stocktake"


def _coerce_expiry_date(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("Choose an access expiry date.")

    try:
        parsed = date.fromisoformat(raw_value)
    except ValueError:
        raise ValueError("Access expiry date must be a valid date.")

    if parsed < date.today():
        raise ValueError("Access expiry date cannot be in the past.")

    return parsed


def _serialize_grant_row(row):
    if not row:
        return None

    data = dict(row)
    expires_at = data.get("expires_at")
    revoked_at = data.get("revoked_at")
    data["granted_at_display"] = format_date(data.get("granted_at"), show_time=True)
    data["expires_at_display"] = format_date(expires_at, show_time=True)
    data["revoked_at_display"] = format_date(revoked_at, show_time=True)
    data["is_active"] = bool(expires_at and not revoked_at and expires_at >= datetime.now())
    return data


def _serialize_access_request_row(row):
    if not row:
        return None

    data = dict(row)
    metadata = data.get("metadata")
    data["metadata"] = metadata if isinstance(metadata, dict) else (metadata or {})
    data["request_reason"] = (data["metadata"].get("request_reason") or "").strip()
    data["requested_at_display"] = format_date(data.get("requested_at"), show_time=True)
    data["last_submitted_at_display"] = format_date(data.get("last_submitted_at"), show_time=True)
    data["decision_at_display"] = format_date(data.get("decision_at"), show_time=True)
    data["latest_granted_at_display"] = format_date(data.get("latest_granted_at"), show_time=True)
    data["latest_expires_at_display"] = format_date(data.get("latest_expires_at"), show_time=True)
    data["latest_revoked_at_display"] = format_date(data.get("latest_revoked_at"), show_time=True)
    data["active_expires_at_display"] = format_date(data.get("active_expires_at"), show_time=True)
    data["has_active_access"] = bool(data.get("active_grant_id"))
    data["has_latest_grant"] = bool(data.get("latest_grant_id"))

    if data["has_active_access"]:
        data["access_state"] = "ACTIVE"
    elif data.get("latest_grant_id") and data.get("latest_revoked_at"):
        data["access_state"] = "REVOKED"
    elif data.get("latest_grant_id") and data.get("latest_expires_at"):
        data["access_state"] = "EXPIRED"
    else:
        data["access_state"] = "NONE"

    return data


def _get_active_grant_row(conn, user_id):
    return conn.execute(
        """
        SELECT
            sag.*,
            grantor.username AS granted_by_username,
            revoker.username AS revoked_by_username
        FROM stocktake_access_grants sag
        LEFT JOIN users grantor ON grantor.id = sag.granted_by
        LEFT JOIN users revoker ON revoker.id = sag.revoked_by
        WHERE sag.user_id = %s
          AND sag.revoked_at IS NULL
          AND sag.expires_at >= NOW()
        ORDER BY sag.expires_at DESC, sag.id DESC
        LIMIT 1
        """,
        (int(user_id),),
    ).fetchone()


def user_has_active_stocktake_access(user_id, user_role=None, external_conn=None):
    if str(user_role or "").strip().lower() == "admin":
        return True

    conn = external_conn if external_conn else get_db()
    try:
        return bool(_get_active_grant_row(conn, user_id))
    finally:
        if not external_conn:
            conn.close()


def get_stocktake_access_state(user_id, user_role=None, external_conn=None):
    role = str(user_role or "").strip().lower()
    if not user_id:
        return {
            "is_admin": False,
            "has_access": False,
            "can_request": False,
            "active_grant": None,
            "pending_request": None,
            "latest_request": None,
        }

    if role == "admin":
        return {
            "is_admin": True,
            "has_access": True,
            "can_request": False,
            "active_grant": None,
            "pending_request": None,
            "latest_request": None,
        }

    conn = external_conn if external_conn else get_db()
    try:
        active_grant = _serialize_grant_row(_get_active_grant_row(conn, user_id))
        latest_request = get_approval_request_by_entity(
            STOCKTAKE_ACCESS_APPROVAL_TYPE,
            STOCKTAKE_ACCESS_ENTITY_TYPE,
            int(user_id),
            external_conn=conn,
        )
        pending_request = latest_request if latest_request and latest_request.get("status") == "PENDING" else None

        return {
            "is_admin": False,
            "has_access": bool(active_grant),
            "can_request": not active_grant and not pending_request,
            "active_grant": active_grant,
            "pending_request": pending_request,
            "latest_request": latest_request,
        }
    finally:
        if not external_conn:
            conn.close()


def _archive_pending_admin_notifications(conn, user_id):
    archive_notifications(
        entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
        entity_id=int(user_id),
        notification_types=STOCKTAKE_ACCESS_ADMIN_PENDING_NOTIFICATION_TYPES,
        external_conn=conn,
    )


def _archive_requester_notifications(conn, user_id):
    archive_notifications(
        recipient_user_id=int(user_id),
        entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
        entity_id=int(user_id),
        notification_types=STOCKTAKE_ACCESS_REQUESTER_NOTIFICATION_TYPES,
        external_conn=conn,
    )


def submit_stocktake_access_request(*, user_id, username, user_role, request_reason):
    cleaned_reason = str(request_reason or "").strip()
    if not cleaned_reason:
        raise ValueError("Reason is required before requesting stocktake access.")

    if str(user_role or "").strip().lower() == "admin":
        raise ValueError("Administrators already have full stocktake access.")

    user_id = int(user_id)
    username = str(username or "").strip() or "Staff user"

    conn = get_db()
    try:
        conn.execute("BEGIN")

        if _get_active_grant_row(conn, user_id):
            raise ValueError("You already have active stocktake access.")

        existing_request = get_approval_request_by_entity(
            STOCKTAKE_ACCESS_APPROVAL_TYPE,
            STOCKTAKE_ACCESS_ENTITY_TYPE,
            user_id,
            external_conn=conn,
        )
        metadata = {
            "request_reason": cleaned_reason,
            "requester_username": username,
        }

        if not existing_request:
            request_row = create_approval_request(
                approval_type=STOCKTAKE_ACCESS_APPROVAL_TYPE,
                entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
                entity_id=user_id,
                requested_by=user_id,
                requester_role=user_role,
                metadata=metadata,
                external_conn=conn,
            )
        else:
            if existing_request["status"] == "PENDING":
                raise ValueError("You already have a pending stocktake access request.")

            prior_status = existing_request["status"]
            if prior_status == "APPROVED":
                action_type = "EDITED_AFTER_APPROVAL"
            elif prior_status == "REVISIONS_NEEDED":
                action_type = "RESUBMITTED"
            else:
                action_type = "SUBMITTED"

            now = _now()
            conn.execute(
                """
                UPDATE approval_requests
                SET status = %s,
                    last_submitted_at = %s,
                    decision_by = NULL,
                    decision_at = NULL,
                    decision_notes = NULL,
                    is_locked = %s,
                    current_revision_no = current_revision_no + 1,
                    metadata = %s
                WHERE id = %s
                """,
                ("PENDING", now, 0, _jsonb(metadata), int(existing_request["id"])),
            )
            conn.execute(
                """
                INSERT INTO approval_actions (
                    approval_request_id,
                    action_type,
                    from_status,
                    to_status,
                    action_by,
                    action_at,
                    notes
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    int(existing_request["id"]),
                    action_type,
                    prior_status,
                    "PENDING",
                    user_id,
                    now,
                    cleaned_reason,
                ),
            )
            request_row = get_approval_request_by_entity(
                STOCKTAKE_ACCESS_APPROVAL_TYPE,
                STOCKTAKE_ACCESS_ENTITY_TYPE,
                user_id,
                external_conn=conn,
            )

        admin_ids = [
            admin_id
            for admin_id in list_active_user_ids(role="admin", external_conn=conn)
            if int(admin_id) != user_id
        ]
        _archive_pending_admin_notifications(conn, user_id)
        if admin_ids:
            create_notifications_for_users(
                admin_ids,
                "STOCKTAKE_ACCESS_REQUESTED",
                "Stocktake access requested",
                f"{username} requested temporary access to the stocktake pages.",
                category="approval",
                entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
                entity_id=user_id,
                action_url=_admin_stocktake_access_url(),
                created_by=user_id,
                metadata={
                    "approval_request_id": int(request_row["id"]),
                    "request_reason": cleaned_reason,
                    "requester_username": username,
                },
                external_conn=conn,
            )

        conn.commit()
        return request_row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approve_stocktake_access_request(*, approval_request_id, admin_user_id, expires_on, notes=None):
    approval_request_id = int(approval_request_id)
    admin_user_id = int(admin_user_id)
    expiry_date = _coerce_expiry_date(expires_on)
    cleaned_notes = str(notes or "").strip() or None

    conn = get_db()
    try:
        conn.execute("BEGIN")

        request_row = conn.execute(
            """
            SELECT *
            FROM approval_requests
            WHERE id = %s
            FOR UPDATE
            """,
            (approval_request_id,),
        ).fetchone()
        if not request_row:
            raise ValueError("Stocktake access request not found.")
        if request_row["approval_type"] != STOCKTAKE_ACCESS_APPROVAL_TYPE or request_row["entity_type"] != STOCKTAKE_ACCESS_ENTITY_TYPE:
            raise ValueError("Approval request does not belong to stocktake access.")
        if request_row["status"] == "PENDING" and _get_active_grant_row(conn, request_row["entity_id"]):
            raise ValueError("This user already has active stocktake access.")

        approved_row = approve_request(
            approval_request_id=approval_request_id,
            admin_user_id=admin_user_id,
            notes=cleaned_notes,
            external_conn=conn,
        )

        user_id = int(request_row["entity_id"])
        expires_at = datetime.combine(expiry_date, time(23, 59, 59))

        conn.execute(
            """
            UPDATE stocktake_access_grants
            SET revoked_at = NOW(),
                revoked_by = %s,
                revoke_notes = %s
            WHERE user_id = %s
              AND revoked_at IS NULL
              AND expires_at >= NOW()
            """,
            (
                admin_user_id,
                "Superseded by a newer stocktake access approval.",
                user_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO stocktake_access_grants (
                approval_request_id,
                user_id,
                granted_by,
                granted_at,
                expires_at,
                grant_notes
            )
            VALUES (%s, %s, %s, NOW(), %s, %s)
            """,
            (
                approval_request_id,
                user_id,
                admin_user_id,
                expires_at,
                cleaned_notes,
            ),
        )

        _archive_pending_admin_notifications(conn, user_id)
        _archive_requester_notifications(conn, user_id)
        create_notification(
            user_id,
            "STOCKTAKE_ACCESS_GRANTED",
            "Stocktake access granted",
            f"Your stocktake access was approved and will expire on {format_date(expires_at, show_time=True)}.",
            category="approval",
            entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
            entity_id=user_id,
            action_url=_requester_stocktake_url(),
            created_by=admin_user_id,
            metadata={
                "approval_request_id": approval_request_id,
                "expires_at": expires_at.isoformat(sep=" "),
                "expires_on": expiry_date.isoformat(),
            },
            external_conn=conn,
        )

        conn.commit()
        return approved_row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_stocktake_access_request(*, approval_request_id, admin_user_id, notes):
    cleaned_notes = str(notes or "").strip()
    if not cleaned_notes:
        raise ValueError("Add a note before rejecting the request.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        request_row = conn.execute(
            """
            SELECT *
            FROM approval_requests
            WHERE id = %s
            FOR UPDATE
            """,
            (int(approval_request_id),),
        ).fetchone()
        if not request_row:
            raise ValueError("Stocktake access request not found.")
        if request_row["approval_type"] != STOCKTAKE_ACCESS_APPROVAL_TYPE or request_row["entity_type"] != STOCKTAKE_ACCESS_ENTITY_TYPE:
            raise ValueError("Approval request does not belong to stocktake access.")

        row = cancel_request(
            approval_request_id=int(approval_request_id),
            actor_id=int(admin_user_id),
            actor_role="admin",
            notes=cleaned_notes,
            external_conn=conn,
        )

        user_id = int(request_row["entity_id"])
        _archive_pending_admin_notifications(conn, user_id)
        _archive_requester_notifications(conn, user_id)
        create_notification(
            user_id,
            "STOCKTAKE_ACCESS_REJECTED",
            "Stocktake access request rejected",
            "Your stocktake access request was rejected. You can submit a new request when needed.",
            category="approval",
            entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
            entity_id=user_id,
            action_url=None,
            created_by=int(admin_user_id),
            metadata={
                "approval_request_id": int(approval_request_id),
                "decision_notes": cleaned_notes,
            },
            external_conn=conn,
        )

        conn.commit()
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def revoke_stocktake_access(*, approval_request_id, admin_user_id, notes):
    cleaned_notes = str(notes or "").strip()
    if not cleaned_notes:
        raise ValueError("Add a note before revoking access.")

    conn = get_db()
    try:
        conn.execute("BEGIN")
        request_row = conn.execute(
            """
            SELECT *
            FROM approval_requests
            WHERE id = %s
            FOR UPDATE
            """,
            (int(approval_request_id),),
        ).fetchone()
        if not request_row:
            raise ValueError("Stocktake access request not found.")
        if request_row["approval_type"] != STOCKTAKE_ACCESS_APPROVAL_TYPE or request_row["entity_type"] != STOCKTAKE_ACCESS_ENTITY_TYPE:
            raise ValueError("Approval request does not belong to stocktake access.")

        active_grant = conn.execute(
            """
            SELECT *
            FROM stocktake_access_grants
            WHERE approval_request_id = %s
              AND revoked_at IS NULL
              AND expires_at >= NOW()
            ORDER BY expires_at DESC, id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (int(approval_request_id),),
        ).fetchone()
        if not active_grant:
            raise ValueError("There is no active stocktake access to revoke.")

        conn.execute(
            """
            UPDATE stocktake_access_grants
            SET revoked_at = NOW(),
                revoked_by = %s,
                revoke_notes = %s
            WHERE id = %s
            """,
            (int(admin_user_id), cleaned_notes, int(active_grant["id"])),
        )

        if request_row["status"] != "CANCELLED":
            cancel_request(
                approval_request_id=int(approval_request_id),
                actor_id=int(admin_user_id),
                actor_role="admin",
                notes=cleaned_notes,
                external_conn=conn,
            )

        user_id = int(request_row["entity_id"])
        _archive_requester_notifications(conn, user_id)
        create_notification(
            user_id,
            "STOCKTAKE_ACCESS_REVOKED",
            "Stocktake access revoked",
            "Your temporary stocktake access was revoked by an administrator.",
            category="approval",
            entity_type=STOCKTAKE_ACCESS_ENTITY_TYPE,
            entity_id=user_id,
            action_url=None,
            created_by=int(admin_user_id),
            metadata={
                "approval_request_id": int(approval_request_id),
                "revoke_notes": cleaned_notes,
            },
            external_conn=conn,
        )

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_stocktake_access_requests(limit=100):
    try:
        safe_limit = max(1, min(int(limit or 100), 200))
    except (TypeError, ValueError):
        safe_limit = 100

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                ar.*,
                requester.username AS requested_by_username,
                decider.username AS decision_by_username,
                latest_grant.id AS latest_grant_id,
                latest_grant.granted_at AS latest_granted_at,
                latest_grant.expires_at AS latest_expires_at,
                latest_grant.revoked_at AS latest_revoked_at,
                latest_grant.revoke_notes AS latest_revoke_notes,
                latest_grant.grant_notes AS latest_grant_notes,
                latest_grantor.username AS latest_granted_by_username,
                active_grant.id AS active_grant_id,
                active_grant.expires_at AS active_expires_at,
                active_grantor.username AS active_granted_by_username
            FROM approval_requests ar
            JOIN users requester ON requester.id = ar.requested_by
            LEFT JOIN users decider ON decider.id = ar.decision_by
            LEFT JOIN LATERAL (
                SELECT *
                FROM stocktake_access_grants sag
                WHERE sag.user_id = ar.entity_id
                ORDER BY sag.granted_at DESC, sag.id DESC
                LIMIT 1
            ) latest_grant ON TRUE
            LEFT JOIN users latest_grantor ON latest_grantor.id = latest_grant.granted_by
            LEFT JOIN LATERAL (
                SELECT *
                FROM stocktake_access_grants sag
                WHERE sag.user_id = ar.entity_id
                  AND sag.revoked_at IS NULL
                  AND sag.expires_at >= NOW()
                ORDER BY sag.expires_at DESC, sag.id DESC
                LIMIT 1
            ) active_grant ON TRUE
            LEFT JOIN users active_grantor ON active_grantor.id = active_grant.granted_by
            WHERE ar.approval_type = %s
              AND ar.entity_type = %s
            ORDER BY COALESCE(ar.last_submitted_at, ar.requested_at) DESC, ar.id DESC
            LIMIT %s
            """,
            (
                STOCKTAKE_ACCESS_APPROVAL_TYPE,
                STOCKTAKE_ACCESS_ENTITY_TYPE,
                safe_limit,
            ),
        ).fetchall()
        return [_serialize_access_request_row(row) for row in rows]
    finally:
        conn.close()
