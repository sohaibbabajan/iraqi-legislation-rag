"""
Deterministic QueryPlan + quota-based fusion (ARCHITECTURE §5).

LLM-free, unit-testable. Replaces brittle ROUTE_DIST_HIGH-only merge order
with per-leg quotas and per-law diversity caps.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from common import (
    normalize_ar,
    parse_article_query,
    is_exact_lookup_question,
    is_overview_question,
)

# Arabic-Indic → ASCII for citation keys
_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

_CITATION_KEY = re.compile(
    r"رقم\s*\(?\s*([٠-٩0-9]+)\s*\)?\s*(?:لسنة|/)\s*([٠-٩0-9]{2,4})",
    re.IGNORECASE,
)

_COMPARATIVE = ("الفرق بين", "مقارنة", "قارن بين", "versus", " vs ")
_DRAFT_CUES = ("صيغة", "عريضة", "نموذج", "مسودة", "draft", "template")
_RRF_K = 60


class Shape(str, Enum):
    EXACT_ARTICLE = "EXACT_ARTICLE"           # S1
    ARTICLE_ANALYTICAL = "ARTICLE_ANALYTICAL" # S2
    CITATION_KEY = "CITATION_KEY"             # S3
    NAMED_INSTRUMENT = "NAMED_INSTRUMENT"     # S4
    DEFINITIONAL = "DEFINITIONAL"             # S5
    TOPICAL = "TOPICAL"                       # S6
    MULTI_INSTRUMENT = "MULTI_INSTRUMENT"     # S7
    PROCEDURAL_DRAFT = "PROCEDURAL_DRAFT"     # S8
    OUT_OF_SCOPE = "OUT_OF_SCOPE"             # S9


@dataclass(frozen=True)
class LegSpec:
    name: str
    weight: float = 1.0
    quota: int = 2


@dataclass(frozen=True)
class Budget:
    k: int = 6
    max_tokens: int = 700
    max_cost_usd: float = 0.01


@dataclass(frozen=True)
class QueryPlan:
    shape: Shape
    evidence: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    scope_doc_ids: list[int] = field(default_factory=list)
    article_label: str | None = None
    legs: list[LegSpec] = field(default_factory=list)
    expansions: list[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    max_per_law: int = 3


# Default leg quotas by shape (ARCHITECTURE §5.3, k=6 example).
_SHAPE_QUOTAS: dict[Shape, list[tuple[str, float, int]]] = {
    Shape.EXACT_ARTICLE: [
        ("article_exact", 1.5, 3),
    ],
    Shape.ARTICLE_ANALYTICAL: [
        ("article_exact", 1.4, 2),
        ("hybrid", 1.0, 3),
        ("law_scoped", 0.9, 1),
    ],
    Shape.CITATION_KEY: [
        ("law_scoped", 1.4, 5),
        ("article_exact", 1.0, 1),
    ],
    Shape.NAMED_INSTRUMENT: [
        ("law_scoped", 1.3, 4),
        ("hybrid", 0.9, 2),
        ("title_like", 1.0, 2),
    ],
    Shape.DEFINITIONAL: [
        ("defining_articles", 1.5, 3),
        ("law_scoped", 1.1, 2),
        ("hybrid", 0.8, 1),
    ],
    Shape.TOPICAL: [
        ("hybrid", 1.2, 4),
        ("card_route", 0.9, 1),
        ("title_like", 0.8, 1),
        ("law_scoped", 0.7, 1),  # fill only; quota keeps hybrid dominant
    ],
    Shape.MULTI_INSTRUMENT: [
        ("law_scoped", 1.2, 4),
        ("hybrid", 0.9, 2),
    ],
    Shape.PROCEDURAL_DRAFT: [
        ("hybrid", 1.0, 3),
        ("law_scoped", 1.0, 2),
        ("article_exact", 1.0, 1),
    ],
    Shape.OUT_OF_SCOPE: [
        ("hybrid", 1.0, 3),
    ],
}

_SHAPE_MAX_PER_LAW: dict[Shape, int] = {
    Shape.EXACT_ARTICLE: 99,
    Shape.ARTICLE_ANALYTICAL: 3,
    Shape.CITATION_KEY: 4,
    Shape.NAMED_INSTRUMENT: 4,
    Shape.DEFINITIONAL: 4,
    Shape.TOPICAL: 2,
    Shape.MULTI_INSTRUMENT: 2,
    Shape.PROCEDURAL_DRAFT: 3,
    Shape.OUT_OF_SCOPE: 2,
}


def ascii_digits(s: str) -> str:
    return (s or "").translate(_DIGIT_MAP)


def parse_citation_key(question: str) -> tuple[str, str] | None:
    """Return (law_number, year) ASCII if سؤال mentions رقم N لسنة Y."""
    m = _CITATION_KEY.search(question or "")
    if not m:
        return None
    num = ascii_digits(m.group(1)).lstrip("0") or "0"
    year = ascii_digits(m.group(2))
    if len(year) == 2:
        year = ("20" if int(year) < 50 else "19") + year
    return num, year


def _count_instrument_refs(question: str) -> int:
    try:
        from law_registry import extract_instrument_phrases
        phrases = extract_instrument_phrases(question or "")
    except Exception:
        phrases = []
    # Distinct normalized phrases
    seen: set[str] = set()
    for p in phrases:
        n = normalize_ar(p)
        if len(n) >= 6:
            seen.add(n)
    qn = normalize_ar(question or "")
    if any(normalize_ar(c) in qn for c in _COMPARATIVE):
        return max(len(seen), 2)
    return len(seen)


def route_vector_margin(vector_ranked: list[tuple[float, int]]) -> float:
    """
    Margin between top-1 and top-2 route cosine distances (scale-free).
    Larger margin → clearer named-law hit. Empty → 0.
    """
    if not vector_ranked:
        return 0.0
    if len(vector_ranked) == 1:
        # Single hit: treat very-close absolute distance as decisive margin.
        return 0.12 if vector_ranked[0][0] <= 0.28 else 0.02
    return max(0.0, float(vector_ranked[1][0]) - float(vector_ranked[0][0]))


def plan_query(
    question: str,
    *,
    k: int = 6,
    phrase_ids: list[int] | None = None,
    seed_ids: list[int] | None = None,
    vector_ranked: list[tuple[float, int]] | None = None,
    citation_law_ids: list[int] | None = None,
    card_alias_hit: bool = False,
    alias_len: int = 0,
) -> QueryPlan:
    """
    Classify question shape and emit leg quotas. Pure function of the question
    + cheap routing signals (no LLM, no LanceDB I/O inside).
    """
    q = question or ""
    phrase_ids = phrase_ids or []
    seed_ids = seed_ids or []
    vector_ranked = vector_ranked or []
    citation_law_ids = citation_law_ids or []

    art = parse_article_query(q)
    citation = parse_citation_key(q)
    n_instr = _count_instrument_refs(q)
    margin = route_vector_margin(vector_ranked)
    overview = is_overview_question(q)
    qn = normalize_ar(q)

    evidence: dict[str, float] = {
        "article_label": 1.0 if art else 0.0,
        "exact_lookup": 1.0 if (art and is_exact_lookup_question(q)) else 0.0,
        "citation_key_exact": 1.0 if citation else 0.0,
        "instrument_phrase": 1.0 if phrase_ids else 0.0,
        "alias_lexicon_len": float(alias_len or 0),
        "card_alias": 1.0 if card_alias_hit else 0.0,
        "route_vector_margin": margin,
        "overview_cue": 1.0 if overview else 0.0,
        "n_instruments": float(n_instr),
    }

    # --- shape selection (priority order) --------------------------------
    draft = any(normalize_ar(c) in qn for c in _DRAFT_CUES)
    if draft and not art:
        shape = Shape.PROCEDURAL_DRAFT
    elif art and is_exact_lookup_question(q):
        shape = Shape.EXACT_ARTICLE
    elif art:
        shape = Shape.ARTICLE_ANALYTICAL
    elif citation and citation_law_ids:
        shape = Shape.CITATION_KEY
    elif citation and not (phrase_ids or seed_ids or card_alias_hit):
        # Key parsed but no registry hit yet — still citation-shaped;
        # retrieve will soft-scope if ids arrive later.
        shape = Shape.CITATION_KEY
    elif n_instr >= 2 or any(normalize_ar(c) in qn for c in _COMPARATIVE):
        shape = Shape.MULTI_INSTRUMENT
    elif overview and (phrase_ids or seed_ids or card_alias_hit or n_instr >= 1):
        shape = Shape.DEFINITIONAL
    elif phrase_ids or seed_ids or card_alias_hit or (
        alias_len >= 10
    ) or (
        margin >= 0.08 and vector_ranked and vector_ranked[0][0] <= 0.40
    ):
        shape = Shape.NAMED_INSTRUMENT
    else:
        # Content-token topical ask (default). Keep stopword strip local so
        # this module never imports ask.py (ask imports query_plan).
        _stops = {
            "ما", "هي", "هو", "في", "من", "على", "هل", "و", "او", "ان",
            "قانون", "نظام", "المادة", "what", "is", "the", "a", "an",
        }
        parts = re.split(r"[\s،,.؟?!:؛/\\|()\[\]{}\"']+", qn)
        toks = [
            t for t in parts
            if len(t) >= 3 and t not in _stops and not t.isdigit()
        ]
        if not toks and not art and not phrase_ids:
            shape = Shape.OUT_OF_SCOPE
        else:
            shape = Shape.TOPICAL

    # Confidence: monotone in decisive evidence
    conf = 0.15
    conf += 0.35 * evidence["exact_lookup"]
    conf += 0.25 * evidence["citation_key_exact"]
    conf += 0.20 * evidence["instrument_phrase"]
    conf += 0.15 * evidence["card_alias"]
    conf += min(0.20, evidence["alias_lexicon_len"] / 50.0)
    conf += min(0.20, evidence["route_vector_margin"] * 2.0)
    conf = min(1.0, conf)

    scope: list[int] = []
    if shape == Shape.CITATION_KEY and citation_law_ids:
        scope = list(dict.fromkeys(int(x) for x in citation_law_ids))
    elif shape in (
        Shape.NAMED_INSTRUMENT,
        Shape.DEFINITIONAL,
        Shape.MULTI_INSTRUMENT,
        Shape.EXACT_ARTICLE,
        Shape.ARTICLE_ANALYTICAL,
    ):
        # Soft scope preference (not a hard filter unless citation).
        # EXACT_ARTICLE / ARTICLE_ANALYTICAL need this so art=N defines are
        # pulled from قانون العمل (etc.) instead of any statute with that number.
        for lid in list(phrase_ids) + list(seed_ids):
            if int(lid) not in scope:
                scope.append(int(lid))

    legs = [
        LegSpec(name=n, weight=w, quota=q_)
        for n, w, q_ in _SHAPE_QUOTAS[shape]
    ]
    # Scale quotas if caller asks for k != 6
    if k != 6 and k > 0:
        scale = k / 6.0
        legs = [
            LegSpec(
                name=leg.name,
                weight=leg.weight,
                quota=max(1, int(round(leg.quota * scale))),
            )
            for leg in legs
        ]

    return QueryPlan(
        shape=shape,
        evidence=evidence,
        confidence=conf,
        scope_doc_ids=scope,
        article_label=art,
        legs=legs,
        expansions=[],
        budget=Budget(k=k),
        max_per_law=_SHAPE_MAX_PER_LAW.get(shape, 3),
    )


def _dedupe_key(row: dict) -> str:
    """Collapse article + chunk hits for the same article to one context slot."""
    lid = row.get("law_book_id")
    label = row.get("article_label") or ""
    if not label:
        # Infer first article from article_nums padded form
        nums = (row.get("article_nums") or "").strip(",")
        if nums:
            label = nums.split(",")[0]
    if lid is not None and label:
        return f"{int(lid)}:{label}"
    return str(row.get("chunk_id") or id(row))


def fuse_legs(
    leg_hits: dict[str, list[dict]],
    plan: QueryPlan,
    *,
    k: int | None = None,
    guaranteed_law_ids: Iterable[int] | None = None,
) -> list[dict]:
    """
    Weighted RRF + per-leg quotas + per-law diversity.

    `leg_hits` maps leg name → ranked rows (best first). Unknown legs ignored.
    """
    limit = k if k is not None else plan.budget.k
    weights = {leg.name: leg.weight for leg in plan.legs}
    quotas = {leg.name: leg.quota for leg in plan.legs}
    max_per_law = plan.max_per_law
    allowed = set(weights.keys())

    # Score every (leg, row) with weighted RRF — only legs on the plan
    scored: list[tuple[float, str, int, dict]] = []
    for leg_name, rows in leg_hits.items():
        if leg_name not in allowed:
            continue
        w = weights[leg_name]
        for rank, row in enumerate(rows):
            score = w / (_RRF_K + rank + 1)
            scored.append((score, leg_name, rank, row))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    out: list[dict] = []
    seen: set[str] = set()
    used_leg: dict[str, int] = {name: 0 for name in quotas}
    used_law: dict[int, int] = {}

    def _try_add(leg_name: str, row: dict) -> bool:
        key = _dedupe_key(row)
        if key in seen:
            return False
        qlim = quotas.get(leg_name)
        if qlim is not None and used_leg.get(leg_name, 0) >= qlim:
            return False
        try:
            lid = int(row.get("law_book_id") or 0)
        except (TypeError, ValueError):
            lid = 0
        if lid and used_law.get(lid, 0) >= max_per_law:
            return False
        seen.add(key)
        if qlim is not None:
            used_leg[leg_name] = used_leg.get(leg_name, 0) + 1
        if lid:
            used_law[lid] = used_law.get(lid, 0) + 1
        # Annotate provenance for debugging / eval
        tagged = dict(row)
        tagged.setdefault("_leg", leg_name)
        out.append(tagged)
        return True

    # MULTI_INSTRUMENT: guarantee ≥1 slot per named instrument first
    if plan.shape == Shape.MULTI_INSTRUMENT and guaranteed_law_ids:
        scoped = leg_hits.get("law_scoped") or []
        by_law: dict[int, list[dict]] = {}
        for r in scoped:
            try:
                lid = int(r.get("law_book_id") or 0)
            except (TypeError, ValueError):
                continue
            by_law.setdefault(lid, []).append(r)
        for lid in guaranteed_law_ids:
            if len(out) >= limit:
                break
            for r in by_law.get(int(lid), []):
                if _try_add("law_scoped", r):
                    break

    for _score, leg_name, _rank, row in scored:
        if len(out) >= limit:
            break
        _try_add(leg_name, row)

    return out[:limit]


def quotas_dict(plan: QueryPlan) -> dict[str, int]:
    return {leg.name: leg.quota for leg in plan.legs}
