"""Turn today's new strings into forecasts: one shadow PRD + probability per suspected feature."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .config import App, Settings
from .ledger import load_predictions, load_seen, save_predictions
from .llm import ask_json

SIGNAL_WORDS = ("new", "beta", "try ", "introducing", "coming soon", "early access", "waitlist", "upgrade",
                "now you can", "labs", "preview", "experimental", "invite", "share", "agent", "plan", "pro",
                "team", "workspace", "connect", "import", "export", "schedule", "memory", "voice", "video")


def rank(strings: list[dict], limit: int) -> list[dict]:
    """Most feature-like first; strings from never-before-seen files are discounted."""
    def score(s: dict) -> float:
        t = s["text"].lower()
        sc = {"flag": 3.0, "copy": 2.0, "key": 1.5}.get(s["kind"], 1.0)
        sc += sum(0.6 for w in SIGNAL_WORDS if w in t)
        n = len(s["text"])
        sc += 0.8 if 15 <= n <= 160 else 0
        if s.get("new_chunk"):
            sc -= 1.2
        return sc
    out, norms = [], set()
    for s in sorted(strings, key=score, reverse=True):
        norm = re.sub(r"[^a-z]", "", s["text"].lower())[:60]
        if norm in norms:
            continue
        norms.add(norm)
        out.append(s)
        if len(out) >= limit:
            break
    return out


SYSTEM = """You are the forecasting engine of Launch Oracle, a public experiment that predicts unreleased product features from code and is graded on whether they ship.

You receive strings that appeared for the FIRST TIME today in the public JavaScript a logged-out browser downloads from {app}. Each comes with its kind (flag = argument to a feature-flag/experiment call; copy = user-facing text; key = flag-shaped identifier), the code around it, and new_chunk=true if it came from a code file we had never downloaded before (so it may be an OLD feature we simply had not seen).

Your job: find unreleased, user-facing capabilities and forecast them. Be strict.
- Most new strings are noise: wording tweaks, error messages, analytics events, refactors, legal text, localisation, internal admin tools, and marketing for features that are already public. Ignore them.
- Output a feature only if at least two strings corroborate it, or one flag/copy string is unambiguous on its own.
- If you know the capability was already publicly available in {app} before {today}, skip it.
- Ground every claim in the evidence. Where the PRD goes beyond the strings, say "Inferred:".
- confidence = probability that the feature becomes publicly available (to at least some regular users, and visible in official release notes or announcements) within window_days of {today}. Use the full 0.05–0.95 range; internal tools and vague experiments deserve low numbers; polished onboarding copy and pricing text deserve higher ones. Do not cluster around 0.7.
- window_days: 14–180, your best guess of the shipping horizon. Default {window}.
- evidence must quote input strings EXACTLY (copy them character for character).

You also get the currently open forecasts for {app}. If today's strings support one of them, put it in "updates" instead of creating a duplicate.

Reply with ONLY this JSON:
{{"features":[{{"title":"short noun phrase","one_liner":"what a user will be able to do","category":"new_feature|expansion|pricing|integration|experiment|other","evidence":["exact string", "..."],"prd":{{"problem":"","users":"","what":"","how_it_appears":"where in the product the strings suggest it lives","rollout_guess":"","open_questions":["",""]}},"window_days":60,"confidence":0.4}}],
 "updates":[{{"id":"existing forecast id","evidence":["exact string"],"confidence":0.5,"note":"what the new evidence changes"}}],
 "noise_summary":"one sentence describing what the remaining strings were"}}"""


def _user_prompt(app: App, strings: list[dict], open_preds: list[dict]) -> str:
    items = [{"text": s["text"], "kind": s["kind"], "new_chunk": s.get("new_chunk", False),
              "code": s.get("ctx", "")[:220]} for s in strings]
    existing = [{"id": p["id"], "title": p["title"], "one_liner": p["one_liner"]} for p in open_preds]
    return (f"App: {app.name}\nOpen forecasts: {json.dumps(existing, ensure_ascii=False)}\n\n"
            f"New strings ({len(items)}):\n{json.dumps(items, ensure_ascii=False, indent=0)}")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "feature"


def _mock(user: str) -> dict:
    """Deterministic stand-in for tests: one feature per distinct 'Introducing X' / flag cluster."""
    strings = json.loads(user.split("):\n", 1)[1])
    texts = [s["text"] for s in strings]
    feats = []
    flags = [t for s, t in zip(strings, texts) if s["kind"] == "flag"]
    for f in flags:
        stem = f.split("_")[-1] if "_" in f else f
        related = [t for t in texts if stem.lower() in t.lower() and t != f][:3]
        feats.append({"title": stem.replace("-", " ").title(), "one_liner": f"Use {stem}", "category": "new_feature",
                      "evidence": [f] + related + ["NOT A REAL STRING"],
                      "prd": {"problem": "p", "users": "u", "what": "w", "how_it_appears": "h",
                              "rollout_guess": "r", "open_questions": ["q"]},
                      "window_days": 30, "confidence": 0.7})
    return {"features": feats, "updates": [], "noise_summary": "mock"}


def hypothesize(app: App, settings: Settings, new_strings: list[dict], today: str) -> dict:
    if not new_strings:
        return {"created": 0, "updated": 0, "note": "no new strings"}
    preds = load_predictions()
    open_preds = [p for p in preds if p["app"] == app.id and p["status"] == "open"]
    batch = rank(new_strings, settings.max_new_strings_per_app)
    by_text = {s["text"]: s for s in batch}
    seen = load_seen(app.id)

    reply = ask_json(SYSTEM.format(app=app.name, today=today, window=app.window_days),
                     _user_prompt(app, batch, open_preds), model=settings.model, mock=_mock)

    def valid_evidence(ev: list) -> list[dict]:
        out = []
        for t in ev or []:
            if isinstance(t, str) and t in by_text:  # drop anything the model paraphrased or invented
                s = by_text[t]
                out.append({"text": t, "kind": s["kind"], "first_seen": seen.get(t, (today,))[0],
                            "new_chunk": s.get("new_chunk", False)})
        return out

    created = updated = 0
    ids = {p["id"] for p in preds}
    for f in reply.get("features", []):
        ev = valid_evidence(f.get("evidence"))
        if not ev or not f.get("title"):
            continue
        conf = min(0.95, max(0.05, float(f.get("confidence", 0.5))))
        window = int(min(180, max(14, int(f.get("window_days") or app.window_days))))
        pid = f"{app.id}-{today}-{_slug(f['title'])}"
        while pid in ids:
            pid += "-x"
        ids.add(pid)
        preds.append({
            "id": pid, "app": app.id, "app_name": app.name, "title": f["title"].strip(),
            "one_liner": f.get("one_liner", "").strip(), "category": f.get("category", "other"),
            "created": today, "first_evidence": min(e["first_seen"] for e in ev),
            "window_days": window,
            "deadline": (date.fromisoformat(today) + timedelta(days=window)).isoformat(),
            "confidence": conf, "history": [{"date": today, "confidence": conf, "note": "forecast made"}],
            "evidence": ev, "prd": f.get("prd", {}), "status": "open", "resolution": None,
        })
        created += 1

    by_id = {p["id"]: p for p in preds}
    for u in reply.get("updates", []):
        p = by_id.get(u.get("id"))
        if not p or p["status"] != "open":
            continue
        ev = valid_evidence(u.get("evidence"))
        known = {e["text"] for e in p["evidence"]}
        p["evidence"] += [e for e in ev if e["text"] not in known]
        if u.get("confidence") is not None:
            c = min(0.95, max(0.05, float(u["confidence"])))
            p["history"].append({"date": today, "confidence": c, "note": u.get("note", "")[:240]})
            p["confidence"] = c  # latest view; scoring uses the original (history[0])
        updated += 1

    save_predictions(preds)
    return {"created": created, "updated": updated, "sent": len(batch), "noise": reply.get("noise_summary", "")}
