"""Local review UI for selecting and depositing a batch of EPrints PDFs."""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, abort, flash, g, redirect, render_template_string, request, send_file, url_for
from playwright.sync_api import sync_playwright

from eprint_login import (
    _launch_browser,
    _launch_saved_context,
    _required_env,
    lookup_rta_record,
    pdf_files,
    prepare_item_in_browser,
)

ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("EPRINTS_UI_DATABASE", ROOT / "eprints_ui.sqlite3"))
app = Flask(__name__)
app.secret_key = os.getenv("EPRINTS_UI_SECRET", "local-development-only")

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    month TEXT NOT NULL,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    nim TEXT,
    title TEXT,
    name TEXT,
    abstract TEXT,
    approval_date TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(month, filename)
)
"""

PAGE = """<!doctype html>
<title>EPrints batch review</title>
<h1>EPrints batch review</h1>
{% with messages = get_flashed_messages() %}{% for message in messages %}<p>{{ message }}</p>{% endfor %}{% endwith %}
<form method="post" action="{{ url_for('scan') }}">
  <label>Month <input name="month" required value="{{ month or '' }}" placeholder="September"></label>
  <label>Count <input name="count" type="number" min="1" value="{{ count or 10 }}"></label>
  <button>Find PDFs and fetch RTA data</button>
</form>
<form method="get" action="{{ url_for('index') }}">
  <label>View saved month <input name="month" required value="{{ month or '' }}" placeholder="September"></label>
  <button>View saved candidates</button>
</form>
{% if candidates %}
<form method="post" action="{{ url_for('approve') }}">
<input type="hidden" name="month" value="{{ month }}">
<table border="1" cellpadding="5">
<tr><th>PDF</th><th>NIM</th><th>Title</th><th>Name</th><th>Approval date</th><th>Status</th><th>Preview</th><th>Action</th></tr>
{% for c in candidates %}
<tr>
<td>{{ c.filename }}</td><td>{{ c.nim or '' }}</td><td>{{ c.title or '' }}</td><td>{{ c.name or '' }}</td>
<td><input type="date" name="date_{{ c.id }}" value="{{ c.approval_date or '' }}"></td>
<td>{{ c.status }}{% if c.error %}: {{ c.error }}{% endif %}</td>
<td><a href="{{ url_for('preview', candidate_id=c.id) }}">PDF</a></td>
<td>
{% if c.status in ('approved', 'processing', 'failed') and c.approval_date %}
<button type="submit" formaction="{{ url_for('process_candidate', candidate_id=c.id) }}" formmethod="post">Process</button>
{% endif %}
</td>
</tr>
{% endfor %}
</table>
<p><button>Save approval dates</button></p>
</form>
{% endif %}
"""


def get_db() -> sqlite3.Connection:
    if "db" in g:
        return g.db
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    g.db = db
    return g.db


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _month(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Month contains invalid characters.")
    return value


def _candidate_rows(db: sqlite3.Connection, month: str) -> list[sqlite3.Row]:
    return list(db.execute("SELECT * FROM candidates WHERE month=? ORDER BY filename", (month,)))


def _new_pdf_files(
    db: sqlite3.Connection, month: str, count: int
) -> list[Path]:
    existing = {
        row["filename"]
        for row in db.execute(
            "SELECT filename FROM candidates WHERE month=?",
            (month,),
        )
    }
    return [path for path in pdf_files(month) if path.name not in existing][:count]


def _nim(path: Path) -> str:
    match = re.match(r"([A-Za-z]\d+)-", path.name)
    if not match:
        raise ValueError(f"Could not extract a NIM from {path.name}.")
    return match.group(1)


@app.get("/")
def index() -> str:
    month = request.args.get("month", "")
    candidates = _candidate_rows(get_db(), month) if month else []
    return render_template_string(PAGE, candidates=candidates, month=month, count=len(candidates) or 10)


@app.post("/scan")
def scan() -> Any:
    try:
        month = _month(request.form["month"])
        count = max(1, int(request.form.get("count", "10")))
        db = get_db()
        files = _new_pdf_files(db, month, count)
        if not files:
            raise ValueError(
                f"No new PDF files found in file/{month}; "
                "completed and skipped files are excluded."
            )
        load_dotenv()
        email, password = _required_env("RTA_EMAIL"), _required_env("RTA_PASSWORD")
        with sync_playwright() as playwright:
            # RTA accepts headless mode; no browser window is needed for scanning.
            browser = _launch_browser(playwright, headless=True)
            try:
                for path in files:
                    nim = _nim(path)
                    rta_page = browser.new_page()
                    try:
                        record = lookup_rta_record(rta_page, email, password, nim)
                        db.execute(
                            """INSERT INTO candidates
                            (month, filename, path, nim, title, name, abstract, status, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', CURRENT_TIMESTAMP)
                            ON CONFLICT(month, filename) DO UPDATE SET path=excluded.path,
                            nim=excluded.nim, title=excluded.title, name=excluded.name,
                            abstract=excluded.abstract, error=NULL, updated_at=CURRENT_TIMESTAMP""",
                            (month, path.name, str(path), record.nim, record.title, record.name, record.abstract),
                        )
                    except Exception as exc:
                        db.execute(
                            """INSERT INTO candidates (month, filename, path, nim, status, error)
                            VALUES (?, ?, ?, ?, 'failed', ?)
                            ON CONFLICT(month, filename) DO UPDATE SET error=excluded.error,
                            status='failed', updated_at=CURRENT_TIMESTAMP""",
                            (month, path.name, str(path), nim, str(exc)),
                        )
                    finally:
                        rta_page.close()
                db.commit()
            finally:
                browser.close()
        flash(f"Loaded {len(files)} PDF candidate(s).")
    except (ValueError, RuntimeError) as exc:
        flash(str(exc))
    return redirect(url_for("index", month=request.form.get("month", "")))


@app.post("/approve")
def approve() -> Any:
    month = _month(request.form["month"])
    db = get_db()
    for row in _candidate_rows(db, month):
        approval = request.form.get(f"date_{row['id']}", "").strip()
        if approval:
            try:
                date.fromisoformat(approval)
            except ValueError:
                flash(f"Invalid approval date for {row['filename']}.")
                continue
            if row["status"] not in ("completed", "skipped"):
                db.execute("UPDATE candidates SET approval_date=?, status='approved', error=NULL WHERE id=?",
                           (approval, row["id"]))
    db.commit()
    flash("Approval dates saved. Nothing is deposited until you submit processing.")
    return redirect(url_for("index", month=month))


@app.post("/process/<int:candidate_id>")
def process_candidate(candidate_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row:
        abort(404)
    month = row["month"]
    if row["status"] not in ("approved", "processing", "failed") or not row["approval_date"]:
        flash(f"{row['filename']} belum siap diproses. Isi tanggal approval terlebih dahulu.")
        return redirect(url_for("index", month=month))
    load_dotenv()
    credentials = (_required_env("EPRINT_USERNAME"), _required_env("EPRINT_PASSWORD"),
                   _required_env("RTA_EMAIL"), _required_env("RTA_PASSWORD"))
    with sync_playwright() as playwright:
        try:
            browser = _launch_saved_context(playwright, headless=False)
        except Exception as exc:
            if "existing browser session" in str(exc):
                flash(
                    "Browser login EPrints masih terbuka. Tutup browser tersebut "
                    "terlebih dahulu, lalu klik Process lagi."
                )
                return redirect(url_for("index", month=month))
            raise
        try:
            path = Path(row["path"])
            if not path.is_file():
                db.execute("UPDATE candidates SET status='failed', error=? WHERE id=?", ("PDF no longer exists", row["id"]))
            else:
                db.execute("UPDATE candidates SET status='processing', error=NULL WHERE id=?", (row["id"],))
                db.commit()
                try:
                    deposited = prepare_item_in_browser(
                        browser, *credentials, month, True, path,
                        date.fromisoformat(row["approval_date"]), True
                    )
                    status = "completed" if deposited else "skipped"
                    db.execute("UPDATE candidates SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                               (status, row["id"]))
                except Exception as exc:
                    db.execute("UPDATE candidates SET status='failed', error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                               (str(exc), row["id"]))
            db.commit()
        finally:
            browser.close()
    flash(f"Finished processing {row['filename']}.")
    return redirect(url_for("index", month=month))


@app.get("/preview/<int:candidate_id>")
def preview(candidate_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT path, filename FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    if not row or not Path(row["path"]).is_file():
        abort(404)
    return send_file(row["path"], mimetype="application/pdf", as_attachment=False, download_name=row["filename"])


if __name__ == "__main__":
    load_dotenv()
    app.run(host=os.getenv("EPRINTS_UI_HOST", "127.0.0.1"), port=int(os.getenv("EPRINTS_UI_PORT", "5000")), debug=False)
