import requests
import time
import logging
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
import os
from dotenv import load_dotenv
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME")

TARGET_URL  = 'https://www.iranintl.com/en'
FROM_DATE   = '20190301'
TO_DATE     = datetime.utcnow().strftime('%Y%m%d')  # always up to today

CDX_URL     = 'https://web.archive.org/cdx/search/cdx'
CDX_TIMEOUT = 60
CDX_DELAY   = 1.0    # ~1 request/sec — the documented safe ceiling for CDX
MAX_RETRIES = 3

# ─── MongoDB setup ───────────────────────────────────────────────────────────
client     = MongoClient(MONGO_URI)
db         = client[MONGO_DB_NAME]
collection = db[MONGO_COLLECTION_NAME]
collection.create_index('timestamp', unique=True)
collection.create_index('date')
log.info(f"Connected to MongoDB: {MONGO_DB_NAME}.{MONGO_COLLECTION_NAME}")

def query_cdx_for_year(year):
    """Query CDX for one calendar year, one snapshot per day (earliest by default order)."""
    params = {
        'url':      TARGET_URL,
        'output':   'json',
        'fl':       'timestamp,original,statuscode',
        'filter':   'statuscode:200',
        'from':     f"{year}0101000000",
        'to':       f"{year}1231235959",
        'collapse': 'timestamp:8',   # one snapshot per calendar day, earliest first
        'limit':    500,             # a year has <=366 days, so this never truncates
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.info(f"  CDX query year={year} attempt={attempt}")
            r = requests.get(CDX_URL, params=params, timeout=CDX_TIMEOUT)
            if r.status_code == 429:
                wait = 60 * attempt
                log.warning(f"  Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            if len(data) <= 1:
                return []
            headers, rows = data[0], data[1:]
            return [dict(zip(headers, row)) for row in rows if len(row) == len(headers)]
        except requests.exceptions.RequestException as e:
            log.warning(f"  Request error: {e}, retrying...")
            time.sleep(5 * attempt)

    log.error(f"  Failed after {MAX_RETRIES} retries for year={year}")
    return []

def store_snapshots(snapshots):
    stored, skipped = 0, 0
    for snap in snapshots:
        ts = snap.get('timestamp', '')
        if not ts or len(ts) < 8:
            continue
        original = snap.get('original', TARGET_URL)
        doc = {
            'timestamp':    ts,
            'date':         datetime.strptime(ts[:8], '%Y%m%d'),
            'original_url': original,
            'wayback_url':  f"https://web.archive.org/web/{ts}id_/{original}",
            'scraped':      False,
            'language':     'en',
        }
        try:
            collection.insert_one(doc)
            stored += 1
        except DuplicateKeyError:
            skipped += 1
    return stored, skipped

if __name__ == '__main__':
    start_year = int(FROM_DATE[:4])
    end_year   = int(TO_DATE[:4])
    total_stored, total_skipped = 0, 0

    for year in range(start_year, end_year + 1):
        log.info(f"Querying year {year}...")
        snapshots = query_cdx_for_year(year)
        if snapshots:
            stored, skipped = store_snapshots(snapshots)
            total_stored += stored
            total_skipped += skipped
            log.info(f"Year {year}: stored={stored} skipped={skipped}")
        else:
            log.info(f"Year {year}: no snapshots found")
        time.sleep(CDX_DELAY)

    log.info("=" * 50)
    log.info(f"DONE — Total stored: {total_stored} | Total skipped: {total_skipped}")
    log.info(f"Total in DB: {collection.count_documents({})}")
