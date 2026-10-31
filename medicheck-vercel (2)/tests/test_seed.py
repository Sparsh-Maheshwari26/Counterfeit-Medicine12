import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["MEDICHECK_SQLITE"] = os.path.join(tempfile.mkdtemp(), "seed.db")
import requests  # noqa: E402

def _offline(*a, **k):
    raise requests.ConnectionError("offline")
requests.get = _offline  # openFDA unreachable for this whole module

import app as flask_app  # noqa: E402
from core import db  # noqa: E402


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.c = flask_app.app.test_client()

    def test_seeded_from_bundled_csv_without_network(self):
        s = self.c.get("/api/stats").json
        self.assertEqual(s["records"], 500)
        self.assertFalse(any(r["brand_name"].startswith("DEMO") for r in db.search_records("demo", 50)))

    def test_real_record_matches_case_insensitively(self):
        body = {"medicine_name": "  ACETAMINOPHEN ", "batch_number": "x1", "manufacturer": "cardinal health 107, llc",
                "product_code": "55154-7897", "manufacture_date": "2026-10-01", "expiry_date": "2027-10-01"}
        out = self.c.post("/api/check", json=body).json
        self.assertEqual((out["verdict"], out["mismatches"]), ("REFERENCE MATCH", []))
        wrong = self.c.post("/api/check", json={**body, "product_code": "55154-0000"}).json
        self.assertEqual(wrong["verdict"], "SUSPICIOUS")
        self.assertEqual(wrong["mismatches"][0]["field"], "Product Code")

    def test_autocomplete_and_sync_cooldown_unaffected_by_bundle(self):
        self.assertIn("Acetaminophen", self.c.get("/api/suggest?field=name&q=acetam").json["suggestions"])
        r = self.c.post("/api/sync")  # offline -> clear error, not "synced a moment ago"
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
