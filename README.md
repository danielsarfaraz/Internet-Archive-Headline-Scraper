Internet Archive Headline Scraper

A data pipeline that reconstructs a historical archive of news headlines from a diaspora news network, using the Wayback Machine as the source. The pipeline pulls archived snapshots going back to March 2019, extracts headlines from the raw HTML, and normalizes everything into a queryable Postgres table with over 40,000 rows.


How it works

The pipeline runs in three stages, each handled by its own script.

1. fetch_wayback_snapshots.py

Queries the Wayback Machine's CDX API year by year from 2019 to the present day. For each year it asks for one snapshot per calendar day (using collapse=timestamp:8) so the dataset doesn't get flooded with near duplicate captures from the same day. Each snapshot's timestamp and original URL get written into a MongoDB collection as a placeholder document with scraped: False.

Key details:

Requests are throttled to about 1 per second to stay within the Wayback Machine's documented rate limits.
429 responses trigger an exponential backoff before retrying.
MongoDB has a unique index on timestamp so reruns don't create duplicate snapshot records.
2. extract_headlines.py

Goes through every snapshot in Mongo marked scraped: False, fetches the archived HTML for that day, and pulls the headline text out of it.

The hard part here was that the site's markup changed completely across three different eras:

2019 to 2021, a Drupal 7 site
2022 to 2024, an AMP and Next.js hybrid
2025 to present, a rebuilt Next.js site

Rather than write separate parsers for each era, the extractor uses two general strategies:

Strategy A looks for the Drupal era's field-name-title container and grabs the h1 inside it.
Strategy B finds the innermost element whose class name contains the substring "headline," skipping any element that has a matching child, so it doesn't grab a whole section as one blob.

Together these two strategies cover all three site eras without needing year specific logic. Each processed snapshot gets updated in Mongo with its extracted headlines, a headline count, and a scrape status, so partial runs can pick back up without redoing work.

3. migrate_to_postgres.py

Pulls every Mongo document that was successfully scraped and has at least one headline, and writes each individual headline out as its own row in a Postgres table hosted on Neon.

Each row gets a deterministic ID in the format YYYYMMDD-N, where N is the headline's position within that day. This makes the migration idempotent: rerunning it just upserts the same rows instead of creating duplicates, using ON CONFLICT (headline_id) DO UPDATE.

Project structure
.
├── fetch_wayback_snapshots.py   # Stage 1: populate Mongo with CDX snapshot metadata
├── extract_headlines.py         # Stage 2: scrape and parse headlines from archived HTML
├── migrate_to_postgres.py       # Stage 3: normalize and upsert headlines into Postgres
├── cleandb.ipynb                # Notebook used for ad hoc data cleaning and exploration
├── requirements.txt
├── .env.example
└── .gitignore


Tech stack
Python for all pipeline logic
MongoDB as the raw, semi structured staging store
PostgreSQL (Neon) as the final normalized store
BeautifulSoup for HTML parsing across multiple site structures
psycopg for the Postgres connection
python-dotenv for environment configuration
