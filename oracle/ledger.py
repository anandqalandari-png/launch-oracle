"""Per-app memory of every string and file ever seen, plus the daily 'new' sets."""
from __future__ import annotations

import json
from pathlib import Path

from .capture import Capture
from .config import DATA


def app_dir(app_id: str) -> Path:
    d = DATA / app_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_seen(app_id: str) -> dict[str, tuple[str, str]]:
    """{string: (first_seen, kind)}"""
    p = app_dir(app_id) / "seen.tsv"
    out: dict[str, tuple[str, str]] = {}
    if p.exists():
        for line in p.read_text().splitlines():
            first, kind, text = line.split("\t", 2)
            out[json.loads(text)] = (first, kind)
    return out


def save_seen(app_id: str, seen: dict[str, tuple[str, str]]) -> None:
    rows = sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0]))
    lines = [f"{first}\t{kind}\t{json.dumps(text, ensure_ascii=False)}" for text, (first, kind) in rows]
    (app_dir(app_id) / "seen.tsv").write_text("\n".join(lines) + ("\n" if lines else ""))


def load_files(app_id: str) -> dict[str, str]:
    p = app_dir(app_id) / "files.json"
    return json.loads(p.read_text()) if p.exists() else {}


def record(cap: Capture, today: str) -> dict:
    """Merge a capture into the ledger. Returns a run summary including today's new strings."""
    if cap.blocked:  # never let a challenge page into the ledger
        summary = {"date": today, "app": cap.app, "baseline": False, "files": 0, "bytes": 0, "strings": 0,
                   "new": 0, "new_files": 0, "ok": False, "blocked": True, "errors": [f"blocked by {cap.blocked}"]}
        with (app_dir(cap.app) / "runs.jsonl").open("a") as fh:
            fh.write(json.dumps(summary) + "\n")
        return {**summary, "new_strings": []}
    seen = load_seen(cap.app)
    files_before = load_files(cap.app)
    baseline = not seen
    new = []
    for s, f in cap.strings.items():
        if s in seen:
            continue
        seen[s] = (today, f.kind)
        if not baseline:
            new.append({"text": s, "kind": f.kind, "file": f.file, "ctx": f.ctx,
                        "new_chunk": f.file not in files_before})
    files = dict(files_before)
    for name in cap.files:
        files.setdefault(name, today)
    save_seen(cap.app, seen)
    (app_dir(cap.app) / "files.json").write_text(json.dumps(files, indent=0, sort_keys=True))

    summary = {
        "date": today, "app": cap.app, "baseline": baseline, "files": len(cap.files),
        "bytes": cap.bytes, "strings": len(cap.strings), "new": len(new),
        "new_files": sum(1 for n in cap.files if n not in files_before) if not baseline else 0,
        "errors": cap.errors[:20], "ok": bool(cap.files),
    }
    if new:
        (app_dir(cap.app) / "new").mkdir(exist_ok=True)
        (app_dir(cap.app) / "new" / f"{today}.json").write_text(json.dumps(new, ensure_ascii=False, indent=1))
    with (app_dir(cap.app) / "runs.jsonl").open("a") as fh:
        fh.write(json.dumps(summary) + "\n")
    summary["new_strings"] = new
    return summary


def load_new(app_id: str, day: str) -> list[dict]:
    p = app_dir(app_id) / "new" / f"{day}.json"
    return json.loads(p.read_text()) if p.exists() else []


def runs(app_id: str) -> list[dict]:
    p = app_dir(app_id) / "runs.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


# ---------- predictions ----------

PRED = lambda: DATA / "predictions.json"  # noqa: E731


def load_predictions() -> list[dict]:
    return json.loads(PRED().read_text()) if PRED().exists() else []


def save_predictions(preds: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PRED().write_text(json.dumps(preds, ensure_ascii=False, indent=1))
