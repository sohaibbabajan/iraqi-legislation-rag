"""
Deterministic article index: defines vs mentions.

Parses law `full_text` into rows for cache/article_index.jsonl — no LLM, no
embeddings. A *defines* row is an article that *is* that label (header + body
span). A *mentions* row is a mid-text citation of another article number.

This is the P0 fix for article_nums conflating "is art. 438" with "cites 438".
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from common import (
    ARTICLE_INDEX_FILE,
    _ARTICLE_REF,
    _ascii_article_num,
    default_corpus_path,
    iter_records,
)

BUILDER_VERSION = "1.0.0"

# Line-start defining headers: المادة 438 / المادة - ٣١ - / المادة 126 أ / المادة 43 مكرر
_DEFINE_HEADER = re.compile(
    r"(?m)^[ \t]*(?:المادة|ماد[ةه])[ \t]*[-–—]?[ \t]*"
    r"([٠-٩0-9]{1,4})"
    r"(?:[ \t]*([أ-يA-Za-z]))?"
    r"(?:[ \t]*(مكرر))?"
    r"[ \t]*$"
)


@dataclass
class ArticleIndexRow:
    law_book_id: int
    article_label: str
    role: str  # "defines" | "mentions"
    char_start: int
    char_end: int
    text: str = ""
    mentions_articles: list[str] = field(default_factory=list)
    in_article: str = ""  # for mentions: defining label that contains the cite
    parse_confidence: float = 1.0
    builder_version: str = BUILDER_VERSION

    def to_json(self) -> dict:
        d = asdict(self)
        if self.role == "defines":
            d.pop("in_article", None)
        else:
            d.pop("mentions_articles", None)
            d.pop("text", None)
        return d


def _label_from_parts(num: str, letter: str | None, mukarrar: str | None) -> str:
    base = _ascii_article_num(num)
    if not base:
        return ""
    if mukarrar:
        return f"{base}مكرر"
    if letter:
        # Normalize Arabic letter forms lightly; keep as single char suffix.
        return f"{base}{letter.strip()}"
    return base


def find_define_headers(full_text: str) -> list[tuple[int, int, str]]:
    """
    Return (start, end, article_label) for each defining header in full_text.
    Prefer explicit المادة headers; also accept bare digit-only lines.
    """
    if not full_text:
        return []

    headers: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []

    def _overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in occupied)

    for m in _DEFINE_HEADER.finditer(full_text):
        label = _label_from_parts(m.group(1), m.group(2), m.group(3))
        if not label:
            continue
        headers.append((m.start(), m.end(), label))
        occupied.append((m.start(), m.end()))

    # Bare digit-only lines (legacy PDF extraction style), if not already a header.
    bare = re.compile(r"(?m)^[ \t]*([٠-٩0-9]{1,4})[ \t]*$")
    for m in bare.finditer(full_text):
        if _overlaps(m.start(), m.end()):
            continue
        label = _ascii_article_num(m.group(1))
        if not label:
            continue
        headers.append((m.start(), m.end(), label))
        occupied.append((m.start(), m.end()))

    headers.sort(key=lambda x: x[0])
    return headers


def parse_full_text(law_book_id: int, full_text: str) -> list[ArticleIndexRow]:
    """Parse one law's full_text into defines + mentions rows."""
    text = full_text or ""
    if not text.strip():
        return []

    headers = find_define_headers(text)
    rows: list[ArticleIndexRow] = []

    if not headers:
        # No structural headers — still record mid-text citations as mentions
        # against an empty defining article (in_article="").
        for m in _ARTICLE_REF.finditer(text):
            label = _ascii_article_num(m.group(1))
            if not label:
                continue
            rows.append(ArticleIndexRow(
                law_book_id=law_book_id,
                article_label=label,
                role="mentions",
                char_start=m.start(1),
                char_end=m.end(1),
                in_article="",
                parse_confidence=0.5,
            ))
            if m.group(2):
                label2 = _ascii_article_num(m.group(2))
                if label2:
                    rows.append(ArticleIndexRow(
                        law_book_id=law_book_id,
                        article_label=label2,
                        role="mentions",
                        char_start=m.start(2),
                        char_end=m.end(2),
                        in_article="",
                        parse_confidence=0.5,
                    ))
        return rows

    for i, (h_start, h_end, label) in enumerate(headers):
        body_end = headers[i + 1][0] if i + 1 < len(headers) else len(text)
        # Article span = header through body (exclusive of next header).
        art_end = body_end
        # Trim trailing whitespace from span end for cleaner offsets.
        while art_end > h_end and text[art_end - 1] in "\n\r \t":
            art_end -= 1
        if art_end < h_end:
            art_end = h_end

        article_text = text[h_start:art_end]
        body = text[h_end:art_end]

        mention_labels: list[str] = []
        seen_m: set[str] = set()

        for m in _ARTICLE_REF.finditer(body):
            for num_g, gidx in ((m.group(1), 1), (m.group(2), 2)):
                if not num_g:
                    continue
                mlabel = _ascii_article_num(num_g)
                if not mlabel:
                    continue
                # Char span of the digit token itself.
                gs = h_end + m.start(gidx)
                ge = h_end + m.end(gidx)
                rows.append(ArticleIndexRow(
                    law_book_id=law_book_id,
                    article_label=mlabel,
                    role="mentions",
                    char_start=gs,
                    char_end=ge,
                    in_article=label,
                ))
                if mlabel not in seen_m:
                    seen_m.add(mlabel)
                    mention_labels.append(mlabel)

        rows.append(ArticleIndexRow(
            law_book_id=law_book_id,
            article_label=label,
            role="defines",
            char_start=h_start,
            char_end=art_end,
            text=article_text,
            mentions_articles=mention_labels,
        ))

    # Stable order: defines first by char_start, then mentions by char_start.
    defines = [r for r in rows if r.role == "defines"]
    mentions = [r for r in rows if r.role == "mentions"]
    defines.sort(key=lambda r: r.char_start)
    mentions.sort(key=lambda r: r.char_start)
    return defines + mentions


def index_record(rec: dict) -> list[ArticleIndexRow]:
    law_id = rec.get("lawBookID")
    if law_id is None:
        return []
    try:
        law_id = int(law_id)
    except (TypeError, ValueError):
        return []
    return parse_full_text(law_id, rec.get("full_text") or "")


def build_article_index(
    source: Path | None = None,
    *,
    limit: int = 0,
) -> list[ArticleIndexRow]:
    path = Path(source) if source else default_corpus_path()
    out: list[ArticleIndexRow] = []
    n = 0
    for rec in iter_records(path):
        out.extend(index_record(rec))
        n += 1
        if limit and n >= limit:
            break
    return out


def save_article_index(
    rows: Iterable[ArticleIndexRow],
    path: Path | None = None,
) -> Path:
    out = Path(path) if path else ARTICLE_INDEX_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.to_json(), ensure_ascii=False) + "\n")
    return out


def load_article_index(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else ARTICLE_INDEX_FILE
    if not p.exists():
        return []
    rows: list[dict] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def iter_defines(rows: Iterable[dict]) -> Iterator[dict]:
    for r in rows:
        if r.get("role") == "defines":
            yield r


def lookup_defines(
    rows: Iterable[dict],
    *,
    law_book_id: int | None = None,
    article_label: str | None = None,
) -> list[dict]:
    """Filter defines rows — exact-lookup helper for later ask.py wiring."""
    out: list[dict] = []
    for r in iter_defines(rows):
        if law_book_id is not None and int(r.get("law_book_id", -1)) != int(law_book_id):
            continue
        if article_label is not None and str(r.get("article_label")) != str(article_label):
            continue
        out.append(r)
    return out
