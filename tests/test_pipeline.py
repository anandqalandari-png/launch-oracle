"""End-to-end: baseline night → new deploy with a hidden feature → forecast → launch → graded → site.

Runs a real headless browser against a local two-version fixture app; Claude is mocked (ORACLE_MOCK=1).
"""
import functools
import json
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


class Server:
    def __init__(self):
        self.root = FIX / "v1"
        handler = functools.partial(self._Handler, self)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, srv, *a, **kw):
            super().__init__(*a, directory=str(srv.root), **kw)

        def log_message(self, *a):
            pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    srv = Server()
    cfg = tmp_path / "apps.toml"
    cfg.write_text(f'''[settings]
model = "mock"
[[app]]
id = "fixture"
name = "Fixture"
urls = ["{srv.url}"]
release_notes = ["{srv.url}notes"]
window_days = 30
''')
    monkeypatch.setenv("ORACLE_CONFIG", str(cfg))
    monkeypatch.setenv("ORACLE_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("ORACLE_SITE", str(tmp_path / "site"))
    monkeypatch.setenv("ORACLE_MOCK", "1")
    monkeypatch.setenv("ORACLE_RELEASE_NOTES_DIR", str(tmp_path / "notes"))
    if shutil.which("google-chrome") and not os.environ.get("ORACLE_CHROME"):
        monkeypatch.setenv("ORACLE_CHROME", shutil.which("google-chrome"))
    import importlib
    import oracle.config
    importlib.reload(oracle.config)
    for m in ("oracle.ledger", "oracle.hypothesize", "oracle.grade", "oracle.site", "oracle.__main__"):
        importlib.reload(importlib.import_module(m))
    yield srv, tmp_path
    srv.httpd.shutdown()


def run(day, monkeypatch, *args):
    monkeypatch.setenv("ORACLE_TODAY", day)
    from oracle.__main__ import main
    assert main(list(args) or ["run"]) == 0


def test_full_loop(env, monkeypatch):
    srv, tmp = env
    data = tmp / "data"

    # Night 1: baseline only.
    run("2026-11-01", monkeypatch)
    seen = (data / "fixture" / "seen.tsv").read_text()
    for s in ("Start a new chat", "Manage your account settings.", "Open the side panel.", "Export to PDF."):
        assert json.dumps(s) in seen, f"{s} not captured (chunk discovery broken?)"
    assert not (data / "predictions.json").exists()

    # Night 2: new deploy. Only genuinely new strings count; the hidden feature becomes a forecast.
    srv.root = FIX / "v2"
    run("2026-11-02", monkeypatch)
    new = {s["text"] for s in json.loads((data / "fixture" / "new" / "2026-11-02.json").read_text())}
    assert {"labs_voice_notes", "Try voice notes", "Voice notes are in beta."} <= new
    assert "Start a new chat" not in new and "Export to PDF." not in new
    preds = json.loads((data / "predictions.json").read_text())
    assert len(preds) == 1
    p = preds[0]
    assert p["status"] == "open" and p["deadline"] == "2026-12-02"
    texts = {e["text"] for e in p["evidence"]}
    assert "labs_voice_notes" in texts and "NOT A REAL STRING" not in texts  # invented evidence is dropped

    # Night 3: no release notes yet → still open.
    run("2026-11-10", monkeypatch)
    assert json.loads((data / "predictions.json").read_text())[0]["status"] == "open"

    # Night 4: release notes announce it → shipped on time, lead time measured from first clue.
    (tmp / "notes").mkdir()
    shutil.copy(FIX / "notes" / "fixture.txt", tmp / "notes" / "fixture.txt")
    run("2026-11-21", monkeypatch)
    p = json.loads((data / "predictions.json").read_text())[0]
    assert p["status"] == "shipped", p.get("review")
    assert p["resolution"]["ship_date"] == "2026-11-20"
    assert p["resolution"]["lead_days"] == 18

    html = (tmp / "site" / "index.html").read_text()
    assert '"status": "shipped"' in html and "__DATA__" not in html


def test_unverifiable_launch_goes_to_review(env, monkeypatch):
    """If the model claims a launch but its quote isn't in the release notes, nothing is scored."""
    srv, tmp = env
    run("2026-11-01", monkeypatch)
    srv.root = FIX / "v2"
    run("2026-11-02", monkeypatch)
    import oracle.grade as g
    monkeypatch.setattr(g, "_mock", lambda user: {"verdicts": [{
        "id": json.loads((tmp / "data" / "predictions.json").read_text())[0]["id"],
        "shipped": True, "ship_date": "2026-11-05", "quote": "Voice notes launched worldwide today",
        "prd_match": 5, "reason": "x"}]})
    (tmp / "notes").mkdir()
    (tmp / "notes" / "fixture.txt").write_text("2026-11-05\nSmall bug fixes.")
    run("2026-11-06", monkeypatch, "grade")
    p = json.loads((tmp / "data" / "predictions.json").read_text())[0]
    assert p["status"] == "open" and p["review"]


def test_expiry_and_void(env, monkeypatch):
    srv, tmp = env
    run("2026-11-01", monkeypatch)
    srv.root = FIX / "v2"
    run("2026-11-02", monkeypatch)
    pid = json.loads((tmp / "data" / "predictions.json").read_text())[0]["id"]

    run("2026-11-03", monkeypatch, "resolve", pid, "shipped", "--date", "2026-10-01")
    assert json.loads((tmp / "data" / "predictions.json").read_text())[0]["status"] == "void"

    run("2026-11-03", monkeypatch, "resolve", pid, "reopen")
    run("2027-01-03", monkeypatch, "grade")   # 30-day window × 2 = 60 days → expired after 1 Jan
    assert json.loads((tmp / "data" / "predictions.json").read_text())[0]["status"] == "expired"
