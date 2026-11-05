"""Minimal Claude client (stdlib only) with JSON extraction and a mock mode for tests."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable

API = "https://api.anthropic.com/v1/messages"


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError("no JSON object in model reply")
    return json.loads(text[start: end + 1])


def ask_json(system: str, user: str, *, model: str, max_tokens: int = 8000,
             mock: Callable[[str], dict] | None = None) -> dict:
    """Send one prompt, return the parsed JSON object from the reply. Retries once on bad JSON."""
    if os.environ.get("ORACLE_MOCK") == "1":
        if mock is None:
            raise LLMError("mock mode without a mock function")
        return mock(user)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise LLMError("ANTHROPIC_API_KEY is not set")

    messages = [{"role": "user", "content": user}]
    last_err: Exception | None = None
    for attempt in range(3):
        body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": messages}).encode()
        req = urllib.request.Request(API, data=body, method="POST", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 529) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            raise LLMError(f"API {e.code}: {detail}") from e
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        try:
            return _extract_json(text)
        except (LLMError, json.JSONDecodeError) as e:
            last_err = e
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": "That was not valid JSON. Reply with only the JSON object."}]
    raise LLMError(f"model did not return valid JSON: {last_err}")
