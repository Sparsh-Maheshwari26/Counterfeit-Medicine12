"""Database layer: Postgres on Vercel (DATABASE_URL), SQLite file for local dev.

Tables
  medicine_reference - product listings from the openFDA NDC Directory (seeded + cached)
  check_log          - one row per screening (name, verdict, score). No batch numbers stored.
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

COLS = ["record_key", "brand_name", "generic_name", "active_ingredients", "labeler_name",
        "product_ndc", "package_ndcs", "dosage_form", "route", "source", "synced_at"]

_ready = False


def _url() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""


def backend() -> str:
    return "postgres" if _url() else "sqlite"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def conn():
    if backend() == "postgres":
        import psycopg
        from psycopg.rows import dict_row
        c = psycopg.connect(_url(), row_factory=dict_row, connect_timeout=8)
    else:
        c = sqlite3.connect(os.environ.get("MEDICHECK_SQLITE", "/tmp/medicheck.db"), timeout=10)
        c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def _sql(sql: str) -> str:
    return sql.replace("?", "%s") if backend() == "postgres" else sql


def _all(c, sql: str, params: Iterable = ()) -> list[dict[str, Any]]:
    cur = c.execute(_sql(sql), tuple(params))
    return [dict(r) for r in cur.fetchall()]


def _run(c, sql: str, params: Iterable = ()) -> None:
    c.execute(_sql(sql), tuple(params))


def _many(c, sql: str, rows: list[tuple]) -> None:
    if backend() == "postgres":
        c.cursor().executemany(_sql(sql), rows)
    else:
        c.executemany(sql, rows)


def init() -> None:
    global _ready
    if _ready:
        return
    with conn() as c:
        _run(c, """CREATE TABLE IF NOT EXISTS medicine_reference (
            record_key TEXT PRIMARY KEY,
            brand_name TEXT NOT NULL DEFAULT '',
            generic_name TEXT NOT NULL DEFAULT '',
            active_ingredients TEXT NOT NULL DEFAULT '',
            labeler_name TEXT NOT NULL DEFAULT '',
            product_ndc TEXT NOT NULL DEFAULT '',
            package_ndcs TEXT NOT NULL DEFAULT '',
            dosage_form TEXT NOT NULL DEFAULT '',
            route TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'seed',
            synced_at TEXT NOT NULL DEFAULT ''
        )""")
        _run(c, """CREATE TABLE IF NOT EXISTS check_log (
            id TEXT PRIMARY KEY,
            medicine_name TEXT NOT NULL,
            verdict TEXT NOT NULL,
            score INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""")
        # Optional batch-level reference data (openFDA has none). Empty until a batch source is loaded.
        _run(c, """CREATE TABLE IF NOT EXISTS batch_reference (
            id TEXT PRIMARY KEY,
            product_ndc TEXT NOT NULL,
            batch_number TEXT NOT NULL,
            manufacture_date TEXT NOT NULL DEFAULT '',
            expiry_date TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT ''
        )""")
        _run(c, "CREATE INDEX IF NOT EXISTS idx_batch_ndc ON batch_reference(product_ndc)")
        _run(c, "CREATE INDEX IF NOT EXISTS idx_med_brand ON medicine_reference(brand_name)")
        _run(c, "CREATE INDEX IF NOT EXISTS idx_med_generic ON medicine_reference(generic_name)")
        _run(c, "CREATE INDEX IF NOT EXISTS idx_log_created ON check_log(created_at)")
    _ready = True


def _like(term: str) -> str:
    esc = term.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
    pkgs = [p for p in (row.get("package_ndcs") or "").split("|") if p]
    out = dict(row)
    out["package_ndcs"] = pkgs
    out["package_ndc"] = pkgs[0] if pkgs else ""
    return out


def upsert_records(records: list[dict[str, Any]], source: str = "seed") -> int:
    init()
    stamp, seen, rows = now(), set(), []
    for r in records:
        key = r.get("product_ndc") or "|".join((r.get("brand_name", ""), r.get("generic_name", ""), r.get("labeler_name", "")))
        if not key.strip("|") or key in seen:
            continue
        seen.add(key)
        rows.append((key, r.get("brand_name", ""), r.get("generic_name", ""), r.get("active_ingredients", ""),
                     r.get("labeler_name", ""), r.get("product_ndc", ""), "|".join(r.get("package_ndcs") or []),
                     r.get("dosage_form", ""), r.get("route", ""), source, stamp))
    if not rows:
        return 0
    sets = ", ".join(f"{c}=excluded.{c}" for c in COLS[1:])
    with conn() as c:
        _many(c, f"INSERT INTO medicine_reference ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))}) "
                 f"ON CONFLICT(record_key) DO UPDATE SET {sets}", rows)
    return len(rows)


def find_candidates(name: str, code: str = "", limit: int = 400) -> list[dict[str, Any]]:
    """Records that might match the entered name (any word) or code. Final matching is done in verification.py."""
    init()
    tokens = [t for t in re.findall(r"[a-z0-9]+", name.casefold()) if len(t) > 2] or [name.casefold().strip()]
    clauses, params = [], []
    for t in tokens[:6]:
        for col in ("brand_name", "generic_name", "active_ingredients"):
            clauses.append(f"LOWER({col}) LIKE ? ESCAPE '\\'")
            params.append(_like(t))
    norm = re.sub(r"[^a-z0-9]", "", code.casefold())
    if norm:
        clauses.append("REPLACE(LOWER(product_ndc), '-', '') = ?")
        params.append(norm)
        clauses.append("REPLACE(LOWER(package_ndcs), '-', '') LIKE ? ESCAPE '\\'")
        params.append(_like(norm))
    with conn() as c:
        rows = _all(c, f"SELECT * FROM medicine_reference WHERE {' OR '.join(clauses)} LIMIT {int(limit)}", params)
    return [_row_to_record(r) for r in rows]


def search_records(q: str, limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
    init()
    like = _like(q)
    cols = ("brand_name", "generic_name", "active_ingredients", "labeler_name", "product_ndc", "package_ndcs")
    where = " OR ".join(f"LOWER({c}) LIKE ? ESCAPE '\\'" for c in cols)
    with conn() as c:
        rows = _all(c, f"SELECT * FROM medicine_reference WHERE {where} "
                       f"ORDER BY brand_name, generic_name, record_key LIMIT ? OFFSET ?",
                    [like] * len(cols) + [int(limit), int(offset)])
    return [_row_to_record(r) for r in rows]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def suggest_names(q: str, limit: int = 8) -> list[str]:
    """Distinct brand/generic names containing the typed text. Prefix matches rank first."""
    init()
    n = _norm(q)
    if len(n) < 2:
        return []
    with conn() as c:
        rows = []
        for col in ("brand_name", "generic_name"):
            rows += _all(c, f"SELECT DISTINCT {col} AS v FROM medicine_reference "
                            f"WHERE {col} <> '' AND LOWER({col}) LIKE ? ESCAPE '\\' LIMIT 40", [_like(n)])
    seen, names = set(), []
    for r in rows:
        k = _norm(r["v"])
        if k and k not in seen:
            seen.add(k)
            names.append(r["v"].strip())
    names.sort(key=lambda v: (not _norm(v).startswith(n), len(v), v.casefold()))
    return names[:limit]


def suggest_batches(q: str, medicine: str = "", limit: int = 8) -> list[str]:
    """Batch numbers from batch_reference, narrowed to the chosen medicine when it is known."""
    init()
    n = _norm(q)
    if len(n) < 2:
        return []
    sql = "SELECT DISTINCT b.batch_number AS v FROM batch_reference b "
    params: list[Any] = []
    med = _norm(medicine)
    if med:
        sql += ("JOIN medicine_reference m ON m.product_ndc = b.product_ndc "
                "WHERE (LOWER(m.brand_name) = ? OR LOWER(m.generic_name) = ?) AND ")
        params += [med, med]
    else:
        sql += "WHERE "
    sql += "LOWER(b.batch_number) LIKE ? ESCAPE '\\' LIMIT 40"
    params.append(_like(n))
    with conn() as c:
        rows = _all(c, sql, params)
    out = sorted({r["v"].strip() for r in rows}, key=lambda v: (not _norm(v).startswith(n), len(v), v.casefold()))
    return out[:limit]


def find_batches(product_ndcs: list[str]) -> list[dict[str, Any]]:
    init()
    ndcs = [n for n in dict.fromkeys(product_ndcs) if n][:200]
    if not ndcs:
        return []
    with conn() as c:
        return _all(c, f"SELECT product_ndc, batch_number, manufacture_date, expiry_date FROM batch_reference "
                       f"WHERE product_ndc IN ({','.join('?' * len(ndcs))})", ndcs)


def stats() -> dict[str, Any]:
    init()
    with conn() as c:
        n = _all(c, "SELECT COUNT(*) AS n FROM medicine_reference")[0]["n"]
        seeded = _all(c, "SELECT MAX(synced_at) AS t FROM medicine_reference WHERE source IN ('seed', 'bundled')")[0]["t"]
        checks = _all(c, "SELECT COUNT(*) AS n FROM check_log")[0]["n"]
    return {"records": int(n), "last_sync": seeded or "", "checks": int(checks), "backend": backend()}


def last_live_sync() -> str:
    init()
    with conn() as c:
        return _all(c, "SELECT MAX(synced_at) AS t FROM medicine_reference WHERE source = 'seed'")[0]["t"] or ""


def log_check(name: str, verdict: str, score: int) -> None:
    init()
    with conn() as c:
        _run(c, "INSERT INTO check_log (id, medicine_name, verdict, score, created_at) VALUES (?,?,?,?,?)",
             (uuid.uuid4().hex, name[:120], verdict, int(score), now()))


def recent_checks(limit: int = 10) -> list[dict[str, Any]]:
    init()
    with conn() as c:
        return _all(c, "SELECT medicine_name, verdict, score, created_at FROM check_log ORDER BY created_at DESC LIMIT ?", [limit])
