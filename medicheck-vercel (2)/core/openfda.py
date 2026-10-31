"""Live lookups against the openFDA NDC Directory (labeler-submitted listings)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

API_URL = "https://api.fda.gov/drug/ndc.json"
SOURCE_URL = "https://open.fda.gov/apis/drug/ndc/"
HEADERS = {"User-Agent": "CounterfeitMedicineSystemStudentProject/2.0 (educational)", "Accept": "application/json"}


class SourceError(RuntimeError):
    pass


def _q(term: str) -> str:
    return quote('"' + term.replace('"', " ").strip() + '"', safe="")


def flatten(rec: dict[str, Any]) -> dict[str, Any]:
    ingredients = rec.get("active_ingredients") or []
    ing_text = "; ".join(
        " ".join(str(x) for x in (i.get("name", ""), i.get("strength", "")) if x) if isinstance(i, dict) else str(i)
        for i in ingredients
    )
    pkgs = [p.get("package_ndc", "") for p in rec.get("packaging") or [] if isinstance(p, dict)]
    route = rec.get("route")
    return {
        "brand_name": str(rec.get("brand_name") or ""),
        "generic_name": str(rec.get("generic_name") or ""),
        "active_ingredients": ing_text,
        "labeler_name": str(rec.get("labeler_name") or ""),
        "product_ndc": str(rec.get("product_ndc") or ""),
        "package_ndc": pkgs[0] if pkgs else "",
        "package_ndcs": [p for p in pkgs if p],
        "dosage_form": str(rec.get("dosage_form") or ""),
        "route": ", ".join(route) if isinstance(route, list) else str(route or ""),
    }


def _fetch(search: str, limit: int, skip: int = 0) -> list[dict[str, Any]]:
    url = f"{API_URL}?search={search}&limit={limit}&skip={skip}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=8)
    except requests.RequestException as exc:
        raise SourceError("Could not reach openFDA. Try again shortly.") from exc
    if resp.status_code == 404:  # openFDA uses 404 for "no results"
        return []
    if not resp.ok:
        raise SourceError(f"openFDA returned HTTP {resp.status_code}.")
    try:
        return [flatten(r) for r in resp.json().get("results", [])]
    except ValueError as exc:
        raise SourceError("openFDA returned an unreadable response.") from exc


def search_name(name: str, limit: int = 100) -> list[dict[str, Any]]:
    t = _q(name)
    return _fetch(f"(brand_name:{t}+generic_name:{t}+active_ingredients.name:{t})+AND+finished:true", limit)


def search_code(code: str) -> list[dict[str, Any]]:
    t = _q(code.strip())
    return _fetch(f"(product_ndc:{t}+packaging.package_ndc:{t})", 25)


def search_any(text: str, limit: int = 50, skip: int = 0) -> list[dict[str, Any]]:
    t = _q(text)
    return _fetch(
        f"(brand_name:{t}+generic_name:{t}+active_ingredients.name:{t}+labeler_name:{t}+product_ndc:{t})+AND+finished:true",
        limit, skip,
    )
