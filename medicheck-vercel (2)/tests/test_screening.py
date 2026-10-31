import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.scoring import screen  # noqa: E402
from core.verification import normalize_product_code, record_matches_name, select_official_matches  # noqa: E402

TODAY = date(2026, 11, 1)


def rec(i, brand="SampleCare Paracetamol", generic="Paracetamol", labeler="SampleCare Labs", pkgs=None):
    return {"brand_name": brand, "generic_name": generic, "active_ingredients": f"{generic} 500 mg",
            "labeler_name": labeler, "product_ndc": f"12345-{i:04d}",
            "package_ndc": f"12345-{i:04d}-01", "package_ndcs": pkgs or [f"12345-{i:04d}-01"]}


FORM = {"medicine_name": "Paracetamol", "batch_number": "B1", "manufacturer": "", "product_code": "",
        "manufacture_date": "2026-10-01", "expiry_date": "2027-10-01"}


class Tests(unittest.TestCase):
    def test_code_normalising(self):
        self.assertEqual(normalize_product_code("12345-6789"), "123456789")

    def test_empty_field_does_not_match_everything(self):
        self.assertFalse(record_matches_name({"brand_name": "", "generic_name": "", "active_ingredients": ""}, "ibuprofen"))

    def test_code_mismatch_and_name_mismatch(self):
        r = [rec(1), rec(2, "Other", "Ibuprofen", "Other Labs")]
        self.assertEqual(select_official_matches(r, "Paracetamol", "99999-0000")[1], "code_mismatch")
        self.assertEqual(select_official_matches(r, "Ibuprofen", "12345-0001")[1], "code_name_mismatch")
        self.assertEqual(select_official_matches(r, "Paracetamol", "12345-0001")[1], "name_and_code")

    def test_any_package_ndc_matches(self):
        r = [rec(1, pkgs=["12345-0001-01", "12345-0001-02"])]
        self.assertEqual(select_official_matches(r, "Paracetamol", "12345-0001-02")[1], "name_and_code")

    def test_manufacturer_checked_beyond_first_five(self):
        records = [rec(i, labeler=f"Other {i}") for i in range(10)] + [rec(50, labeler="Real Pharma")]
        out = screen({**FORM, "manufacturer": "Real Pharma"}, records, TODAY)
        self.assertEqual(out["mismatches"], [])
        self.assertEqual(out["verdict"], "REFERENCE MATCH")

    def test_code_name_mismatch_is_potentially_counterfeit(self):
        out = screen({**FORM, "medicine_name": "Ibuprofen", "product_code": "12345-0001"}, [rec(1)], TODAY)
        self.assertEqual(out["verdict"], "POTENTIALLY COUNTERFEIT")
        self.assertEqual(out["mismatches"][0]["field"], "Medicine Name")

    def test_three_statuses_and_unavailable(self):
        one = screen({**FORM, "expiry_date": "2026-01-01"}, [rec(1)], TODAY)
        self.assertEqual((one["verdict"], one["score"], one["risk_score"]), ("SUSPICIOUS", 55, 45))
        self.assertEqual([m["field"] for m in one["mismatches"]], ["Expiry Date"])
        self.assertEqual(one["mismatches"][0]["kind"], "rule")
        two = screen({**FORM, "expiry_date": "2026-01-01", "manufacturer": "Nobody"}, [rec(1)], TODAY)
        self.assertEqual(two["verdict"], "POTENTIALLY COUNTERFEIT")
        ok = screen(FORM, [rec(1)], TODAY)
        self.assertEqual((ok["verdict"], ok["risk_score"], ok["mismatches"]), ("REFERENCE MATCH", 0, []))
        none = screen(FORM, [], TODAY)  # unavailable is NOT counterfeit
        self.assertEqual((none["verdict"], none["score"]), ("NEEDS CHECKING", 65))
        self.assertEqual(none["mismatches"], [])

    def test_case_and_spacing_do_not_matter_but_display_is_preserved(self):
        recs = [rec(1, labeler="SampleCare Labs")]
        a = screen({**FORM, "medicine_name": "  PARACETAMOL ", "manufacturer": "  samplecare   labs"}, recs, TODAY)
        self.assertEqual((a["verdict"], a["mismatches"]), ("REFERENCE MATCH", []))
        b = screen({**FORM, "manufacturer": "  Wrong Co  "}, recs, TODAY)
        self.assertEqual(b["mismatches"][0]["entered"], "Wrong Co")  # trimmed, original casing kept

    def test_batch_comparison(self):
        rows = [{"product_ndc": "12345-0001", "batch_number": "AB12346", "manufacture_date": "", "expiry_date": "2027-10-01"}]
        look = lambda ndcs: rows if "12345-0001" in ndcs else []
        same = screen({**FORM, "batch_number": " ab12346 "}, [rec(1)], TODAY, look)
        self.assertEqual((same["verdict"], same["mismatches"]), ("REFERENCE MATCH", []))
        diff = screen({**FORM, "batch_number": "AB12345"}, [rec(1)], TODAY, look)
        self.assertEqual(diff["verdict"], "SUSPICIOUS")
        self.assertEqual(diff["mismatches"][0], {"field": "Batch Number", "entered": "AB12345", "reference": "AB12346", "kind": "database"})
        exp = screen({**FORM, "batch_number": "AB12346", "expiry_date": "2027-05-01"}, [rec(1)], TODAY, look)
        self.assertEqual((exp["mismatches"][0]["field"], exp["mismatches"][0]["reference"]), ("Expiry Date", "2027-10-01"))


if __name__ == "__main__":
    unittest.main()
