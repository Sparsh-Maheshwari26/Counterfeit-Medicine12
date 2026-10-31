"""Name and product-code screening helpers.

These compare product-level reference listings only. They never establish the
authenticity of a physical pack or manufacturing batch.
"""
from __future__ import annotations

import re
from typing import Any, Iterable


def clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_product_code(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", clean_text(value).casefold())


def record_matches_name(record: dict[str, Any], query: str) -> bool:
    q = clean_text(query).casefold()
    if not q:
        return False
    fields = [record.get("brand_name", ""), record.get("generic_name", ""), record.get("active_ingredients", "")]
    for field in fields:
        val = clean_text(field).casefold()
        if val and (q in val or val in q):  # `val and` fixes empty fields matching everything
            return True
    q_tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2]
    searchable = " ".join(clean_text(f).casefold() for f in fields)
    return bool(q_tokens) and sum(t in searchable for t in q_tokens) / len(q_tokens) >= 0.7


def _record_codes(record: dict[str, Any]) -> set[str]:
    codes = {normalize_product_code(record.get("product_ndc")), normalize_product_code(record.get("package_ndc"))}
    for pkg in record.get("package_ndcs") or []:
        codes.add(normalize_product_code(pkg))
    codes.discard("")
    return codes


def select_official_matches(
    records: Iterable[dict[str, Any]], medicine_name: str, product_code: str = ""
) -> tuple[list[dict[str, Any]], str]:
    """Cases: name_only, name_and_code, code_mismatch, code_name_mismatch, no_match."""
    all_records = list(records)
    name_matches = [r for r in all_records if record_matches_name(r, medicine_name)]
    entered = normalize_product_code(product_code)
    if not entered:
        return name_matches, "name_only" if name_matches else "no_match"

    code_matches = [r for r in all_records if entered in _record_codes(r)]
    if code_matches:
        combined = [r for r in code_matches if record_matches_name(r, medicine_name)]
        if combined:
            return combined, "name_and_code"
        return code_matches, "code_name_mismatch"
    if name_matches:
        return name_matches, "code_mismatch"
    return [], "no_match"
