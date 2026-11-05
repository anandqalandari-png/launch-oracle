# Launch Oracle

Forecasts unreleased features from the JavaScript that ChatGPT, Claude, Gemini, Notion and Linear already ship to every logged-out visitor, then grades each forecast in public when the feature launches, or doesn't.

Every night:

1. **Capture.** A headless browser opens each app logged-out and downloads the same code any visitor gets, plus the lazy-loaded chunks those files point to (Vite dependency lists, dynamic imports and webpack/Next.js chunk maps).
2. **Diff.** Every human-readable string (interface copy, feature-flag names, i18n messages) goes into a per-app ledger. The first night is the baseline. After that, a string never seen before is a clue.
3. **Forecast.** Claude reads the night's clues, discards noise, and writes a forecast per real feature: a shadow spec (problem, users, what it does, where it lives, rollout guess), a deadline and a probability. Evidence must quote the code exactly. Anything paraphrased or invented is dropped before saving.
4. **Grade.** Open forecasts are checked against each company's official release notes.
   - A claimed launch counts only if the quoted release-note line is actually on the page; otherwise it is parked for a human to review.
   - Shipped before the deadline is a hit; after the deadline, late; nothing by twice the window, a miss.
   - Features that were already public get voided rather than counted.
5. **Publish.** `site/index.html` shows open forecasts, settled verdicts, a calibration chart and the Brier score of every forecast as first published. Visitors can make their own call on each forecast; their calls are kept in their browser and scored against the oracle's.

## Set up (10 minutes)

1. Create an empty GitHub repo and push this folder to it.
2. **Settings → Secrets and variables → Actions → New repository secret**: `ANTHROPIC_API_KEY`.
3. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
4. **Actions → Launch Oracle nightly → Run workflow.** This first run records the baseline. Forecasts start on the first night something new appears.

The workflow runs at 03:17 UTC daily, commits `data/` and `site/` back to the repo (that history is the audit trail) and deploys the site to GitHub Pages.

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...
python -m oracle run            # capture → diff → forecast → grade → site
python -m oracle status         # open forecasts + scoreboard in the terminal
open site/index.html
```

Useful environment variables:

| Variable | What it does |
| --- | --- |
| `ORACLE_ONLY=chatgpt,claude` | Run a subset of apps. |
| `ORACLE_MODEL` | Override the model in `apps.toml`. |
| `ORACLE_CHROME=/path/to/chrome` | Use an installed Chrome instead of Playwright's. |

## Correct the grader

The grader is strict, so some real launches will need a human:

```bash
python -m oracle resolve chatgpt-2026-11-02-voice-notes shipped --date 2026-11-20 \
  --source https://help.openai.com/... --quote "Voice notes are now available" --prd-match 4
python -m oracle resolve <id> void       # it was already public
python -m oracle resolve <id> expired    # call it a miss early
python -m oracle resolve <id> reopen
```

Parked claims are stored on the forecast under `review` in `data/predictions.json`.

## Add or change apps

Edit `apps.toml`: `urls` are the pages to load (login pages still ship the app bundle), `release_notes` are the pages the grader reads, `window_days` is the default horizon.

## Known limits

- **Blocked captures.** Some sites block datacenter IPs, including GitHub's runners (Cloudflare-protected apps are the usual suspects). A blocked app shows as failed in the site footer and is skipped for that night. Fixes, in order of effort:
  - Run `python -m oracle run` on your own machine on a schedule and push.
  - Register your machine as a self-hosted runner and change `runs-on` to `self-hosted`.
- **Never-downloaded chunks.** A chunk downloaded for the first time can look "new" even though its code is old. Those strings are flagged `new_chunk` and ranked lower, and Claude is told to skip features it knows are already public.
- **Release-note gaps.** Release notes don't announce everything. Quiet launches that never reach them need `resolve` by hand.
- **Scope.** Only public JavaScript served to logged-out visitors is read: no logins, no app binaries, no decompiling. Forecasts are machine-written guesses, not insider information.

## Layout

```
apps.toml                  what to watch
oracle/capture.py          headless browser + chunk crawler
oracle/extract.py          string extraction, chunk discovery, hash-stable file names
oracle/ledger.py           seen-string ledger, nightly new sets, predictions store
oracle/hypothesize.py      ranking + Claude forecasting with evidence validation
oracle/grade.py            release-note grading, expiry, manual resolution
oracle/score.py            Brier score, calibration, lead time
oracle/site.py             builds site/ from data/
data/<app>/seen.tsv        every string ever seen, with first-seen date
data/<app>/new/DATE.json   that night's new strings with code context
data/<app>/runs.jsonl      capture log
data/predictions.json      every forecast and its verdict
tests/                     unit tests + end-to-end run against a local two-version fixture app
```

Tests: `pytest -q`. The end-to-end test runs a real browser against `tests/fixtures`, with Claude mocked.
