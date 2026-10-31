"""Seed the database with listings for commonly sold medicines from openFDA."""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime, timedelta, timezone

from . import db, openfda

SEED_TERMS = ["paracetamol", "acetaminophen", "ibuprofen", "amoxicillin", "metformin", "azithromycin",
              "cetirizine", "omeprazole", "atorvastatin", "aspirin", "ciprofloxacin", "diclofenac",
              "pantoprazole", "amlodipine", "losartan"]
PER_TERM = 40
COOLDOWN = timedelta(minutes=5)
BUNDLED = Path(__file__).resolve().parent.parent / "data" / "openfda_listings.csv"
_seeded = False


def load_bundled() -> int:
    """Load the 500 official openFDA listings shipped with the project (no network needed)."""
    with BUNDLED.open(newline="", encoding="utf-8") as h:
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(h)]
    return db.upsert_records(rows, source="bundled")


def ensure_seeded() -> None:
    """First request on an empty database: load the bundled listings once."""
    global _seeded
    if _seeded:
        return
    if db.stats()["records"] == 0 and BUNDLED.exists():
        load_bundled()
    _seeded = True



def run(force: bool = False) -> dict:
    last = db.last_live_sync()
    if last and not force:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if age < COOLDOWN:
            return {"skipped": True, "message": "Synced a moment ago. Try again in a few minutes."}

    def one(term):
        try:
            return openfda.search_name(term, limit=PER_TERM)
        except openfda.SourceError:
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(one, SEED_TERMS))
    ok = [r for r in results if r is not None]
    if not ok:
        raise openfda.SourceError("Could not reach openFDA. No changes were made.")
    saved = db.upsert_records([rec for r in ok for rec in r], source="seed")
    return {"skipped": False, "saved": saved, "failed_terms": len(results) - len(ok)}
