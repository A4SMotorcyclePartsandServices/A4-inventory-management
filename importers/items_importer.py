import csv
from db.database import get_db

def normalize_header(text):
    if not text:
        return ""
    return (
        text.strip()
            .lower()
            .replace("%", "")
            .replace("/", " ")
            .replace("-", " ")
            .replace("_", " ")
    )

def import_items_csv(file):
    if not file or not file.filename.endswith(".csv"):
        return False

    conn = get_db()

    lines = file.stream.read().decode("utf-8", errors="ignore").splitlines()
    reader = csv.DictReader(lines)

    # Normalize headers
    reader.fieldnames = [normalize_header(h) for h in reader.fieldnames]

    imported = 0
    skipped = 0

    for row in reader:
        # Clean row keys and values
        row = {
            normalize_header(k): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }

        def get_value(key, default=None, cast=str):
            raw = row.get(key)
            if raw in (None, ""):
                return default
            try:
                if cast == float:
                    raw = str(raw).replace("%", "")
                    raw = "".join(c for c in raw if c.isdigit() or c in ".-")
                return cast(raw)
            except:
                return default

        name = get_value("name", "", str)

        if not name:
            skipped += 1
            continue

        # Using ON CONFLICT (Upsert) logic
        conn.execute("""
            INSERT INTO items (
                name,
                description,
                pack_size,
                vendor_price,
                cost_per_piece,
                a4s_selling_price,
                markup,
                category,
                reorder_level
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                pack_size = excluded.pack_size,
                vendor_price = excluded.vendor_price,
                cost_per_piece = excluded.cost_per_piece,
                a4s_selling_price = excluded.a4s_selling_price,
                markup = excluded.markup,
                category = excluded.category,
                reorder_level = excluded.reorder_level
        """, (
            name,
            get_value("description", "", str),
            get_value("pack size", "", str),
            get_value("vendor price pc", 0.0, float),
            get_value("cost per piece", 0.0, float),
            get_value("a4s selling price", 0.0, float),
            get_value("mark up", 0.0, float) / 100,  # store as decimal
            get_value("pms acc svc", "", str),
            get_value("minimum inv level", 0, int),
        ))

        imported += 1

    conn.commit()
    conn.close()

    print(f"Items import complete. Processed: {imported}, Skipped: {skipped}")
    return True
