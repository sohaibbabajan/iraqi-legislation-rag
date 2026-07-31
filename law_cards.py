"""
law_cards.py — upfront LLM "law cards" + alias lexicon (routing/UI only).

CRITICAL: Cards and the alias lexicon are NEVER injected into the answer LLM
context. They exist only for:
  - law routing (colloquial alias → law_book_id)
  - future UI metadata (scope summary, tags, sample questions)

Answer generation still sees retrieved law chunks only (ask.py build_context).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterable

from common import ALIAS_LEXICON_FILE, LAW_CARDS_FILE, normalize_ar

# Serialize JSONL appends when build_law_cards runs with ThreadPoolExecutor.
_APPEND_LOCK = threading.Lock()

# JSON Schema for OpenRouter structured outputs / local validation.
LAW_CARD_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "scope_summary",
        "subject_tags",
        "colloquial_aliases",
        "likely_questions",
    ],
    "properties": {
        "scope_summary": {
            "type": "string",
            "description": "1–3 Arabic sentences: what this instrument covers.",
        },
        "subject_tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 12,
        },
        "colloquial_aliases": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
            "maxItems": 16,
            "description": "How people casually name this law (Arabic).",
        },
        "likely_questions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 10,
            "description": "Likely user questions this law answers.",
        },
        "title_en": {
            "type": "string",
            "description": "Optional short English title.",
        },
    },
}

CARD_SYSTEM_PROMPT = """\
أنت مساعد فهرسة تشريعات عراقية. أعطِ بطاقة JSON موجزة عن القانون المعطى فقط.
لا تقدّم استشارة قانونية. لا تخترع مواد أو أرقام غير موجودة في النص.
aliases يجب أن تكون أسماء دارجة قصيرة يستخدمها الناس (مثل «قانون العقوبات»
أو «قانون العمل») — بدون كلمة «العراقي» وبدون رقم/سنة إلا إن لزم التمييز.
اكتب كل الحقول بالعربية إلا title_en إن وُجد.
"""

_MIN_ALIAS_LEN = 4
_MAX_TEXT_CHARS = 4500

# Amendment / secondary-instrument markers (mirrors seed penalties in
# law_registry). Card aliases that strip these and claim a bare base-code
# name are rejected — AGENTS.md bug #1 at the alias layer.
_AMENDMENT_MARKERS = (
    "تعديل", "بيان", "قرار", "تصديق", "الغاء", "ذيل", "تفسير",
)
_BASE_CODE_NAMES = (
    "قانون العقوبات",
    "قانون الاحوال الشخصية",
    "قانون العمل",
    "القانون المدني",
    "قانون المرافعات المدنية",
    "قانون اصول المحاكمات الجزائية",
    "قانون الشركات",
    "قانون المواريث",
    "قانون التجارة",
    "قانون الخدمة المدنية",
)
_BASE_CODE_NORMS = tuple(normalize_ar(b) for b in _BASE_CODE_NAMES)

_STRIP_ALIAS_SUFFIX = re.compile(
    r"\s+(العراقي|لجمهورية العراق|جمهورية العراق)\s*$"
)
_STRIP_NUM_YEAR = re.compile(
    r"\s*رقم\s*\(?\s*[٠-٩0-9]+\s*\)?\s*(لسنة|\/)\s*[٠-٩0-9]+.*$"
)


def title_amendment_markers(title: str) -> list[str]:
    """Which amendment/secondary markers appear in the law title."""
    tn = normalize_ar(title)
    return [m for m in _AMENDMENT_MARKERS if normalize_ar(m) in tn]


def alias_claims_base_code(alias: str) -> bool:
    """True if alias is (or contains) a head-of-distribution base-code name."""
    an = normalize_ar(alias)
    if len(an) < _MIN_ALIAS_LEN:
        return False
    return any(nb in an or an in nb for nb in _BASE_CODE_NORMS)


def is_dangerous_card_alias(alias: str, title: str) -> bool:
    """
    Reject aliases that claim a bare base-code name while the title carries
    an amendment/bayan/qarar marker and the alias drops that marker.
    """
    markers = title_amendment_markers(title)
    if not markers:
        return False
    an = normalize_ar(alias)
    if not an:
        return False
    if any(normalize_ar(m) in an for m in markers):
        return False  # marker preserved — honest
    return alias_claims_base_code(alias)


def card_alias_routing_score(
    alias: str, title: str, question: str,
) -> float | None:
    """
    Seed-like score for a card/lexicon alias hit.

    Returns None when the alias must be ignored (dangerous false-friend).
    Otherwise alias length with تعديل/بيان/قرار demotions matching seeds.
    """
    if is_dangerous_card_alias(alias, title):
        return None
    an = normalize_ar(alias)
    if len(an) < _MIN_ALIAS_LEN:
        return None
    score = float(len(an))
    tn = normalize_ar(title)
    qn = normalize_ar(question)
    # Shared secondary-title demotions (عسكري / ذيل / تعديل / سريان …).
    try:
        from law_registry import title_route_penalty
        score -= title_route_penalty(title, question)
    except Exception:
        if "تعديل" in tn and "تعديل" not in qn:
            score -= 30
        elif any(x in tn for x in ("بيان تصحيح", "تعليمات", "قرار", "بيان")):
            if not any(x in qn for x in ("تعليمات", "قرار", "بيان")):
                score -= 14
    return score


def _alias_variants(alias: str) -> list[str]:
    """Expand one colloquial alias into shorter routing forms."""
    raw = (alias or "").strip()
    if not raw:
        return []
    out: list[str] = [raw]
    cur = raw
    stripped = _STRIP_ALIAS_SUFFIX.sub("", cur).strip()
    if stripped and stripped != cur:
        out.append(stripped)
        cur = stripped
    stripped = _STRIP_NUM_YEAR.sub("", cur).strip(" -–—")
    if stripped and stripped != cur:
        out.append(stripped)
        cur = stripped
    # Drop a trailing parenthetical sample marker
    stripped = re.sub(r"\s*\([^)]*عينة[^)]*\)\s*$", "", cur).strip()
    if stripped and stripped != cur:
        out.append(stripped)
    return out


def card_aliases_for_lexicon(card: dict) -> list[str]:
    """Normalized unique aliases worth indexing for routing."""
    seen: set[str] = set()
    out: list[str] = []
    title = (card.get("title") or "").strip()

    def _add(raw: str) -> None:
        for variant in _alias_variants(raw):
            n = normalize_ar(variant)
            if len(n) < _MIN_ALIAS_LEN or n in seen:
                continue
            # Drop base-code hijacks on amendment/bayan titles (SPEND_REVIEW §7).
            if is_dangerous_card_alias(variant, title):
                continue
            seen.add(n)
            out.append(variant.strip())

    for a in card.get("colloquial_aliases") or []:
        _add(str(a or ""))
    # Title-derived shorts so «قانون العقوبات» still routes when the model
    # only emitted «قانون العقوبات العراقي».
    if title:
        try:
            from law_registry import rule_aliases_for_title
            for a in rule_aliases_for_title(title):
                _add(a)
        except Exception:
            _add(title)
    return out


_LEXICON_CACHE: list[dict] | None = None
_LEXICON_CACHE_PATH: Path | None = None
_CARDS_BY_ID: dict[int, dict] | None = None
_CARDS_CACHE_PATH: Path | None = None


def truncate_law_text(full_text: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    text = (full_text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Prefer a clean break near an article marker
    last_nl = cut.rfind("\n")
    if last_nl > max_chars // 2:
        cut = cut[:last_nl]
    return cut + "\n…"


def build_card_user_prompt(rec: dict) -> str:
    title = (rec.get("lawTitle") or rec.get("title") or "").strip()
    year = str(rec.get("lawYear") or rec.get("year") or "")
    lid = int(rec.get("lawBookID") or rec.get("law_book_id") or 0)
    classification = (rec.get("classification") or rec.get("lawIndex") or "").strip()
    body = truncate_law_text(rec.get("full_text") or "")
    return (
        f"law_book_id: {lid}\n"
        f"title: {title}\n"
        f"year: {year}\n"
        f"classification: {classification}\n\n"
        f"full_text (truncated):\n{body}\n\n"
        "Return JSON with keys: scope_summary, subject_tags, "
        "colloquial_aliases, likely_questions, title_en (optional)."
    )


def _as_str_list(val: Any, *, max_items: int) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        parts = [p.strip() for p in re.split(r"[,،;؛|/]", val) if p.strip()]
        return parts[:max_items]
    if not isinstance(val, list):
        return []
    out: list[str] = []
    for item in val:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
        if len(out) >= max_items:
            break
    return out


def parse_card_payload(raw: str | dict, *, law_book_id: int, title: str = "") -> dict:
    """
    Parse model JSON (string or dict) into a normalized law-card row.
    Raises ValueError on unusable payloads.
    """
    if isinstance(raw, str):
        text = raw.strip()
        # Strip common markdown fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError(f"card payload must be str or dict, got {type(raw)}")

    if not isinstance(data, dict):
        raise ValueError("card JSON root must be an object")

    scope = str(data.get("scope_summary") or "").strip()
    if not scope:
        raise ValueError("scope_summary is required")

    tags = _as_str_list(data.get("subject_tags"), max_items=12)
    if not tags:
        raise ValueError("subject_tags must be a non-empty list")

    aliases = _as_str_list(data.get("colloquial_aliases"), max_items=16)
    questions = _as_str_list(data.get("likely_questions"), max_items=10)
    if not questions:
        raise ValueError("likely_questions must be a non-empty list")

    title_en = str(data.get("title_en") or "").strip() or None

    card = {
        "law_book_id": int(law_book_id),
        "title": (title or str(data.get("title") or "")).strip(),
        "scope_summary": scope,
        "subject_tags": tags,
        "colloquial_aliases": aliases,
        "likely_questions": questions,
    }
    if title_en:
        card["title_en"] = title_en
    validate_card(card)
    return card


def validate_card(card: dict) -> None:
    """Raise ValueError if card fails the local schema checks."""
    if not isinstance(card, dict):
        raise ValueError("card must be a dict")
    lid = card.get("law_book_id")
    if not isinstance(lid, int) or lid <= 0:
        raise ValueError(f"invalid law_book_id: {lid!r}")
    if not str(card.get("scope_summary") or "").strip():
        raise ValueError("missing scope_summary")
    tags = card.get("subject_tags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("subject_tags must be non-empty list")
    aliases = card.get("colloquial_aliases")
    if aliases is not None and not isinstance(aliases, list):
        raise ValueError("colloquial_aliases must be a list")
    questions = card.get("likely_questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("likely_questions must be non-empty list")
    # Soft length guards (keep routing aliases usable)
    for a in aliases or []:
        if not isinstance(a, str):
            raise ValueError("alias entries must be strings")
    for q in questions:
        if not isinstance(q, str) or not q.strip():
            raise ValueError("likely_questions entries must be non-empty strings")


def load_law_cards(path: Path = LAW_CARDS_FILE) -> dict[int, dict]:
    """law_book_id → card. Last line wins for duplicates."""
    global _CARDS_BY_ID, _CARDS_CACHE_PATH
    if _CARDS_BY_ID is not None and _CARDS_CACHE_PATH == path:
        return _CARDS_BY_ID
    out: dict[int, dict] = {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    lid = int(row.get("law_book_id") or 0)
                except (TypeError, ValueError):
                    continue
                if lid:
                    out[lid] = row
    _CARDS_BY_ID = out
    _CARDS_CACHE_PATH = path
    return out


def existing_card_ids(path: Path = LAW_CARDS_FILE) -> set[int]:
    return set(load_law_cards(path).keys())


def append_law_card(card: dict, path: Path = LAW_CARDS_FILE) -> None:
    validate_card(card)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(card, ensure_ascii=False) + "\n"
    with _APPEND_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        # Invalidate caches so subsequent loads see the new row
        global _CARDS_BY_ID, _CARDS_CACHE_PATH, _LEXICON_CACHE, _LEXICON_CACHE_PATH
        _CARDS_BY_ID = None
        _CARDS_CACHE_PATH = None
        _LEXICON_CACHE = None
        _LEXICON_CACHE_PATH = None


def cards_to_lexicon_rows(cards: Iterable[dict]) -> list[dict]:
    rows: list[dict] = []
    for card in cards:
        lid = int(card.get("law_book_id") or 0)
        title = (card.get("title") or "").strip()
        if not lid:
            continue
        for alias in card_aliases_for_lexicon(card):
            rows.append({
                "alias": alias,
                "alias_norm": normalize_ar(alias),
                "law_book_id": lid,
                "title": title,
                "source": "law_card",
            })
    return rows


def save_alias_lexicon(
    cards: Iterable[dict] | None = None,
    path: Path = ALIAS_LEXICON_FILE,
    cards_path: Path = LAW_CARDS_FILE,
) -> int:
    """
    Rewrite alias_lexicon.jsonl from cards (or from cards_path).
    Returns number of lexicon rows written.
    """
    if cards is None:
        cards = list(load_law_cards(cards_path).values())
    rows = cards_to_lexicon_rows(cards)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    global _LEXICON_CACHE, _LEXICON_CACHE_PATH
    _LEXICON_CACHE = None
    _LEXICON_CACHE_PATH = None
    return len(rows)


def load_alias_lexicon(path: Path = ALIAS_LEXICON_FILE) -> list[dict]:
    global _LEXICON_CACHE, _LEXICON_CACHE_PATH
    if _LEXICON_CACHE is not None and _LEXICON_CACHE_PATH == path:
        return _LEXICON_CACHE
    out: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    _LEXICON_CACHE = out
    _LEXICON_CACHE_PATH = path
    return out


def laws_matching_lexicon_aliases(
    question: str,
    lexicon: list[dict] | None = None,
) -> list[int]:
    """
    law_book_ids whose colloquial aliases appear in the question.
    Scored like seed aliases (تعديل/بيان demotion); dangerous base-code
    hijacks on amendment titles are dropped. Deterministic; empty if none.
    """
    rows = lexicon if lexicon is not None else load_alias_lexicon()
    if not rows:
        return []
    qn = normalize_ar(question)
    scored: dict[int, float] = {}
    for r in rows:
        alias = r.get("alias") or ""
        an = r.get("alias_norm") or normalize_ar(alias)
        # Substring match: require a meaningful phrase. Bare «قانون» /
        # «اهلي» flood ~hundreds of lids and stall/crash LanceDB pulls.
        if len(an) < 8 or an not in qn:
            continue
        lid = int(r.get("law_book_id") or 0)
        if not lid:
            continue
        title = r.get("title") or ""
        score = card_alias_routing_score(alias or an, title, question)
        if score is None or score <= 0:
            continue
        scored[lid] = max(scored.get(lid, 0.0), score)
    if not scored:
        return []
    best = max(scored.values())
    # Keep near-best only so a long named alias beats short generics.
    keep = {lid: s for lid, s in scored.items() if s >= best - 3}
    ranked = sorted(keep.items(), key=lambda x: (-x[1], -x[0]))
    return [lid for lid, _ in ranked[:12]]


def strongest_lexicon_alias_len(
    question: str,
    lexicon: list[dict] | None = None,
) -> int:
    """
    Longest *unpenalized* lexicon alias in the question.

    Heavily demoted amendment/bayan hits do not inflate NAMED_INSTRUMENT
    confidence (SPEND_REVIEW §5).
    """
    rows = lexicon if lexicon is not None else load_alias_lexicon()
    if not rows:
        return 0
    qn = normalize_ar(question)
    best = 0
    for r in rows:
        alias = r.get("alias") or ""
        an = r.get("alias_norm") or normalize_ar(alias)
        if len(an) < 8 or an not in qn:
            continue
        title = r.get("title") or ""
        score = card_alias_routing_score(alias or an, title, question)
        if score is None:
            continue
        # Only count near-full-length scores for confidence gating.
        if score >= len(an) - 1:
            best = max(best, len(an))
    return best


def merge_card_aliases_into_registry_row(row: dict, card: dict | None) -> dict:
    """Attach card colloquial aliases onto a registry row (copy)."""
    if not card:
        return row
    out = dict(row)
    aliases = list(out.get("aliases") or [])
    seen = {normalize_ar(a) for a in aliases}
    for a in card_aliases_for_lexicon(card):
        n = normalize_ar(a)
        if n not in seen:
            seen.add(n)
            aliases.append(a)
    out["aliases"] = aliases
    out["aliases_joined"] = " || ".join(aliases)
    # Keep route_text bounded
    title = out.get("title") or ""
    out["route_text"] = " | ".join([title] + aliases[:10])
    return out
