"""The scoreboard: hit rate, Brier score, calibration, lead time."""
from __future__ import annotations

from statistics import median

BUCKETS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]


def original_confidence(p: dict) -> float:
    """Grade the forecast as first published, not later revisions."""
    return p["history"][0]["confidence"] if p.get("history") else p["confidence"]


def outcome(p: dict) -> int | None:
    """1 = shipped inside the window, 0 = late or never, None = still open or void."""
    return {"shipped": 1, "late": 0, "expired": 0}.get(p["status"])


def summarize(preds: list[dict]) -> dict:
    scored = [p for p in preds if outcome(p) is not None]
    brier = (sum((original_confidence(p) - outcome(p)) ** 2 for p in scored) / len(scored)) if scored else None
    leads = [p["resolution"]["lead_days"] for p in preds
             if p["status"] in ("shipped", "late") and p.get("resolution", {}).get("lead_days") is not None]
    matches = [p["resolution"].get("prd_match") for p in preds
               if p["status"] in ("shipped", "late") and p["resolution"].get("prd_match") is not None]

    calib = []
    for lo, hi in BUCKETS:
        b = [p for p in scored if lo <= original_confidence(p) < hi]
        if b:
            calib.append({"lo": lo, "hi": min(hi, 1.0), "n": len(b),
                          "said": sum(original_confidence(p) for p in b) / len(b),
                          "happened": sum(outcome(p) for p in b) / len(b)})

    per_app: dict[str, dict] = {}
    for p in preds:
        a = per_app.setdefault(p["app"], {"name": p.get("app_name", p["app"]), "open": 0, "shipped": 0,
                                          "late": 0, "expired": 0, "void": 0})
        a[p["status"]] = a.get(p["status"], 0) + 1

    return {
        "total": len(preds),
        "open": sum(p["status"] == "open" for p in preds),
        "shipped": sum(p["status"] == "shipped" for p in preds),
        "late": sum(p["status"] == "late" for p in preds),
        "expired": sum(p["status"] == "expired" for p in preds),
        "void": sum(p["status"] == "void" for p in preds),
        "scored": len(scored),
        "brier": brier,
        "median_lead_days": median(leads) if leads else None,
        "avg_prd_match": (sum(matches) / len(matches)) if matches else None,
        "calibration": calib,
        "per_app": per_app,
    }
