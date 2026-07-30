"""HTML / JSON parsing helpers for iraqld pages."""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any


class CloudflareChallengeError(RuntimeError):
    """Raised when a response looks like a Cloudflare interstitial, not law HTML."""


_CHALLENGE_MARKERS = (
    "Just a moment",
    "cf-browser-verification",
    "challenge-platform",
    "Attention Required! | Cloudflare",
    "Enable JavaScript and cookies to continue",
    "cf-challenge",
)


def looks_like_cloudflare_challenge(body: str) -> bool:
    if not body:
        return False
    if "var articles" in body and "lawbookid" in body.lower():
        return False
    return any(m in body for m in _CHALLENGE_MARKERS)


def extract_js_array(page: str, var_name: str = "articles") -> list[Any] | None:
    """Extract ``var <name> = [...]`` with a string-aware bracket scan."""
    for marker in (f"var {var_name} =", f"var {var_name}="):
        idx = page.find(marker)
        if idx >= 0:
            break
    else:
        return None
    i = page.find("[", idx)
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for j in range(i, len(page)):
        ch = page[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in ('"', "'"):
            in_str = True
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(page[i : j + 1])
    return None


def html_to_text(fragment: str) -> str:
    fragment = html_lib.unescape(fragment or "")
    fragment = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?i)</p\s*>", "\n", fragment)
    fragment = re.sub(r"(?i)<[^>]+>", "", fragment)
    fragment = html_lib.unescape(fragment)
    fragment = fragment.replace("\xa0", " ").replace("\u200f", "").replace("\u200e", "")
    lines = [ln.strip() for ln in fragment.replace("\r", "").split("\n")]
    return "\n".join(ln for ln in lines if ln)


def articles_to_full_text(articles: list[dict] | None) -> str:
    if not articles:
        return ""
    parts: list[str] = []
    for art in sorted(articles, key=lambda a: a.get("displayOrder") or 0):
        code = (art.get("articleCodeTxt") or "").strip()
        body = html_to_text(art.get("articleText") or "")
        block: list[str] = []
        if code:
            block.append(code)
        if body:
            block.append(body)
        if block:
            parts.append("\n".join(block))
    return "\n\n".join(parts).strip()


def full_text_from_detail_html(page: str) -> str:
    if looks_like_cloudflare_challenge(page):
        raise CloudflareChallengeError(
            "Cloudflare challenge page received instead of legislation HTML. "
            "Use --mode playwright (attended) or rely on a published corpus release."
        )
    articles = extract_js_array(page, "articles")
    return articles_to_full_text(articles)
