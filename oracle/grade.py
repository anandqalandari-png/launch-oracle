"""Check open forecasts against official release notes; close them as shipped, late, void or missed."""
from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import date, timedelta

from .capture import UA
from .config import App, Settings
from .ledger import load_predictions, save_predictions
from .llm import ask_json

SYSTEM = """You grade Launch Oracle's forecasts against {app}'s official release notes.

For each open forecast, decide whether the release notes below announce that capability (same user-facing ability; naming may differ). Be strict: a loosely related feature is not a match.

For each forecast return:
- shipped: true only if an entry clearly announces it
- ship_date: YYYY-MM-DD of that entry (null if not shipped or no date given)
- quote: up to 25 words copied EXACTLY from the release notes entry (null if not shipped)
- prd_match: 0–5, how well the forecast's PRD described what actually shipped (0 if not shipped)
- reason: one sentence

Reply with ONLY: {{"verdicts":[{{"id":"","shipped":false,"ship_date":null,"quote":null,"prd_match":0,"reason":""}}]}}"""


async def _fetch_texts(urls: list[str], limit: int) -> dict[str, str]:
    from playwright.async_api import async_playwright

    out = {}
    async with async_playwright() as pw:
        launch = {"headless": True}
        if os.environ.get("ORACLE_CHROME"):
            launch["executable_path"] = os.environ["ORACLE_CHROME"]
        browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(user_agent=UA, locale="en-US")
        page = await ctx.new_page()
        for u in urls:
            try:
                await page.goto(u, wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_timeout(2_500)
                out[u] = (await page.inner_text("body"))[:limit]
            except Exception as e:
                out[u] = ""
                print(f"  release notes unavailable: {u} ({type(e).__name__})")
        await browser.close()
    return out


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def _mock(user: str) -> dict:
    preds = json.loads(user.split("Open forecasts:\n", 1)[1].split("\n\nRelease notes", 1)[0])
    notes = user.split("Release notes", 1)[1].split("\n", 1)[1]  # drop the "(fetched …)" header
    lines = notes.splitlines()
    verdicts = []
    for p in preds:
        i = next((k for k, l in enumerate(lines) if l.lower().startswith(p["title"].lower())), None)
        hit = i is not None
        dates = [d for l in lines[:i] for d in re.findall(r"\d{4}-\d{2}-\d{2}", l)] if hit else []
        verdicts.append({"id": p["id"], "shipped": hit, "ship_date": dates[-1] if dates else None,
                         "quote": lines[i].strip()[:120] if hit else None, "prd_match": 4 if hit else 0,
                         "reason": "mock"})
    return {"verdicts": verdicts}


def close(p: dict, kind: str, today: str, **extra) -> None:
    p["status"] = kind
    p["resolution"] = {"date": today, **extra}


def resolve_shipped(p: dict, ship_date: str, today: str, **extra) -> None:
    """Classify a confirmed launch relative to the forecast."""
    created, deadline = date.fromisoformat(p["created"]), date.fromisoformat(p["deadline"])
    sd = date.fromisoformat(ship_date)
    if sd < created:
        close(p, "void", today, ship_date=ship_date, why="was already public when forecast", **extra)
        return
    lead = (sd - date.fromisoformat(p["first_evidence"])).days
    close(p, "shipped" if sd <= deadline else "late", today, ship_date=ship_date, lead_days=lead, **extra)


def grade(app: App, settings: Settings, today: str) -> dict:
    preds = load_predictions()
    open_preds = [p for p in preds if p["app"] == app.id and p["status"] == "open"]
    stats = {"checked": len(open_preds), "shipped": 0, "late": 0, "void": 0, "expired": 0, "review": 0}
    if not open_preds:
        return stats

    texts = {}
    if app.release_notes:
        if os.environ.get("ORACLE_RELEASE_NOTES_DIR"):  # tests / offline
            d = os.environ["ORACLE_RELEASE_NOTES_DIR"]
            p = os.path.join(d, f"{app.id}.txt")
            texts = {"file": open(p).read()} if os.path.exists(p) else {}
        else:
            texts = asyncio.run(_fetch_texts(app.release_notes, settings.release_notes_chars))
    corpus = "\n\n".join(t for t in texts.values() if t)

    if corpus.strip():
        payload = [{"id": p["id"], "title": p["title"], "one_liner": p["one_liner"],
                    "prd": p.get("prd", {}), "forecast_made": p["created"]} for p in open_preds]
        reply = ask_json(SYSTEM.format(app=app.name),
                         f"Open forecasts:\n{json.dumps(payload, ensure_ascii=False)}\n\nRelease notes (fetched {today}):\n{corpus}",
                         model=settings.model, max_tokens=4000, mock=_mock)
        ncorpus = _norm(corpus)
        by_id = {p["id"]: p for p in open_preds}
        for v in reply.get("verdicts", []):
            p = by_id.get(v.get("id"))
            if not p or not v.get("shipped"):
                continue
            quote = (v.get("quote") or "").strip()
            sd = v.get("ship_date")
            quote_ok = quote and _norm(quote)[:80] in ncorpus
            date_ok = bool(sd and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sd) and sd <= today)
            if not (quote_ok and date_ok):
                # Model claims a launch we can't verify; park it for a human instead of scoring it.
                p.setdefault("review", []).append({"date": today, "claim": v})
                stats["review"] += 1
                continue
            resolve_shipped(p, sd, today, source=app.release_notes[0] if app.release_notes else "",
                            quote=quote, prd_match=int(v.get("prd_match") or 0), by="grader")
            stats[p["status"]] += 1

    for p in open_preds:
        if p["status"] != "open":
            continue
        cutoff = date.fromisoformat(p["created"]) + timedelta(days=int(p["window_days"] * settings.expiry_multiplier))
        if date.fromisoformat(today) > cutoff:
            close(p, "expired", today, why=f"No launch found within {int(p['window_days'] * settings.expiry_multiplier)} days.")
            stats["expired"] += 1

    save_predictions(preds)
    return stats
