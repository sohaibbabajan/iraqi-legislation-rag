"""
law_registry.py — offline law routing index for cheap, accurate retrieval.

One row per lawBookID. Upfront: embed route_text (title + aliases) with
bge-m3 into LanceDB table `law_routes`. At query time the *same* question
embedding is compared to those ~38k title vectors (no extra API call) to
pick candidate laws, then chunks are pulled from those laws only.

Seed aliases fix colloquial names (e.g. «قانون التعليم الاهلي» → العالي الأهلي
*and* نظام التعليم الأهلي).

Optional LLM law cards (`cache/law_cards.jsonl` + `cache/alias_lexicon.jsonl`)
extend colloquial matching when present. Cards are routing/UI metadata only —
never inject them into the answer LLM context.
"""

from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

from common import (
    ROOT, CACHE_DIR, DB_DIR, SOURCES_DIR, normalize_ar, default_corpus_path,
    LAW_CARDS_FILE,
)

REGISTRY_FILE = CACHE_DIR / "law_registry.jsonl"
ROUTES_TABLE = "law_routes"

# Optional P1 LLM law cards (another agent builds these). Loaded if present.
_LAW_CARDS_CACHE: list[dict] | None = None
_LAW_CARDS_CACHE_PATH: Path | None = None

# Colloquial / short names → title substrings that must be boosted.
# normalize_ar applied at match time.
SEED_ALIAS_RULES: list[dict[str, Any]] = [
    {
        "aliases": [
            "التعليم الاهلي",
            "التعليم الأهلي",
            "قانون التعليم الاهلي",
            "قانون التعليم الأهلي",
            "تعليم اهلي",
        ],
        "title_any": [
            "نظام التعليم الاهلي",
            "قانون التعليم العالي الاهلي",
            "الجامعات والكليات الاهلية",
        ],
    },
    {
        "aliases": [
            "التعليم العالي الاهلي",
            "جامعة اهلية",
            "جامعات اهلية",
            "كلية اهلية",
            "كليات اهلية",
        ],
        "title_any": [
            "قانون التعليم العالي الاهلي",
            "الجامعات والكليات الاهلية",
        ],
    },
    {
        "aliases": ["قانون العقوبات", "العقوبات"],
        "title_any": ["قانون العقوبات رقم ١١١", "قانون العقوبات رقم 111"],
    },
    {
        "aliases": ["الاحوال الشخصية", "الأحوال الشخصية"],
        "title_any": ["الاحوال الشخصية", "الأحوال الشخصية"],
    },
    {
        "aliases": ["قانون العمل"],
        "title_any": ["قانون العمل"],
    },
]


def _strip_number_year(title: str) -> str:
    """Remove رقم … لسنة … clutter for shorter aliases."""
    t = title
    t = re.sub(
        r"رقم\s*\(?\s*[٠-٩0-9]+\s*\)?\s*(لسنة|\/)\s*[٠-٩0-9]+",
        " ",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip(" -–—")
    return t


def rule_aliases_for_title(title: str) -> list[str]:
    """Deterministic aliases derived from a law title (no LLM)."""
    out: list[str] = []
    raw = (title or "").strip()
    if not raw:
        return out
    out.append(raw)
    short = _strip_number_year(raw)
    if short and short != raw:
        out.append(short)
    n = normalize_ar(short or raw)
    out.append(n)
    for pref in ("قانون", "نظام", "تعليمات", "قرار", "بيان"):
        pn = normalize_ar(pref)
        if n.startswith(pn + " "):
            rest = n[len(pn) + 1:].strip()
            if len(rest) >= 4:
                out.append(rest)
                out.append(f"{pn} {rest}")
            break
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for a in out:
        a = a.strip()
        if not a or a in seen:
            continue
        seen.add(a)
        uniq.append(a)
    return uniq


def iter_law_records(path: Path | None = None):
    path = path or default_corpus_path()
    if not path.exists():
        raise FileNotFoundError(f"Corpus not found: {path}")
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def build_registry_rows(
    *,
    sari_only: bool = True,
    require_text: bool = True,
    source: Path | None = None,
) -> list[dict]:
    """One dict per lawBookID for routing."""
    by_id: dict[int, dict] = {}
    for rec in iter_law_records(source):
        if sari_only and (rec.get("status_label") or "").strip() != "ساري":
            continue
        if require_text and not (rec.get("full_text") or "").strip():
            continue
        lid = int(rec.get("lawBookID") or 0)
        if not lid:
            continue
        title = (rec.get("lawTitle") or "").strip()
        aliases = rule_aliases_for_title(title)
        # Attach seed aliases that point at this title
        tn = normalize_ar(title)
        for rule in SEED_ALIAS_RULES:
            if any(normalize_ar(t) in tn for t in rule["title_any"]):
                for a in rule["aliases"]:
                    if a not in aliases:
                        aliases.append(a)
        route_text = " | ".join([title] + aliases[:8])
        by_id[lid] = {
            "law_book_id": lid,
            "title": title,
            "year": str(rec.get("lawYear") or ""),
            "status_label": rec.get("status_label") or "",
            "law_flag": rec.get("lawFlag") or "",
            "aliases": aliases,
            "aliases_joined": " || ".join(aliases),
            "route_text": route_text,
            "source_url": rec.get("source_url") or "",
        }
    rows = sorted(by_id.values(), key=lambda r: r["law_book_id"])
    # Merge optional card colloquial aliases into route_text when present.
    try:
        from law_cards import load_law_cards, merge_card_aliases_into_registry_row
        cards = load_law_cards()
        if cards:
            rows = [
                merge_card_aliases_into_registry_row(r, cards.get(int(r["law_book_id"])))
                for r in rows
            ]
    except Exception:
        pass
    return rows


_REGISTRY_CACHE: list[dict] | None = None
_REGISTRY_CACHE_PATH: Path | None = None


def save_registry(rows: list[dict], path: Path = REGISTRY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # Invalidate in-process cache after rebuild
    global _REGISTRY_CACHE, _REGISTRY_CACHE_PATH
    _REGISTRY_CACHE = None
    _REGISTRY_CACHE_PATH = None


def load_registry(path: Path = REGISTRY_FILE) -> list[dict]:
    """Load registry once per process — ~38k rows, reused every query."""
    global _REGISTRY_CACHE, _REGISTRY_CACHE_PATH
    if _REGISTRY_CACHE is not None and _REGISTRY_CACHE_PATH == path:
        return _REGISTRY_CACHE
    if not path.exists():
        _REGISTRY_CACHE = []
        _REGISTRY_CACHE_PATH = path
        return _REGISTRY_CACHE
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    _REGISTRY_CACHE = out
    _REGISTRY_CACHE_PATH = path
    return out


def laws_matching_seed_aliases(question: str, rows: list[dict]) -> list[int]:
    """
    law_book_ids for seed rules whose aliases appear in the question.

    Ranked: prefer titles that match the fired rule's title_any, prefer
    قانون when the user said قانون, prefer newer years, demote تعديلات/
    بيانات/تعليمات unless that is what they asked for.

    Optional law-card / alias-lexicon hits append after hand-written seeds
    when present (routing only — never answer context).
    """
    qn = normalize_ar(question)
    fired: list[tuple[dict, int]] = []
    for rule in SEED_ALIAS_RULES:
        lens = [len(normalize_ar(a)) for a in rule["aliases"]
                if len(normalize_ar(a)) >= 5 and normalize_ar(a) in qn]
        if lens:
            fired.append((rule, max(lens)))

    wants_qanun = "قانون" in qn
    wants_nizam = "نظام" in qn
    wants_uni = any(x in qn for x in ("جامعة", "جامعات", "كلية", "كليات", "عالي"))

    scored: list[tuple[float, int]] = []
    if fired:
        for r in rows:
            tn = normalize_ar(r.get("title") or "")
            if not tn:
                continue
            best = 0.0
            for rule, alias_len in fired:
                if not any(normalize_ar(t) in tn for t in rule["title_any"]):
                    continue
                score = float(alias_len)
                if wants_qanun and tn.startswith(normalize_ar("قانون")):
                    score += 25
                if wants_nizam and normalize_ar("نظام") in tn:
                    score += 25
                if wants_uni and any(x in tn for x in ("عالي", "جامعة", "جامعات", "كلية")):
                    score += 18
                try:
                    year = int(str(r.get("year") or "0")[:4])
                except ValueError:
                    year = 0
                score += year / 200.0  # mild recency
                if "تعديل" in tn and "تعديل" not in qn:
                    score -= 30
                elif any(x in tn for x in ("بيان تصحيح", "تعليمات", "قرار")):
                    if not any(x in qn for x in ("تعليمات", "قرار", "بيان")):
                        score -= 14
                best = max(best, score)
            if best > 0:
                scored.append((best, int(r["law_book_id"])))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    out: list[int] = []
    seen: set[int] = set()
    for _, lid in scored:
        if lid not in seen:
            seen.add(lid)
            out.append(lid)

    # Card / lexicon aliases fill gaps after seeds (deterministic fallback).
    card_ids, _ = laws_matching_card_aliases(question)
    for lid in card_ids:
        if lid not in seen:
            seen.add(lid)
            out.append(lid)
    return out


def extract_instrument_phrases(question: str) -> list[str]:
    """Pull «قانون/نظام …» phrases from anywhere in the question."""
    q = normalize_ar(question)
    q = q.replace("؟", " ").replace("?", " ")
    found: list[str] = []
    for m in re.finditer(
        r"(قانون|نظام|تعليمات)\s+[^،,.]{3,50}",
        q,
    ):
        phrase = m.group(0).strip()
        # cut trailing filler
        phrase = re.split(r"\s+(حسب|في|من|على|و)\s+", phrase)[0].strip()
        if len(phrase) >= 6:
            found.append(phrase)
    # after حسب
    for m in re.finditer(r"حسب\s+((?:قانون|نظام|تعليمات)\s+[^،,.]{3,50})", q):
        found.append(m.group(1).strip())
    # dedupe
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def strongest_seed_alias_len(question: str) -> int:
    """Longest SEED / law-card alias that appears in the question (normalized)."""
    qn = normalize_ar(question)
    best = 0
    for rule in SEED_ALIAS_RULES:
        for a in rule["aliases"]:
            an = normalize_ar(a)
            if len(an) >= 5 and an in qn:
                best = max(best, len(an))
    # Integration hook: colloquial aliases from cache/law_cards.jsonl when present.
    _hit, card_len = laws_matching_card_aliases(question)
    if card_len > best:
        best = card_len
    return best


def load_law_cards(path: Path | None = None) -> list[dict]:
    """
    Load optional LLM law cards (P1). Returns [] if missing — routing still
    works via SEED_ALIAS_RULES + registry titles.
    Expected fields (flexible): law_book_id, aliases / colloquial_aliases /
    colloquial_names (list[str]).
    """
    global _LAW_CARDS_CACHE, _LAW_CARDS_CACHE_PATH
    p = Path(path) if path else LAW_CARDS_FILE
    if _LAW_CARDS_CACHE is not None and _LAW_CARDS_CACHE_PATH == p:
        return _LAW_CARDS_CACHE
    if not p.exists():
        _LAW_CARDS_CACHE = []
        _LAW_CARDS_CACHE_PATH = p
        return _LAW_CARDS_CACHE
    out: list[dict] = []
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    _LAW_CARDS_CACHE = out
    _LAW_CARDS_CACHE_PATH = p
    return out


def _card_alias_list(card: dict) -> list[str]:
    aliases: list[str] = []
    for key in ("aliases", "colloquial_aliases", "colloquial_names", "aka"):
        val = card.get(key)
        if isinstance(val, list):
            aliases.extend(str(a) for a in val if a)
        elif isinstance(val, str) and val.strip():
            aliases.append(val.strip())
    return aliases


def laws_matching_card_aliases(question: str) -> tuple[list[int], int]:
    """
    Match question against law-card colloquial aliases when
    cache/law_cards.jsonl or cache/alias_lexicon.jsonl exists.

    Returns (law_book_ids, strongest_alias_len). Routing/UI only — never
    inject cards into the answer LLM context.
    """
    qn = normalize_ar(question)
    scored: list[tuple[int, int]] = []  # (alias_len, law_book_id)
    best_len = 0

    # Prefer compact lexicon sidecar when present (built by build_law_cards.py).
    try:
        from law_cards import load_alias_lexicon, laws_matching_lexicon_aliases
        from law_cards import strongest_lexicon_alias_len
        lex = load_alias_lexicon()
        if lex:
            best_len = max(best_len, strongest_lexicon_alias_len(question, lex))
            for lid in laws_matching_lexicon_aliases(question, lex):
                scored.append((best_len, lid))
    except Exception:
        pass

    for card in load_law_cards():
        try:
            lid = int(card.get("law_book_id"))
        except (TypeError, ValueError):
            continue
        for a in _card_alias_list(card):
            an = normalize_ar(a)
            if len(an) < 5 or an not in qn:
                continue
            best_len = max(best_len, len(an))
            scored.append((len(an), lid))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    ids: list[int] = []
    seen: set[int] = set()
    for _, lid in scored:
        if lid not in seen:
            seen.add(lid)
            ids.append(lid)
    return ids, best_len


def laws_matching_instrument_phrases(
    question: str, rows: list[dict],
) -> list[int]:
    """
    Rank law_book_ids whose titles (or short aliases) contain a named
    قانون/نظام/تعليمات phrase from the question.

    General form of the اهلي fix: any named instrument in the ~38k registry,
    not only hand-written SEED_ALIAS_RULES.
    """
    phrases = [normalize_ar(p) for p in extract_instrument_phrases(question)]
    phrases = [p for p in phrases if len(p) >= 6]
    if not phrases or not rows:
        return []

    scored: list[tuple[float, int]] = []
    for r in rows:
        tn = normalize_ar(r.get("title") or "")
        if not tn:
            continue
        # Also allow short rule-derived aliases (without رقم/سنة clutter)
        alias_norms = [normalize_ar(a) for a in (r.get("aliases") or [])[:6]]
        best = 0.0
        for phrase in phrases:
            hit = False
            match_len = 0
            if phrase in tn:
                hit = True
                match_len = len(phrase)
            else:
                # phrase may be longer than title short-form — try title in phrase
                # or any alias contained in / containing the phrase
                for an in alias_norms:
                    if len(an) < 6:
                        continue
                    if an in phrase or phrase in an:
                        hit = True
                        match_len = max(match_len, min(len(an), len(phrase)))
                        break
            if not hit:
                continue
            score = float(match_len)
            # Prefer same instrument kind as the phrase prefix
            if phrase.startswith(normalize_ar("قانون")) and tn.startswith(
                normalize_ar("قانون")
            ):
                score += 20
            if phrase.startswith(normalize_ar("نظام")) and normalize_ar("نظام") in tn:
                score += 20
            if phrase.startswith(normalize_ar("تعليمات")) and (
                normalize_ar("تعليمات") in tn
            ):
                score += 20
            try:
                year = int(str(r.get("year") or "0")[:4])
            except ValueError:
                year = 0
            score += year / 200.0
            if "تعديل" in tn and "تعديل" not in phrase:
                score -= 30
            elif any(x in tn for x in ("بيان تصحيح", "تعليمات", "قرار")):
                if not any(x in phrase for x in ("تعليمات", "قرار", "بيان")):
                    score -= 14
            best = max(best, score)
        if best > 0:
            scored.append((best, int(r["law_book_id"])))

    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [lid for _, lid in scored]
