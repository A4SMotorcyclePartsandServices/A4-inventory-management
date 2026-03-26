import csv
from datetime import datetime
from db.database import get_db

# 🔒 Single source of truth for this import
BASELINE_SNAPSHOT_DATE = "2026-03-26 00:00:00"


def normalize_name(value: str) -> str:
    """
    Normalize item names for STRICT matching only.
    - strip leading/trailing spaces
    - collapse multiple spaces
    - lowercase
    """
    if not value:
        return ""
    return " ".join(value.strip().split()).lower()


def import_inventory_csv(file):
    if not file or not file.filename.endswith(".csv"):
        return False, "Invalid file"

    conn = get_db()
    skipped_rows = []

    # 🔹 Preload items (Inventory ID must match items.name)
    items = conn.execute("SELECT id, name FROM items").fetchall()
    item_lookup = {
        normalize_name(item["name"]): item["id"]
        for item in items
    }

    lines = file.stream.read().decode("utf-8", errors="ignore").splitlines()
    reader = csv.DictReader(lines)

    imported = 0
    skipped = 0

    skip_reasons = {
        "missing_fields": 0,
        "bad_quantity": 0,
        "item_not_found": 0,
        "zero_quantity": 0
    }

    for row in reader:
        # Normalize headers
        normalized = {k.strip().lower(): v for k, v in row.items()}

        raw_item_name = normalized.get("inventory id") or ""
        raw_qty = normalized.get("quantity on hand") or ""

        item_name = normalize_name(raw_item_name)
        qty_raw = raw_qty.strip()

        # 1️⃣ Required fields
        if not item_name or not qty_raw:
            skipped += 1
            skip_reasons["missing_fields"] += 1
            continue

        # 2️⃣ Clean quantity
        try:
            quantity = int(float(qty_raw.replace(",", "")))
        except ValueError:
            skipped += 1
            skip_reasons["bad_quantity"] += 1
            continue

        # 3️⃣ Zero or negative stock → no baseline transaction
        if quantity <= 0:
            skipped += 1
            skip_reasons["zero_quantity"] += 1
            continue

        # 4️⃣ STRICT item match (after whitespace normalization only)
        item_id = item_lookup.get(item_name)
        if not item_id:
            skipped += 1
            skip_reasons["item_not_found"] += 1
            skipped_rows.append({
                "inventory_id": raw_item_name,
                "normalized_inventory_id": item_name,
                "quantity_on_hand": qty_raw,
                "reason": "Item not found in items table"
            })
            continue

        # 5️⃣ Insert BASELINE stock as a single IN transaction
        conn.execute("""
            INSERT INTO inventory_transactions
            (item_id, quantity, transaction_type, transaction_date)
            VALUES (%s, %s, 'IN', %s)
        """, (
            item_id,
            quantity,
            BASELINE_SNAPSHOT_DATE
        ))

        imported += 1

    conn.commit()
    conn.close()

    if skipped_rows:
        with open("skipped_inventory_rows.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=skipped_rows[0].keys()
            )
            writer.writeheader()
            writer.writerows(skipped_rows)

    return True, {
        "imported": imported,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
        "snapshot_date": BASELINE_SNAPSHOT_DATE
    }
