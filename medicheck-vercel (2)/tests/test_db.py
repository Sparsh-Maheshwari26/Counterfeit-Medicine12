import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["MEDICHECK_SQLITE"] = os.path.join(tempfile.mkdtemp(), "t.db")
from core import db  # noqa: E402
from core.scoring import screen  # noqa: E402


def rec(i, brand="Febrinil", generic="Paracetamol", labeler="Lab"):
    return {"brand_name": brand, "generic_name": generic, "active_ingredients": f"{generic.upper()} 500 mg",
            "labeler_name": labeler, "product_ndc": f"12345-{i:04d}", "package_ndcs": [f"12345-{i:04d}-01", f"12345-{i:04d}-02"],
            "dosage_form": "TABLET", "route": "ORAL"}


class DbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.upsert_records([rec(i, labeler=f"Lab {i}") for i in range(1, 61)] + [rec(99, "Odd_100%", "Ibuprofen")])

    def test_counts_and_upsert_is_idempotent(self):
        before = db.stats()["records"]
        db.upsert_records([rec(1, labeler="Renamed")])
        self.assertEqual(db.stats()["records"], before)
        self.assertEqual(db.search_records("Renamed")[0]["labeler_name"], "Renamed")

    def test_search_and_pagination(self):
        self.assertEqual(len(db.search_records("paracetamol", 25, 0)), 25)
        self.assertEqual(len(db.search_records("paracetamol", 25, 50)), 10)

    def test_wildcards_are_literal(self):
        self.assertEqual([r["brand_name"] for r in db.search_records("100%")], ["Odd_100%"])
        self.assertEqual(len(db.search_records("%")), 1)  # only the row with a literal %

    def test_code_lookup_any_package(self):
        got = db.find_candidates("zzzz", "12345-0007-02")
        self.assertTrue(any(r["product_ndc"] == "12345-0007" for r in got))

    def test_screen_uses_db_candidates(self):
        form = {"medicine_name": "Febrinil Paracetamol", "batch_number": "b", "manufacturer": "Lab 58",
                "manufacture_date": "2026-10-01", "expiry_date": "2027-10-01"}
        out = screen(form, db.find_candidates(form["medicine_name"]))
        self.assertEqual(out["verdict"], "REFERENCE MATCH")

    def test_name_suggestions(self):
        self.assertEqual(db.suggest_names("f"), [])  # needs 2+ characters
        got = db.suggest_names("  FEBR ")
        self.assertEqual(got[0], "Febrinil")
        self.assertEqual(len(got), len(set(g.casefold() for g in got)))
        self.assertTrue(db.suggest_names("paracet"))  # generic names too
        self.assertEqual(db.suggest_names("qqqqzz"), [])

    def test_batch_suggestions_filtered_by_medicine(self):
        db.init()
        with db.conn() as c:
            db._many(c, "INSERT INTO batch_reference (id, product_ndc, batch_number, expiry_date, source, created_at) VALUES (?,?,?,?,?,?)",
                     [("a", "12345-0002", "AB12346", "2027-10-01", "t", ""), ("b", "12345-0002", "AB99999", "", "t", ""),
                      ("c", "12345-0099", "AB55555", "", "t", "")])
        self.assertEqual(db.suggest_batches("ab1", "febrinil"), ["AB12346"])
        self.assertEqual(sorted(db.suggest_batches("ab", "FEBRINIL")), ["AB12346", "AB99999"])
        self.assertIn("AB55555", db.suggest_batches("ab55"))  # no medicine -> unfiltered
        self.assertEqual(db.suggest_batches("ab55", "febrinil"), [])
        self.assertEqual(len(db.find_batches(["12345-0002"])), 2)

    def test_check_log_has_no_batch(self):
        db.log_check("Febrinil", "SUSPICIOUS", 50)
        row = db.recent_checks(1)[0]
        self.assertEqual(set(row), {"medicine_name", "verdict", "score", "created_at"})


if __name__ == "__main__":
    unittest.main()
