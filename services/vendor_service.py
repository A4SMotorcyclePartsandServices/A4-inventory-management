from psycopg2 import errors as pg_errors

from db.database import get_db
from utils.formatters import norm_text


def get_vendors_panel_records():
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                id,
                vendor_name,
                address,
                contact_person,
                contact_no,
                email,
                is_active,
                created_at,
                updated_at
            FROM vendors
            ORDER BY vendor_name ASC, id ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_vendor_payload(vendor_id, active_only=False):
    conn = get_db()
    try:
        query = """
            SELECT
                id,
                vendor_name,
                address,
                contact_person,
                contact_no,
                email,
                is_active
            FROM vendors
            WHERE id = %s
        """
        params = [vendor_id]
        if active_only:
            query += " AND is_active = 1"

        row = conn.execute(query, tuple(params)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def add_vendor_record(vendor_name, address, contact_person, contact_no, email):
    normalized_vendor_name = norm_text(vendor_name)
    normalized_address = str(address or "").strip()
    normalized_contact_person = norm_text(contact_person)
    normalized_contact_no = str(contact_no or "").strip()
    normalized_email = str(email or "").strip()

    if not all([
        normalized_vendor_name,
        normalized_address,
        normalized_contact_person,
        normalized_contact_no,
        normalized_email,
    ]):
        return {
            "status": "missing_fields",
            "message": "Vendor name, address, contact person, contact no, and email are required.",
        }

    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT id
            FROM vendors
            WHERE LOWER(TRIM(vendor_name)) = %s
            LIMIT 1
            """,
            (normalized_vendor_name.lower(),),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "name": normalized_vendor_name}

        row = conn.execute(
            """
            INSERT INTO vendors (
                vendor_name, address, contact_person, contact_no, email, is_active, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, 1, NOW())
            RETURNING
                id,
                vendor_name,
                address,
                contact_person,
                contact_no,
                email,
                is_active
            """,
            (
                normalized_vendor_name,
                normalized_address,
                normalized_contact_person,
                normalized_contact_no,
                normalized_email,
            ),
        ).fetchone()
        conn.commit()
        return {"status": "ok", "vendor": dict(row)}
    except pg_errors.UniqueViolation:
        conn.rollback()
        return {"status": "duplicate", "name": normalized_vendor_name}
    finally:
        conn.close()


def update_vendor_record(vendor_id, vendor_name, address, contact_person, contact_no, email):
    normalized_vendor_name = norm_text(vendor_name)
    normalized_address = str(address or "").strip()
    normalized_contact_person = norm_text(contact_person)
    normalized_contact_no = str(contact_no or "").strip()
    normalized_email = str(email or "").strip()

    if not all([
        normalized_vendor_name,
        normalized_address,
        normalized_contact_person,
        normalized_contact_no,
        normalized_email,
    ]):
        return {
            "status": "missing_fields",
            "message": "Vendor name, address, contact person, contact no, and email are required.",
        }

    conn = get_db()
    try:
        vendor = conn.execute(
            """
            SELECT id
            FROM vendors
            WHERE id = %s
            LIMIT 1
            """,
            (vendor_id,),
        ).fetchone()
        if not vendor:
            return {"status": "missing"}

        duplicate = conn.execute(
            """
            SELECT id
            FROM vendors
            WHERE LOWER(TRIM(vendor_name)) = %s
              AND id <> %s
            LIMIT 1
            """,
            (normalized_vendor_name.lower(), vendor_id),
        ).fetchone()
        if duplicate:
            return {"status": "duplicate", "name": normalized_vendor_name}

        row = conn.execute(
            """
            UPDATE vendors
            SET vendor_name = %s,
                address = %s,
                contact_person = %s,
                contact_no = %s,
                email = %s,
                updated_at = NOW()
            WHERE id = %s
            RETURNING
                id,
                vendor_name,
                address,
                contact_person,
                contact_no,
                email,
                is_active
            """,
            (
                normalized_vendor_name,
                normalized_address,
                normalized_contact_person,
                normalized_contact_no,
                normalized_email,
                vendor_id,
            ),
        ).fetchone()
        conn.commit()
        return {"status": "ok", "vendor": dict(row)}
    except pg_errors.UniqueViolation:
        conn.rollback()
        return {"status": "duplicate", "name": normalized_vendor_name}
    finally:
        conn.close()


def toggle_vendor_active_status(vendor_id):
    conn = get_db()
    try:
        vendor = conn.execute(
            """
            SELECT id, vendor_name, is_active
            FROM vendors
            WHERE id = %s
            LIMIT 1
            """,
            (vendor_id,),
        ).fetchone()
        if not vendor:
            return {"status": "missing"}

        new_status = 0 if int(vendor["is_active"] or 0) == 1 else 1
        conn.execute(
            """
            UPDATE vendors
            SET is_active = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (new_status, vendor_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "name": vendor["vendor_name"],
            "new_status": new_status,
        }
    finally:
        conn.close()
