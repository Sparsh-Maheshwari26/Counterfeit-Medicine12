# Counterfeit Medicine System (Vercel version)

Static frontend (`public/`) + one Flask app (`app.py`, logic in `core/`). Reference data is queried live from the
[openFDA NDC Directory](https://open.fda.gov/apis/drug/ndc/), so there is no database to host.

## Deploy
1. Push this folder to GitHub (or run `npm i -g vercel && vercel` in this folder).
2. In Vercel: **Add New → Project →** import the repo. Framework preset: **Other**. No build command.
   Vercel detects `app.py` (Flask) automatically and serves `public/`.
3. Deploy. That's it.

## Connect the database (required for permanent storage)
1. Vercel dashboard -> your project -> **Storage** -> **Create Database** -> **Neon (Postgres)** -> free plan -> connect to this project.
   Vercel adds `DATABASE_URL` automatically.
2. **Redeploy** (Deployments -> ... -> Redeploy) so the new variable is picked up.
3. Open the app. Tables are created and the 500 official listings in `data/openfda_listings.csv` are loaded automatically on the first request (no internet needed). **Medicine database -> Sync from openFDA** adds more.
Without `DATABASE_URL` the app falls back to a temporary SQLite file and shows a warning on the Database page.

Tables: `medicine_reference` (product listings) and `check_log` (medicine name, verdict, score, time; never the batch number).

## Run locally
`pip install -r requirements.txt && flask --app app run`, or run the tests: `python tests/test_screening.py`

## Changes from the Streamlit version
- Storage moved from a local SQLite file to Postgres (Vercel's filesystem is ephemeral). Names not in the database are fetched live from openFDA and cached.
- Fixed: manufacturer check now covers all matches, "Name match" no longer says Yes on a code/name mismatch,
  empty fields no longer match every query, and any package NDC (not just the first) is matched.
- Fictional demo rows were dropped; the "Load sample presentation case" button still works.

## Limits
The Postgres path was written to work with Neon but only tested locally on SQLite. Unauthenticated openFDA allows ~240 requests/min per IP. Add a free API key as an env var
(`api_key=` in `core/openfda.py`) if you expect heavy use. This remains a screening prototype, not batch authentication.

## Batch data (optional)
openFDA lists products, not batches. The `batch_reference` table is therefore empty by default, and the app says
"batch not verified". When you load batch records, the Batch Number is compared (case-insensitive, trimmed) and
batch autocomplete starts suggesting values. Example:

```sql
INSERT INTO batch_reference (id, product_ndc, batch_number, manufacture_date, expiry_date, source, created_at)
VALUES ('b1', '0363-0123', 'AB12346', '2025-05-01', '2027-05-01', 'manufacturer feed', '2026-11-01');
```

## Result statuses
REFERENCE MATCH · SUSPICIOUS (one kind of mismatch) · POTENTIALLY COUNTERFEIT (two or more kinds, or a code that
belongs to a different medicine) · NEEDS CHECKING (no reference record; this is never treated as counterfeit).
Risk Score = 100 minus the original screening score; the deductions are unchanged.
