"""Paths and app configuration."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(os.environ.get("ORACLE_ROOT", Path(__file__).resolve().parent.parent))
DATA = Path(os.environ.get("ORACLE_DATA", ROOT / "data"))
SITE = Path(os.environ.get("ORACLE_SITE", ROOT / "site"))
CONFIG = Path(os.environ.get("ORACLE_CONFIG", ROOT / "apps.toml"))


@dataclass
class App:
    id: str
    name: str
    urls: list[str]
    release_notes: list[str] = field(default_factory=list)
    window_days: int = 60
    max_files: int = 600
    max_bytes: int = 120_000_000


@dataclass
class Settings:
    model: str = "claude-sonnet-5-5"
    max_new_strings_per_app: int = 300
    expiry_multiplier: float = 2.0
    release_notes_chars: int = 40_000


def load() -> tuple[Settings, list[App]]:
    raw = tomllib.loads(CONFIG.read_text())
    s = raw.get("settings", {})
    settings = Settings(**{k: v for k, v in s.items() if k in Settings.__dataclass_fields__})
    if os.environ.get("ORACLE_MODEL"):
        settings.model = os.environ["ORACLE_MODEL"]
    apps = [App(**{k: v for k, v in a.items() if k in App.__dataclass_fields__}) for a in raw.get("app", [])]
    only = os.environ.get("ORACLE_ONLY")
    if only:
        wanted = {x.strip() for x in only.split(",")}
        apps = [a for a in apps if a.id in wanted]
    return settings, apps


def today() -> str:
    """ISO date for this run. ORACLE_TODAY overrides it (tests, backfills)."""
    return os.environ.get("ORACLE_TODAY") or date.today().isoformat()
