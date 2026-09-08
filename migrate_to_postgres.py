import os
from datetime import datetime
from dotenv import load_dotenv
from pymongo import MongoClient
import psycopg

load_dotenv()


MONGO_URI              = os.getenv("MONGO_URI")
MONGO_DB_NAME           = os.getenv("MONGO_DB_NAME")
MONGO_COLLECTION_NAME   = os.getenv("MONGO_COLLECTION_NAME")

NEON_DATABASE_URL       = os.getenv("NEON_DATABASE_URL")
NEON_TABLE_NAME         = os.getenv("NEON_TABLE_NAME")

# ─── Connect to Mongo ────────────────────────────────────────────────────────
mongo_client = MongoClient(MONGO_URI)
mongo_db     = mongo_client[MONGO_DB_NAME]
collection   = mongo_db[MONGO_COLLECTION_NAME]

# Only pull docs that were successfully scraped and actually have headlines
docs = list(collection.find({
    "scraped": True,
    "headline_count": {"$gt": 0}
}))

print(f"Found {len(docs)} Mongo docs with headlines to migrate")

# ─── Build rows ──────────────────────────────────────────────────────────────
rows = []
for doc in docs:
    date_str = doc.get("date")            # expecting 'YYYY-MM-DD' or similar
    headlines = doc.get("headlines", [])
    wayback_url = doc.get("wayback_url")
    source_timestamp = doc.get("timestamp")

    # Normalize date to a date object regardless of how it's stored in Mongo
    if isinstance(date_str, datetime):
        headline_date = date_str.date()
    else:
        headline_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()

    date_compact = headline_date.strftime("%Y%m%d")

    for i, headline_text in enumerate(headlines):
        headline_text = headline_text.strip()
        if not headline_text:
            continue
        headline_id = f"{date_compact}-{i}"
        rows.append((headline_id, headline_date, headline_text, wayback_url, source_timestamp))

print(f"Built {len(rows)} headline rows to insert")

# ─── Insert into Neon (Postgres) ─────────────────────────────────────────────
insert_sql = f"""
    INSERT INTO {NEON_TABLE_NAME}
        (headline_id, headline_date, headline_text, wayback_url, source_timestamp)
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (headline_id) DO UPDATE SET
        headline_date     = EXCLUDED.headline_date,
        headline_text     = EXCLUDED.headline_text,
        wayback_url       = EXCLUDED.wayback_url,
        source_timestamp  = EXCLUDED.source_timestamp
"""

with psycopg.connect(NEON_DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.executemany(insert_sql, rows)
    conn.commit()

print(f"Done — upserted {len(rows)} rows into {NEON_TABLE_NAME}")

# ─── Sanity check ─────────────────────────────────────────────────────────────
with psycopg.connect(NEON_DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {NEON_TABLE_NAME}")
        total_rows = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(DISTINCT headline_date) FROM {NEON_TABLE_NAME}")
        distinct_days = cur.fetchone()[0]

print(f"Total headline rows: {total_rows}")
print(f"Distinct days: {distinct_days}")
