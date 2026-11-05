"""Render the public scoreboard (site/index.html with data embedded, plus site/data.json)."""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .ledger import load_predictions, runs
from .score import original_confidence, summarize

TEMPLATE = Path(__file__).with_name("site_template.html")


def build() -> Path:
    settings, apps = config.load()
    preds = load_predictions()
    for p in preds:
        p["original_confidence"] = original_confidence(p)
    watching = []
    for a in apps:
        r = runs(a.id)
        last = r[-1] if r else None
        watching.append({"id": a.id, "name": a.name, "last": last, "since": r[0]["date"] if r else None,
                         "nights": len(r)})
    data = {"generated": config.today(), "summary": summarize(preds), "predictions": preds,
            "watching": watching}
    config.SITE.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(data, ensure_ascii=False)
    (config.SITE / "data.json").write_text(blob)
    html = TEMPLATE.read_text().replace("__DATA__", blob.replace("</", "<\\/"))
    out = config.SITE / "index.html"
    out.write_text(html)
    return out
