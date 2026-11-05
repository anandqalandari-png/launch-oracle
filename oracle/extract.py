"""Pull human-meaningful strings out of minified JavaScript.

Three kinds survive the filters:
  flag  – an identifier passed to a feature-flag / experiment / gate call
  copy  – user-facing text (i18n defaultMessage or sentence-like literals)
  key   – flag-shaped identifiers (snake/kebab/dot) containing feature words
Everything else (class names, URLs, code fragments) is dropped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

STR_RE = re.compile(
    r'"((?:[^"\\\n]|\\.){3,400})"'
    r"|'((?:[^'\\\n]|\\.){3,400})'"
    r"|`((?:[^`\\$]|\\.|\$(?!\{)){3,400})`"  # template literals without ${…}
)

FLAG_CALL_RE = re.compile(
    r'(?:gate|Gate|flag|Flag|feature|Feature|experiment|Experiment|statsig|Statsig|growthbook|GrowthBook|'
    r'isEnabled|IsEnabled|unleash|Unleash|launchDarkly|variation|Variation)\w*\(\s*["\']([A-Za-z0-9_.:\-/]{3,90})["\']'
)
I18N_RE = re.compile(r'defaultMessage\s*:\s*"((?:[^"\\\n]|\\.){2,400})"')

KEY_SHAPE = re.compile(r"^[a-z][A-Za-z0-9]*(?:[_\-.:][A-Za-z0-9]+){1,8}$")
KEY_WORDS = (
    "enable", "flag", "gate", "experiment", "exp_", "beta", "rollout", "killswitch", "kill_switch",
    "feature", "labs", "preview", "launch", "waitlist", "early_access", "earlyaccess", "_v2", "_v3",
    "show_", "allow_", "use_new", "new_", "upsell", "paywall", "pricing", "tier", "plan_",
)
CODE_MARKERS = ("=>", "function", "return ", "var ", "const ", "{", "}", ";", "://", "\\u00", "</", "/>", "${")
CSS_TOKEN = re.compile(r"^[!\-]?[a-z0-9]+(?:[:\-/\[\].#%(),][a-z0-9\[\]\-/.#%(),]*)*$")
LETTER = re.compile(r"[A-Za-z]")
WORD = re.compile(r"[A-Za-z][a-z']{1,}")


@dataclass
class Hit:
    kind: str
    pos: int


def _decode(raw: str, quote: str) -> str:
    try:
        if quote == '"':
            return json.loads(f'"{raw}"')
        return json.loads('"' + raw.replace("\\'", "'").replace('"', '\\"') + '"')
    except Exception:
        return raw


def _is_copy(s: str) -> bool:
    if len(s) < 8 or len(s) > 300 or " " not in s:
        return False
    if any(m in s for m in CODE_MARKERS):
        return False
    letters = len(LETTER.findall(s))
    if letters / len(s) < 0.6:
        return False
    tokens = s.split()
    if sum(1 for t in tokens if CSS_TOKEN.match(t)) / len(tokens) >= 0.6 and not s[0].isupper():
        return False  # tailwind / class lists
    if len(WORD.findall(s)) < 2:
        return False
    # Product copy is sentence-like: capitalised, punctuated, or has an apostrophe.
    return s[0].isupper() or s.rstrip()[-1:] in ".?!…:" or "'" in s or "’" in s


def _is_key(s: str) -> bool:
    if len(s) < 6 or len(s) > 90 or not KEY_SHAPE.match(s):
        return False
    low = s.lower()
    return any(w in low for w in KEY_WORDS)


def extract(js: str) -> dict[str, Hit]:
    """Return {string: Hit} for one JS file. Flags beat copy beat keys when a string qualifies twice."""
    out: dict[str, Hit] = {}
    for m in FLAG_CALL_RE.finditer(js):
        out[m.group(1)] = Hit("flag", m.start(1))
    for m in I18N_RE.finditer(js):
        s = _decode(m.group(1), '"').strip()
        if s and s not in out and LETTER.search(s):
            out[s] = Hit("copy", m.start(1))
    for m in STR_RE.finditer(js):
        if m.group(1) is not None:
            raw, quote = m.group(1), '"'
        elif m.group(2) is not None:
            raw, quote = m.group(2), "'"
        else:
            raw, quote = m.group(3).replace("\\`", "`").replace("\n", "\\n"), "'"
        s = _decode(raw, quote).strip()
        if not s or s in out:
            continue
        if _is_copy(s):
            out[s] = Hit("copy", m.start())
        elif _is_key(s):
            out[s] = Hit("key", m.start())
    return out


def context(js: str, pos: int, width: int = 110) -> str:
    """Surrounding code, whitespace-collapsed, so Claude can see what a string sits next to."""
    snip = js[max(0, pos - width): pos + width]
    return re.sub(r"\s+", " ", snip)


# ---------- chunk discovery ----------

JS_LITERAL_RE = re.compile(r'["\'`]((?:https?:)?//[^"\'`\s]{4,300}?\.m?js|[A-Za-z0-9_./\-]{3,240}?\.m?js)["\'`]')
WEBPACK_PREFIX_RE = re.compile(r'"((?:[\w\-]+/)*static/chunks/)"\s*\+')
ID_HASH_MAP_RE = re.compile(r'\{((?:\d+:"[0-9a-f]{6,24}",?){2,})\}\[\w+\]')
ID_NAME_MAP_RE = re.compile(r'\(\{((?:\d+:"[\w\-]+",?){1,})\}\[\w+\]\s*\|\|\s*\w+\)')
PAIR_RE = re.compile(r'(\d+):"([\w\-]+)"')


def discover(js: str, file_url: str) -> set[str]:
    """Find more JS files referenced by this one (Vite deps, dynamic imports, webpack chunk maps)."""
    from urllib.parse import urljoin, urlsplit

    found: set[str] = set()
    parts = urlsplit(file_url)
    origin = f"{parts.scheme}://{parts.netloc}"

    for m in JS_LITERAL_RE.finditer(js):
        c = m.group(1)
        if c.startswith("//"):
            found.add(parts.scheme + ":" + c)
        elif c.startswith("http"):
            found.add(c)
        elif c.startswith("/"):
            found.add(origin + c)
        elif c.startswith("."):
            found.add(urljoin(file_url, c))
        else:
            first = c.split("/", 1)[0]
            marker = f"/{first}/"
            if "/" in c and marker in parts.path:
                base = parts.path[: parts.path.index(marker) + 1]
                found.add(origin + base + c)
            else:
                found.add(urljoin(file_url, c))

    # Next.js / webpack runtime: "static/chunks/" + ({id:"name"}[e]||e) + "." + {id:"hash"}[e] + ".js"
    for m in WEBPACK_PREFIX_RE.finditer(js):
        prefix = m.group(1)
        window = js[m.end(): m.end() + 200_000]
        hm = ID_HASH_MAP_RE.search(window)
        if not hm:
            continue
        names = {}
        nm = ID_NAME_MAP_RE.search(window[: hm.start() + 1])
        if nm:
            names = dict(PAIR_RE.findall(nm.group(1)))
        idx = parts.path.find("static/chunks/")
        if idx >= 0:
            base = origin + parts.path[:idx] + prefix[prefix.index("static/chunks/"):]
        else:
            base = urljoin(file_url, "/" + prefix.lstrip("/"))
        for cid, h in PAIR_RE.findall(hm.group(1)):
            found.add(f"{base}{names.get(cid, cid)}.{h}.js")
    return found


HASH_TAIL = re.compile(r"[.\-~_]([A-Za-z0-9_\-]{6,32})(?=\.m?js$)")  # base64url hashes may contain - and _


def stable_name(url: str) -> str:
    """File name with its content hash removed, so 'main-Ab12Cd34.js' and 'main-Zz98Yy76.js' match."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    path = p.path
    m = HASH_TAIL.search(path)
    if m and (any(ch.isdigit() for ch in m.group(1)) or m.group(1) != m.group(1).lower()):
        path = path[: m.start()] + path[m.end():]
    path = re.sub(r"/[0-9a-f]{8,}/", "/_/", path)  # hashed directories
    return p.netloc + path
