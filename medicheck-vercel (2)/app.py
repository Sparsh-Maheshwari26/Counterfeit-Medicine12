"""Flask app for Vercel. Static files come from public/. Data lives in Postgres (see core/db.py)."""
import logging
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from core import db, openfda, sync
from core.scoring import screen
from core.verification import record_matches_name

app = Flask(__name__, static_folder=None)
PUBLIC = Path(__file__).parent / "public"
PAGE = 25


@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


@app.before_request
def seed_on_first_use():
    if request.path.startswith("/api/"):
        sync.ensure_seeded()


@app.errorhandler(Exception)
def on_error(exc):
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    logging.exception("Unhandled error")
    return jsonify(error=f"Server error ({type(exc).__name__}). Check the database connection."), 500


def _live_fill(name: str, code: str) -> None:
    """Cache live openFDA results in the database. Failures are ignored if we already have local data."""
    try:
        found = openfda.search_name(name)
        if code:
            found += openfda.search_code(code)
        db.upsert_records(found, source="cache")
    except openfda.SourceError:
        pass


@app.post("/api/check")
def check():
    form = request.get_json(silent=True)
    if not isinstance(form, dict):
        return jsonify(error="Invalid request."), 400
    name = str(form.get("medicine_name") or "").strip()
    code = str(form.get("product_code") or "").strip()
    if not name or not str(form.get("batch_number") or "").strip():
        return jsonify(error="Enter both the medicine name and the batch / lot number."), 400
    if len(name) > 120 or len(code) > 40:
        return jsonify(error="Medicine name or code is too long."), 400

    records = db.find_candidates(name, code)
    # Nothing local matches the name -> ask openFDA live, cache the answer, look again.
    if not any(record_matches_name(r, name) for r in records):
        _live_fill(name, code)
        records = db.find_candidates(name, code)
    try:
        result = screen(form, records, date.today(), batch_lookup=db.find_batches)
    except (ValueError, KeyError):
        return jsonify(error="Check the dates and try again."), 400
    db.log_check(name, result["verdict"], result["risk_score"])
    resp = jsonify(result)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    try:
        page = max(0, min(int(request.args.get("page", 0)), 100))
    except ValueError:
        page = 0
    if not q or len(q) > 80:
        return jsonify(error="Enter a search term (up to 80 characters)."), 400
    rows = db.search_records(q, PAGE, page * PAGE)
    if not rows and page == 0:
        try:
            db.upsert_records(openfda.search_any(q, limit=50), source="cache")
        except openfda.SourceError:
            pass
        rows = db.search_records(q, PAGE, 0)
    resp = jsonify(results=rows, page=page, has_more=len(rows) == PAGE)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/suggest")
def suggest():
    field = request.args.get("field", "")
    q = (request.args.get("q") or "")[:60]
    if field == "name":
        items = db.suggest_names(q)
    elif field == "batch":
        items = db.suggest_batches(q, (request.args.get("medicine") or "")[:120])
    else:
        return jsonify(error="Unknown field."), 400
    resp = jsonify(suggestions=items)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/stats")
def stats():
    resp = jsonify(**db.stats(), recent=db.recent_checks(10))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/sync")
def sync_db():
    try:
        return jsonify(sync.run())
    except openfda.SourceError as exc:
        return jsonify(error=str(exc)), 502
