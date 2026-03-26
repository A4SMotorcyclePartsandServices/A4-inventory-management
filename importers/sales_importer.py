import csv
import difflib
from db.database import get_db

def import_sales_csv(file):
    if not file or not file.filename.endswith(".csv"):
        return False, "Invalid file"

    conn = get_db()

    lines = file.stream.read().decode("utf-8", errors="ignore").splitlines()
    reader = csv.DictReader(lines)

    imported = 0
    skipped = 0

    skip_reasons = {
        "non_inventory_sale": 0,
        "missing_fields": 0,
        "bad_quantity": 0,
        "item_not_found": 0,
        "other": 0
    }

    skipped_rows = []

    items = conn.execute("SELECT id, name FROM items").fetchall()
    item_lookup = {
        item["name"].strip().lower(): item["id"]
        for item in items
    }

    def find_item_id(item_name):
        key = item_name.strip().lower()

        if key in item_lookup:
            return item_lookup[key]

        matches = difflib.get_close_matches(
            key,
            item_lookup.keys(),
            n=1,
            cutoff=0.85
        )
        return item_lookup[matches[0]] if matches else None

    for row in reader:
        try:
            normalized = {k.strip().lower(): v for k, v in row.items()}

            sales_type = (normalized.get("sales type") or "").strip().lower()
            if sales_type != "inventory":
                skip_reasons["non_inventory_sale"] += 1
                skipped += 1
                continue

            item_name = (normalized.get("part number") or "").strip()
            qty_raw = normalized.get("qty pc")
            date_raw = normalized.get("tr date")

            if not item_name or not qty_raw or not date_raw:
                skip_reasons["missing_fields"] += 1
                skipped += 1
                continue

            try:
                quantity = int(float(qty_raw))
            except:
                skip_reasons["bad_quantity"] += 1
                skipped += 1
                continue

            if quantity <= 0:
                skip_reasons["bad_quantity"] += 1
                skipped += 1
                continue

            item_id = find_item_id(item_name)
            if not item_id:
                skip_reasons["item_not_found"] += 1
                skipped += 1
                continue

            conn.execute("""
                INSERT INTO inventory_transactions
                (item_id, quantity, transaction_type, transaction_date)
                VALUES (%s, %s, 'OUT', %s)
            """, (item_id, quantity, date_raw))

            imported += 1

        except Exception as e:
            skip_reasons["other"] += 1
            skipped += 1

    conn.commit()
    conn.close()

    return True, {
        "imported": imported,
        "skipped": skipped,
        "skip_reasons": skip_reasons
    }
