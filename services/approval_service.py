import psycopg2.extras

from db.database import get_db
from utils.formatters import format_date
from utils.timezone import now_local_str

APPROVAL_STATUSES = {"PENDING", "REVISIONS_NEEDED", "APPROVED", "CANCELLED"}
APPROVAL_ACTIONS = {
    "SUBMITTED",
    "AUTO_APPROVED",
    "APPROVED",
    "REVISIONS_REQUESTED",
    "RESUBMITTED",
    "EDITED_AFTER_APPROVAL",
    "REOPENED_AFTER_EDIT",
    "CANCELLED_BY_REQUESTER",
    "CANCELLED_BY_ADMIN",
}


def _now():
    return now_local_str()


def _normalize_upper(value, field_name):
    normalized = str(value or "").strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} is required.")
    return normalized


def _coerce_metadata(metadata):
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object.")
    return metadata


def _jsonb(value):
    return psycopg2.extras.Json(value)


def _serialize_request(row):
    if not row:
        return None

    data = dict(row)
    metadata = data.get("metadata")
    data["metadata"] = metadata if isinstance(metadata, dict) else (metadata or {})
    data["requested_at"] = format_date(data.get("requested_at"), show_time=True)
    data["last_submitted_at"] = format_date(data.get("last_submitted_at"), show_time=True)
    data["decision_at"] = format_date(data.get("decision_at"), show_time=True)
    return data


def _insert_action(conn, approval_request_id, action_type, action_by, from_status, to_status, notes=None):
    if action_type not in APPROVAL_ACTIONS:
        raise ValueError("Invalid approval action.")

    row = conn.execute(
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
        RETURNING id
        """,
        (
            approval_request_id,
            action_type,
            from_status,
            to_status,
            action_by,
            _now(),
            notes,
        ),
    ).fetchone()
    return row["id"]


def _insert_revision_items(conn, approval_request_id, approval_action_id, revision_items):
    for item in revision_items or []:
        conn.execute(
            """
            INSERT INTO approval_revision_items (
                approval_request_id,
                approval_action_id,
                item_id,
                item_name,
                quantity_ordered,
                quantity_received,
                revision_note,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                approval_request_id,
                approval_action_id,
                item.get("item_id"),
                item.get("item_name") or "Unknown Item",
                item.get("quantity_ordered"),
                item.get("quantity_received") or 0,
                item.get("revision_note"),
                _now(),
            ),
        )


def _insert_resubmission_changes(conn, approval_request_id, approval_action_id, change_entries):
    for entry in change_entries or []:
        conn.execute(
            """
            INSERT INTO approval_resubmission_changes (
                approval_request_id,
                approval_action_id,
                change_scope,
                item_id,
                item_name,
                field_name,
                before_value,
                after_value,
                change_label,
                created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                approval_request_id,
                approval_action_id,
                entry.get("change_scope"),
                entry.get("item_id"),
                entry.get("item_name"),
                entry.get("field_name"),
                entry.get("before_value"),
                entry.get("after_value"),
                entry.get("change_label"),
                _now(),
            ),
        )


def _get_request_row(conn, approval_request_id):
    return conn.execute(
        """
        SELECT *
        FROM approval_requests
        WHERE id = %s
        """,
        (approval_request_id,),
    ).fetchone()


def _get_request_row_by_entity(conn, approval_type, entity_type, entity_id):
    return conn.execute(
        """
        SELECT *
        FROM approval_requests
        WHERE approval_type = %s
          AND entity_type = %s
          AND entity_id = %s
        """,
        (
            _normalize_upper(approval_type, "approval_type"),
            str(entity_type or "").strip().lower(),
            int(entity_id),
        ),
    ).fetchone()


def _assert_requester_ownership(request_row, requester_id):
    if int(request_row["requested_by"]) != int(requester_id):
        raise ValueError("You do not own this approval request.")


def _lock_flag_for_status(status):
    return 1 if status in {"CANCELLED"} else 0


def create_approval_request(
    approval_type,
    entity_type,
    entity_id,
    requested_by,
    requester_role,
    metadata=None,
    external_conn=None,
):
    approval_type = _normalize_upper(approval_type, "approval_type")
    entity_type = str(entity_type or "").strip().lower()
    if not entity_type:
        raise ValueError("entity_type is required.")

    try:
        entity_id = int(entity_id)
        requested_by = int(requested_by)
    except (TypeError, ValueError):
        raise ValueError("entity_id and requested_by must be valid integers.")

    requester_role = str(requester_role or "").strip().lower()
    metadata = _coerce_metadata(metadata)

    conn = external_conn if external_conn else get_db()
    try:
        if not external_conn:
            conn.execute("BEGIN")

        existing = conn.execute(
            """
            SELECT id
            FROM approval_requests
            WHERE approval_type = %s
              AND entity_type = %s
              AND entity_id = %s
            """,
            (approval_type, entity_type, entity_id),
        ).fetchone()
        if existing:
            raise ValueError("Approval request already exists for this record.")

        status = "APPROVED" if requester_role == "admin" else "PENDING"
        now = _now()
        row = conn.execute(
            """
            INSERT INTO approval_requests (
                approval_type,
                entity_type,
                entity_id,
                status,
                requested_by,
                requested_at,
                last_submitted_at,
                decision_by,
                decision_at,
                decision_notes,
                is_locked,
                current_revision_no,
                metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                approval_type,
                entity_type,
                entity_id,
                status,
                requested_by,
                now,
                now,
                requested_by if status == "APPROVED" else None,
                now if status == "APPROVED" else None,
                None,
                _lock_flag_for_status(status),
                0,
                _jsonb(metadata),
            ),
        ).fetchone()

        _insert_action(
            conn,
            row["id"],
            "SUBMITTED",
            requested_by,
            None,
            status,
            None,
        )

        if status == "APPROVED":
            _insert_action(
                conn,
                row["id"],
                "AUTO_APPROVED",
                requested_by,
                "PENDING",
                "APPROVED",
                "Automatically approved because the requester is an admin.",
            )

        if not external_conn:
            conn.commit()
        return get_approval_request(row["id"], external_conn=conn)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def get_approval_request(approval_request_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT
                ar.*,
                requester.username AS requested_by_username,
                decider.username AS decision_by_username
            FROM approval_requests ar
            JOIN users requester ON requester.id = ar.requested_by
            LEFT JOIN users decider ON decider.id = ar.decision_by
            WHERE ar.id = %s
            """,
            (approval_request_id,),
        ).fetchone()
    finally:
        if not external_conn:
            conn.close()

    return _serialize_request(row)


def get_approval_request_by_entity(approval_type, entity_type, entity_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        row = conn.execute(
            """
            SELECT
                ar.*,
                requester.username AS requested_by_username,
                decider.username AS decision_by_username
            FROM approval_requests ar
            JOIN users requester ON requester.id = ar.requested_by
            LEFT JOIN users decider ON decider.id = ar.decision_by
            WHERE ar.approval_type = %s
              AND ar.entity_type = %s
              AND ar.entity_id = %s
            """,
            (
                _normalize_upper(approval_type, "approval_type"),
                str(entity_type or "").strip().lower(),
                int(entity_id),
            ),
        ).fetchone()
    finally:
        if not external_conn:
            conn.close()

    return _serialize_request(row)


def get_approval_actions(approval_request_id, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                aa.*,
                u.username AS action_by_username
            FROM approval_actions aa
            LEFT JOIN users u ON u.id = aa.action_by
            WHERE aa.approval_request_id = %s
            ORDER BY aa.action_at DESC, aa.id DESC
            """,
            (approval_request_id,),
        ).fetchall()

        action_ids = [row["id"] for row in rows]
        revision_items_by_action = {}
        change_entries_by_action = {}
        if action_ids:
            revision_rows = conn.execute(
                """
                SELECT *
                FROM approval_revision_items
                WHERE approval_action_id = ANY(%s)
                ORDER BY id ASC
                """,
                (action_ids,),
            ).fetchall()
            for row in revision_rows:
                item = dict(row)
                item["created_at"] = format_date(item.get("created_at"), show_time=True)
                revision_items_by_action.setdefault(item["approval_action_id"], []).append(item)

            change_rows = conn.execute(
                """
                SELECT *
                FROM approval_resubmission_changes
                WHERE approval_action_id = ANY(%s)
                ORDER BY id ASC
                """,
                (action_ids,),
            ).fetchall()
            for row in change_rows:
                change = dict(row)
                change["created_at"] = format_date(change.get("created_at"), show_time=True)
                change_entries_by_action.setdefault(change["approval_action_id"], []).append(change)
    finally:
        if not external_conn:
            conn.close()

    actions = []
    for row in rows:
        action = dict(row)
        action["action_at"] = format_date(action.get("action_at"), show_time=True)
        action["revision_items"] = revision_items_by_action.get(action["id"], [])
        action["change_entries"] = change_entries_by_action.get(action["id"], [])
        actions.append(action)
    return actions


def get_approval_request_with_history(approval_request_id, external_conn=None):
    request_data = get_approval_request(approval_request_id, external_conn=external_conn)
    if not request_data:
        return None
    request_data["actions"] = get_approval_actions(approval_request_id, external_conn=external_conn)
    latest_revision_action = next(
        (
            action for action in request_data["actions"]
            if action.get("action_type") == "REVISIONS_REQUESTED" and action.get("revision_items")
        ),
        None,
    )
    request_data["latest_revision_items"] = (
        latest_revision_action.get("revision_items", [])
        if latest_revision_action
        else []
    )
    latest_resubmission_action = next(
        (
            action for action in request_data["actions"]
            if action.get("action_type") in {"RESUBMITTED", "EDITED_AFTER_APPROVAL"}
        ),
        None,
    )
    request_data["latest_resubmission_changes"] = (
        latest_resubmission_action.get("change_entries", [])
        if latest_resubmission_action
        else []
    )
    return request_data


def list_approval_requests(status=None, approval_type=None):
    where_clauses = []
    params = []

    if status:
        normalized_status = _normalize_upper(status, "status")
        if normalized_status not in APPROVAL_STATUSES:
            raise ValueError("Invalid approval status filter.")
        where_clauses.append("ar.status = %s")
        params.append(normalized_status)

    if approval_type:
        normalized_type = _normalize_upper(approval_type, "approval_type")
        where_clauses.append("ar.approval_type = %s")
        params.append(normalized_type)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    conn = get_db()
    try:
        rows = conn.execute(
            f"""
            SELECT
                ar.*,
                requester.username AS requested_by_username,
                decider.username AS decision_by_username
            FROM approval_requests ar
            JOIN users requester ON requester.id = ar.requested_by
            LEFT JOIN users decider ON decider.id = ar.decision_by
            {where_sql}
            ORDER BY
                CASE
                    WHEN ar.status = 'PENDING' THEN 0
                    WHEN ar.status = 'REVISIONS_NEEDED' THEN 1
                    WHEN ar.status = 'APPROVED' THEN 2
                    ELSE 3
                END,
                ar.last_submitted_at DESC,
                ar.id DESC
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    return [_serialize_request(row) for row in rows]


def approve_request(approval_request_id, admin_user_id, notes=None, external_conn=None):
    conn = external_conn if external_conn else get_db()
    try:
        if not external_conn:
            conn.execute("BEGIN")
        row = _get_request_row(conn, approval_request_id)
        if not row:
            raise ValueError("Approval request not found.")
        if row["status"] == "APPROVED":
            raise ValueError("Approval request is already approved.")
        if row["status"] == "CANCELLED":
            raise ValueError("Cancelled approval request cannot be approved.")

        now = _now()
        conn.execute(
            """
            UPDATE approval_requests
            SET status = %s,
                decision_by = %s,
                decision_at = %s,
                decision_notes = %s,
                is_locked = %s
            WHERE id = %s
            """,
            ("APPROVED", admin_user_id, now, notes or None, 0, approval_request_id),
        )
        _insert_action(
            conn,
            approval_request_id,
            "APPROVED",
            admin_user_id,
            row["status"],
            "APPROVED",
            notes,
        )
        if not external_conn:
            conn.commit()
        return get_approval_request(approval_request_id, external_conn=conn)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def request_revisions(approval_request_id, admin_user_id, notes, revision_items=None, external_conn=None):
    cleaned_notes = str(notes or "").strip() or None
    cleaned_revision_items = []
    for item in revision_items or []:
        revision_note = str(item.get("revision_note") or "").strip()
        if not revision_note:
            raise ValueError("Each item revision must include a note.")
        cleaned_revision_items.append(
            {
                "item_id": item.get("item_id"),
                "item_name": str(item.get("item_name") or "").strip() or "Unknown Item",
                "quantity_ordered": item.get("quantity_ordered"),
                "quantity_received": item.get("quantity_received") or 0,
                "revision_note": revision_note,
            }
        )

    if not cleaned_notes and not cleaned_revision_items:
        raise ValueError("Add a summary note or at least one item revision.")

    conn = external_conn if external_conn else get_db()
    try:
        if not external_conn:
            conn.execute("BEGIN")
        row = _get_request_row(conn, approval_request_id)
        if not row:
            raise ValueError("Approval request not found.")
        if row["status"] == "CANCELLED":
            raise ValueError("Cancelled request cannot be returned for revisions.")

        now = _now()
        conn.execute(
            """
            UPDATE approval_requests
            SET status = %s,
                decision_by = %s,
                decision_at = %s,
                decision_notes = %s,
                is_locked = %s
            WHERE id = %s
            """,
            ("REVISIONS_NEEDED", admin_user_id, now, cleaned_notes, 0, approval_request_id),
        )
        action_id = _insert_action(
            conn,
            approval_request_id,
            "REVISIONS_REQUESTED",
            admin_user_id,
            row["status"],
            "REVISIONS_NEEDED",
            cleaned_notes,
        )
        _insert_revision_items(
            conn,
            approval_request_id=approval_request_id,
            approval_action_id=action_id,
            revision_items=cleaned_revision_items,
        )
        if not external_conn:
            conn.commit()
        return get_approval_request(approval_request_id, external_conn=conn)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def cancel_request(approval_request_id, actor_id, actor_role, notes=None, external_conn=None):
    actor_role = str(actor_role or "").strip().lower()
    cleaned_notes = str(notes or "").strip()

    conn = external_conn if external_conn else get_db()
    try:
        if not external_conn:
            conn.execute("BEGIN")
        row = _get_request_row(conn, approval_request_id)
        if not row:
            raise ValueError("Approval request not found.")
        if row["status"] == "APPROVED" and actor_role != "admin":
            raise ValueError("Approved request cannot be cancelled by the requester.")
        if row["status"] == "CANCELLED":
            raise ValueError("Approval request is already cancelled.")

        if actor_role == "admin":
            action_type = "CANCELLED_BY_ADMIN"
            if not cleaned_notes:
                raise ValueError("Cancellation notes are required.")
        else:
            _assert_requester_ownership(row, actor_id)
            action_type = "CANCELLED_BY_REQUESTER"

        now = _now()
        conn.execute(
            """
            UPDATE approval_requests
            SET status = %s,
                decision_by = %s,
                decision_at = %s,
                decision_notes = %s,
                is_locked = %s
            WHERE id = %s
            """,
            ("CANCELLED", actor_id, now, cleaned_notes or None, 1, approval_request_id),
        )
        _insert_action(
            conn,
            approval_request_id,
            action_type,
            actor_id,
            row["status"],
            "CANCELLED",
            cleaned_notes or None,
        )
        if not external_conn:
            conn.commit()
        return get_approval_request(approval_request_id, external_conn=conn)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def resubmit_request(approval_request_id, requester_id, metadata=None, notes=None, change_entries=None, external_conn=None):
    metadata = _coerce_metadata(metadata) if metadata is not None else None
    cleaned_notes = str(notes or "").strip()
    cleaned_change_entries = []
    for entry in change_entries or []:
        change_scope = str(entry.get("change_scope") or "").strip().upper()
        field_name = str(entry.get("field_name") or "").strip()
        change_label = str(entry.get("change_label") or "").strip()
        if change_scope not in {"HEADER", "ITEM"} or not field_name or not change_label:
            raise ValueError("Invalid resubmission change entry.")
        cleaned_change_entries.append(
            {
                "change_scope": change_scope,
                "item_id": entry.get("item_id"),
                "item_name": entry.get("item_name"),
                "field_name": field_name,
                "before_value": entry.get("before_value"),
                "after_value": entry.get("after_value"),
                "change_label": change_label,
            }
        )

    conn = external_conn if external_conn else get_db()
    try:
        if not external_conn:
            conn.execute("BEGIN")
        row = _get_request_row(conn, approval_request_id)
        if not row:
            raise ValueError("Approval request not found.")
        _assert_requester_ownership(row, requester_id)

        if row["status"] not in {"REVISIONS_NEEDED", "APPROVED"}:
            raise ValueError("Only requests marked for revisions or approved requests can be resubmitted.")

        next_metadata = metadata if metadata is not None else (row["metadata"] or {})
        prior_status = row["status"]
        action_type = "RESUBMITTED" if prior_status == "REVISIONS_NEEDED" else "EDITED_AFTER_APPROVAL"
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
            ("PENDING", now, 0, _jsonb(next_metadata), approval_request_id),
        )
        action_id = _insert_action(
            conn,
            approval_request_id,
            action_type,
            requester_id,
            prior_status,
            "PENDING",
            cleaned_notes or None,
        )
        _insert_resubmission_changes(
            conn,
            approval_request_id=approval_request_id,
            approval_action_id=action_id,
            change_entries=cleaned_change_entries,
        )
        if not external_conn:
            conn.commit()
        return get_approval_request(approval_request_id, external_conn=conn)
    except Exception:
        if not external_conn:
            conn.rollback()
        raise
    finally:
        if not external_conn:
            conn.close()


def can_requester_edit_request(request_row, requester_id):
    if not request_row:
        return False
    try:
        _assert_requester_ownership(request_row, requester_id)
    except ValueError:
        return False
    return request_row["status"] in {"REVISIONS_NEEDED", "APPROVED"} and int(request_row["is_locked"] or 0) == 0


def is_request_locked(request_row):
    if not request_row:
        return True
    return int(request_row["is_locked"] or 0) == 1
