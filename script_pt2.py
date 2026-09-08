import requests
import time
import logging
from datetime import datetime
from pymongo import MongoClient
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

# ─── Config ─────────────────────────────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION_NAME")

FETCH_DELAY = 2.0    # seconds between snapshot fetches — conservative
MAX_RETRIES = 3
TIMEOUT     = 30

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

client     = MongoClient(MONGO_URI)
db         = client[MONGO_DB_NAME]
collection = db[MONGO_COLLECTION_NAME]
log.info(f"Connected: {MONGO_DB_NAME}.{MONGO_COLLECTION_NAME}")

def extract_headlines(html):
    """See earlier message for full explanation of Strategy A / Strategy B."""
    soup = BeautifulSoup(html, 'lxml')
    for el in soup.find_all(id=['wm-ipp-base', 'wm-ipp-print']):
        el.decompose()

    headlines = []
    seen = set()

    def add(text):
        text = ' '.join(text.split())
        if 10 < len(text) < 300 and text not in seen:
            seen.add(text)
            headlines.append(text)

    for div in soup.find_all('div', class_=lambda c: c and 'field-name-title' in c):
        h1 = div.find('h1')
        if h1:
            add(h1.get_text())

    candidates = soup.find_all(class_=lambda c: c and 'headline' in c.lower())
    for el in candidates:
        if el.find(class_=lambda c: c and 'headline' in c.lower()) is not None:
            continue
        add(el.get_text())

    return headlines

def fetch_snapshot(wayback_url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(wayback_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
            if r.status_code == 200:
                return r.text
            elif r.status_code == 404:
                log.warning(f"  404 — not available: {wayback_url}")
                return None
            elif r.status_code == 429:
                wait = 60 * attempt   # matches documented IA cooldown behavior
                log.warning(f"  Rate limited (429), waiting {wait}s...")
                time.sleep(wait)
            else:
                log.warning(f"  HTTP {r.status_code} on attempt {attempt}")
                time.sleep(5 * attempt)
        except requests.exceptions.Timeout:
            log.warning(f"  Timeout on attempt {attempt}, retrying...")
            time.sleep(5 * attempt)
        except requests.exceptions.RequestException as e:
            log.warning(f"  Request error: {e}, retrying...")
            time.sleep(5 * attempt)
    return None

if __name__ == '__main__':
    unscraped = list(collection.find({'scraped': False}))
    total = len(unscraped)
    log.info(f"Found {total} unscraped snapshots — starting...")

    success, failed, empty = 0, 0, 0

    for i, doc in enumerate(unscraped, 1):
        wayback_url = doc.get('wayback_url')
        doc_id      = doc['_id']
        timestamp   = doc.get('timestamp', 'unknown')

        log.info(f"[{i}/{total}] {timestamp} — {wayback_url}")
        html = fetch_snapshot(wayback_url)

        if html is None:
            collection.update_one(
                {'_id': doc_id},
                {'$set': {'scraped': True, 'scrape_error': 'fetch_failed',
                          'scraped_at': datetime.utcnow(), 'headlines': [], 'headline_count': 0}}
            )
            failed += 1
            time.sleep(FETCH_DELAY)
            continue

        headlines = extract_headlines(html)
        if not headlines:
            log.warning(f"  No headlines found")
            empty += 1

        collection.update_one(
            {'_id': doc_id},
            {'$set': {
                'scraped': True, 'scraped_at': datetime.utcnow(),
                'headlines': headlines, 'headline_count': len(headlines),
                'scrape_error': None
            }}
        )
        log.info(f"  Stored {len(headlines)} headlines")
        success += 1
        time.sleep(FETCH_DELAY)

    log.info("=" * 50)
    log.info(f"DONE — success={success} | failed={failed} | empty={empty}")
