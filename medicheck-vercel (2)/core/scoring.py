"""Transparent rule-based screening. Not a probability of authenticity.

The point deductions are unchanged from the original app. The result is now organised as:
status -> risk score -> mismatched details.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from .verification import clean_text, select_official_matches

BatchLookup = Callable[[list[str]], list[dict[str, Any]]]


def norm(value: object) -> str:
    """Case-insensitive, whitespace-trimmed form used for comparisons only (never for display)."""
    return clean_text(value).casefold()


def manufacturer_matches(entered: str, records: list[dict[str, Any]]) -> bool:
    e = norm(entered)
    if not e:
        return True
    for rec in records:
        lab = norm(rec.get("labeler_name"))
        if lab and (e in lab or lab in e):
            return True
    return False


def _short(values: list[str], limit: int = 3) -> str:
    seen, out = set(), []
    for v in values:
        v = clean_text(v)
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    extra = len(out) - limit
    return ", ".join(out[:limit]) + (f" (+{extra} more)" if extra > 0 else "") if out else "Not listed"


def _ym(value: str) -> str:
    return clean_text(value)[:7]


def screen(form: dict[str, Any], records: list[dict[str, Any]], today: date | None = None,
           batch_lookup: BatchLookup | None = None) -> dict[str, Any]:
    today = today or date.today()
    # Raw (trimmed) values are kept for display; comparisons use the normalised forms.
    raw = {k: str(form.get(k) or "").strip() for k in
           ("medicine_name", "product_code", "manufacturer", "batch_number", "manufacture_date", "expiry_date")}
    mfg = date.fromisoformat(raw["manufacture_date"]) if raw["manufacture_date"] else None
    exp = date.fromisoformat(raw["expiry_date"]) if raw["expiry_date"] else None

    matches, case = select_official_matches(records, raw["medicine_name"], raw["product_code"])
    mfr_issue = bool(matches and raw["manufacturer"] and not manufacturer_matches(raw["manufacturer"], matches))

    # Batch data is optional: openFDA lists products, not batches. Compare only if batch records exist.
    batch_rows: list[dict[str, Any]] = []
    if matches and batch_lookup and case in {"name_only", "name_and_code"}:
        batch_rows = batch_lookup([m["product_ndc"] for m in matches if m.get("product_ndc")])
    batch_issue = bool(batch_rows and raw["batch_number"] and
                       norm(raw["batch_number"]) not in {norm(b["batch_number"]) for b in batch_rows})
    batch_exp_issue = ""
    if batch_rows and not batch_issue and raw["expiry_date"]:
        row = next(b for b in batch_rows if norm(b["batch_number"]) == norm(raw["batch_number"]))
        if row.get("expiry_date") and _ym(row["expiry_date"]) != _ym(raw["expiry_date"]):
            batch_exp_issue = row["expiry_date"]

    mismatches: list[dict[str, str]] = []
    date_issue = False
    if case == "code_name_mismatch":
        mismatches.append({"field": "Medicine Name", "entered": raw["medicine_name"], "reference":
                           _short([m.get("brand_name") or m.get("generic_name") for m in matches]), "kind": "database"})
    elif case == "code_mismatch":
        mismatches.append({"field": "Product Code", "entered": raw["product_code"], "reference":
                           _short([m.get("product_ndc") for m in matches]), "kind": "database"})
    if mfr_issue:
        mismatches.append({"field": "Manufacturer", "entered": raw["manufacturer"], "reference":
                           _short([m.get("labeler_name") for m in matches]), "kind": "database"})
    if batch_issue:
        mismatches.append({"field": "Batch Number", "entered": raw["batch_number"], "reference":
                           _short([b["batch_number"] for b in batch_rows]), "kind": "database"})
    if batch_exp_issue:
        mismatches.append({"field": "Expiry Date", "entered": raw["expiry_date"], "reference": batch_exp_issue, "kind": "database"})
    if mfg and mfg > today:
        date_issue = True
        mismatches.append({"field": "Manufacturing Date", "entered": raw["manufacture_date"],
                           "reference": f"Not after {today.isoformat()}", "kind": "rule"})
    expiry_rules = []
    if mfg and exp and exp <= mfg:
        expiry_rules.append(f"later than the manufacturing date ({raw['manufacture_date']})")
    if exp and exp < today:
        expiry_rules.append(f"not before {today.isoformat()}")
    if expiry_rules:
        date_issue = True
        text = " and ".join(expiry_rules)
        mismatches.append({"field": "Expiry Date", "entered": raw["expiry_date"],
                           "reference": text[0].upper() + text[1:], "kind": "rule"})

    # Score: original deductions, plus 30 for a batch mismatch (only possible when batch records exist).
    score = 100
    if date_issue:
        score -= 45
    if case in {"code_mismatch", "code_name_mismatch"}:
        score -= 30
    if mfr_issue:
        score -= 20
    if batch_issue or batch_exp_issue:
        score -= 30
    if not matches:
        score -= 35
    score = max(0, min(100, score))

    categories = sum([date_issue, case in {"code_mismatch", "code_name_mismatch"}, mfr_issue,
                      bool(batch_issue or batch_exp_issue)])
    if case == "code_name_mismatch" or categories >= 2:
        verdict, tone = "POTENTIALLY COUNTERFEIT", "red"
        summary = ("Significant discrepancies require further investigation. This is not a final finding: "
                   "contact the manufacturer or a licensed pharmacist.")
    elif categories == 1:
        verdict, tone = "SUSPICIOUS", "amber"
        summary = "One or more details do not match the available reference records."
    elif not matches:
        verdict, tone = "NEEDS CHECKING", "amber"
        summary = "No reference record is available for this medicine. This does not mean it is fake."
    else:
        verdict, tone = "REFERENCE MATCH", "blue"
        summary = "All checked details match the available reference records. This does not prove this batch is genuine."

    unavailable = []
    if matches and not batch_rows:
        unavailable.append("Batch number: no batch records are available in the reference database, so it was not verified.")
    if matches and raw["manufacture_date"] and not batch_rows:
        unavailable.append("Printed dates were checked for consistency only.")

    return {
        "verdict": verdict, "tone": tone, "summary": summary,
        "risk_score": 100 - score, "score": score,
        "mismatches": mismatches, "unavailable": unavailable, "match_case": case,
        "similar": [{k: m.get(k, "") for k in ("brand_name", "generic_name", "labeler_name", "product_ndc")}
                    for m in matches[:3]] if matches else [],
    }
