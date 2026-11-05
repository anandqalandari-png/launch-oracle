"""Launch Oracle command line.

  python -m oracle run                 nightly: capture → diff → forecast → grade → site
  python -m oracle capture [--no-llm]  capture + diff only (no forecasts)
  python -m oracle grade               grade open forecasts against release notes
  python -m oracle site                rebuild site/ from data/
  python -m oracle status              print open forecasts and the scoreboard
  python -m oracle resolve ID shipped --date YYYY-MM-DD [--source URL] [--prd-match 0-5]
  python -m oracle resolve ID void|expired|reopen
"""
from __future__ import annotations

import argparse
import json
import sys

from . import config
from .ledger import load_predictions, save_predictions


def _capture_all(apps, today, settings, forecast: bool) -> list[dict]:
    from .capture import capture
    from .hypothesize import hypothesize
    from .ledger import record

    out = []
    for app in apps:
        print(f"▸ {app.name}: capturing {', '.join(app.urls)}")
        try:
            cap = capture(app)
        except Exception as e:
            print(f"  capture failed: {type(e).__name__}: {e}")
            out.append({"app": app.id, "date": today, "ok": False, "errors": [str(e)[:200]]})
            continue
        summary = record(cap, today)
        new = summary.pop("new_strings")
        tag = "baseline recorded" if summary["baseline"] else f"{summary['new']} new strings"
        print(f"  {summary['files']} files, {summary['strings']} strings, {tag}"
              + (f", {len(cap.errors)} errors" if cap.errors else ""))
        if not cap.files:
            print("  nothing captured (blocked or offline) — skipping forecasts")
        elif forecast and new:
            try:
                r = hypothesize(app, settings, new, today)
                print(f"  forecasts: {r['created']} new, {r['updated']} updated · {r.get('noise', '')}")
                summary["forecast"] = r
            except Exception as e:
                print(f"  forecasting failed: {type(e).__name__}: {e}")
        out.append(summary)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="oracle")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    c = sub.add_parser("capture")
    c.add_argument("--no-llm", action="store_true")
    sub.add_parser("grade")
    sub.add_parser("site")
    sub.add_parser("status")
    r = sub.add_parser("resolve")
    r.add_argument("id")
    r.add_argument("outcome", choices=["shipped", "void", "expired", "reopen"])
    r.add_argument("--date")
    r.add_argument("--source", default="")
    r.add_argument("--quote", default="")
    r.add_argument("--prd-match", type=int)
    args = ap.parse_args(argv)

    settings, apps = config.load()
    today = config.today()

    if args.cmd in ("run", "capture"):
        _capture_all(apps, today, settings, forecast=(args.cmd == "run" or not args.no_llm))
    if args.cmd in ("run", "grade"):
        from .grade import grade
        for app in apps:
            try:
                g = grade(app, settings, today)
                if g["checked"]:
                    print(f"▸ graded {app.name}: {json.dumps(g)}")
            except Exception as e:
                print(f"▸ grading {app.name} failed: {type(e).__name__}: {e}")
    if args.cmd == "resolve":
        from .grade import close, resolve_shipped
        preds = load_predictions()
        p = next((x for x in preds if x["id"] == args.id), None)
        if not p:
            print(f"no forecast with id {args.id}")
            return 1
        if args.outcome == "shipped":
            if not args.date:
                print("--date is required for shipped")
                return 1
            resolve_shipped(p, args.date, today, source=args.source, quote=args.quote,
                            prd_match=args.prd_match, by="manual")
        elif args.outcome == "reopen":
            p["status"], p["resolution"] = "open", None
        else:
            close(p, args.outcome, today, source=args.source, by="manual")
        save_predictions(preds)
        print(f"{p['id']} → {p['status']}")
    if args.cmd in ("run", "grade", "site", "resolve"):
        from .site import build
        path = build()
        print(f"▸ site written to {path}")
    if args.cmd == "status":
        from .score import summarize
        preds = load_predictions()
        s = summarize(preds)
        for p in sorted((p for p in preds if p["status"] == "open"), key=lambda p: -p["confidence"]):
            print(f"{p['confidence']:>4.0%}  {p['app_name']:<8} {p['title']}  (by {p['deadline']})  [{p['id']}]")
        print(json.dumps({k: v for k, v in s.items() if k not in ("calibration", "per_app")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
