"""Load an app logged-out, collect every JavaScript file it ships, and extract strings.

Only public assets a logged-out browser already downloads (plus chunks those files
reference) are fetched. No login, no app-store binaries, no decompiling.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .config import App
from .extract import context, discover, extract, stable_name

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


@dataclass
class Found:
    kind: str
    file: str          # stable file name
    ctx: str


@dataclass
class Capture:
    app: str
    files: dict[str, str] = field(default_factory=dict)        # stable name -> sha1 of content
    strings: dict[str, Found] = field(default_factory=dict)
    bytes: int = 0
    errors: list[str] = field(default_factory=list)
    blocked: str = ""      # set when a bot check served a challenge instead of the app


CHALLENGE_MARKERS = ("challenges.cloudflare.com", "/cdn-cgi/challenge-platform", "captcha-delivery.com",
                     "hcaptcha.com", "perimeterx", "/_Incapsula_Resource")
CHALLENGE_TITLES = ("just a moment", "attention required", "access denied", "verify you are human",
                    "are you a robot", "security check")


def _is_js(url: str, ctype: str) -> bool:
    path = urlsplit(url).path
    return "javascript" in ctype or path.endswith((".js", ".mjs"))


def _ingest(cap: Capture, url: str, body: bytes) -> str:
    text = body.decode("utf-8", "replace")
    name = stable_name(url)
    cap.files[name] = hashlib.sha1(body).hexdigest()
    cap.bytes += len(body)
    for s, hit in extract(text).items():
        if s not in cap.strings or (cap.strings[s].kind != "flag" and hit.kind == "flag"):
            cap.strings[s] = Found(hit.kind, name, context(text, hit.pos))
    return text


async def capture_app(app: App, *, concurrency: int = 8) -> Capture:
    from playwright.async_api import async_playwright

    cap = Capture(app.id)
    seen_urls: set[str] = set()
    queue: list[str] = []
    hosts: set[str] = set()

    async with async_playwright() as pw:
        launch = {"headless": True}
        if os.environ.get("ORACLE_CHROME"):
            launch["executable_path"] = os.environ["ORACLE_CHROME"]
        browser = await pw.chromium.launch(**launch)
        ctx = await browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1366, "height": 900})
        page = await ctx.new_page()
        pending: list[asyncio.Task] = []

        async def on_response(resp):
            try:
                url = resp.url
                if any(m in url for m in CHALLENGE_MARKERS) and not cap.blocked:
                    cap.blocked = f"bot check ({urlsplit(url).netloc})"
                if url in seen_urls or not _is_js(url, resp.headers.get("content-type", "")):
                    return
                seen_urls.add(url)
                hosts.add(urlsplit(url).netloc)
                body = await resp.body()
                text = _ingest(cap, url, body)
                queue.extend(discover(text, url))
            except Exception as e:  # redirects, aborted loads
                cap.errors.append(f"response {resp.url[:120]}: {type(e).__name__}")

        page.on("response", lambda r: pending.append(asyncio.ensure_future(on_response(r))))

        for url in app.urls:
            hosts.add(urlsplit(url).netloc)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    pass
                await page.wait_for_timeout(3_000)
                title = (await page.title()).lower()
                if any(t in title for t in CHALLENGE_TITLES) and not cap.blocked:
                    cap.blocked = f"bot check (page title: {title[:60]})"
            except Exception as e:
                cap.errors.append(f"goto {url}: {type(e).__name__}: {str(e)[:160]}")
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if cap.blocked:
            await browser.close()
            return cap

        # Breadth-first through referenced chunks, staying on hosts the app itself used.
        sem = asyncio.Semaphore(concurrency)

        async def fetch(url: str):
            async with sem:
                if len(cap.files) >= app.max_files or cap.bytes >= app.max_bytes:
                    return
                try:
                    r = await ctx.request.get(url, timeout=30_000)
                    if r.status != 200 or not _is_js(url, r.headers.get("content-type", "")):
                        return
                    body = await r.body()
                    text = _ingest(cap, url, body)
                    queue.extend(discover(text, url))
                except Exception as e:
                    cap.errors.append(f"fetch {url[:120]}: {type(e).__name__}")

        while queue and len(cap.files) < app.max_files and cap.bytes < app.max_bytes:
            batch = []
            while queue and len(batch) < 64:
                u = queue.pop().split("#")[0]
                if u in seen_urls or urlsplit(u).netloc not in hosts:
                    continue
                seen_urls.add(u)
                batch.append(u)
            if batch:
                await asyncio.gather(*(fetch(u) for u in batch))

        await browser.close()
    return cap


def capture(app: App) -> Capture:
    return asyncio.run(capture_app(app))
