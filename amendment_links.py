"""
Offline amendment linkage: معدل ← amended_by[تعديل…] (no LLM).

Builds cache/amendment_links.jsonl from the laws JSONL. Query-time helpers
load the sidecar and surface amended_by titles next to معدل hits; optional
same-article chunk pull from amenders is LanceDB-only (no extra embed/LLM).

Matching (per تعديل), in order:
  1. title-containment of a معدل core name (stripped رقم/لسنة + parens)
  2. رقم N لسنة Y → unique معدل (never naive when ambiguous)
  3. disambiguate multi num/year hits via classification / lawIndex / title type
  4. optional lawNotes repeal → replaced_by

Do NOT link on bare year or on an amendment's own رقم/لسنة identity.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from common import (
    CACHE_DIR,
    ROOT,
    iter_records,
    normalize_ar,
    resolve_amendment_links_file,
)

BUILDER_VERSION = "1.1.0"
AMENDMENT_LINKS_FILE = resolve_amendment_links_file()

_DIGIT_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Arabic letters + hamza forms used for token-boundary checks after normalize_ar.
_AR_WORD_CHAR = re.compile(r"[\u0621-\u064A\u0671\u067E\u0686\u0698\u06A4\u06AF]")

_CITATION = re.compile(
    r"رقم\s*\(?\s*([٠-٩0-9]+)\s*\)?\s*(?:لسنة|/)\s*([٠-٩0-9]{2,4})",
    re.IGNORECASE,
)
_STRIP_NUM_YEAR = re.compile(
    r"\s*رقم\s*\(?\s*[٠-٩0-9]+\s*\)?\s*(?:لسنة|/)\s*[٠-٩0-9]+",
    re.IGNORECASE,
)
_STRIP_PARENS = re.compile(r"\([^)]*\)")
_YEAR_ONLY = re.compile(r"(?:لسنة|/)\s*[٠-٩0-9]{2,4}\s*$")

_GENERIC_CORES = frozenset({
    "قانون", "نظام", "تعليمات", "بيان", "قرار", "مرسوم", "ارادة",
    "قانون تعديل", "نظام تعديل", "تعليمات تعديل",
})

_REPEAL_CUES = (
    "الغي بموجب", "ألغي بموجب", "أُلغي بموجب", "ملغى بموجب",
    "يحل محل", "يستعاض عنه", "يستعاض عن",
)

# Prefer these majors when printing build stats / smoke checks.
MAJOR_BASES = (
    ("111", "1969", "عقوبات"),
    ("188", "1959", "احوال شخصية"),
    ("24", "1960", "خدمة مدنية"),
)


def ascii_digits(s: str) -> str:
    return (s or "").translate(_DIGIT_MAP)


def ascii_num_year(num: str, year: str) -> tuple[str, str]:
    n = ascii_digits(num).lstrip("0") or "0"
    y = ascii_digits(year)
    if len(y) == 2:
        y = ("20" if int(y) < 50 else "19") + y
    return n, y


def parse_citations(text: str) -> list[tuple[str, str]]:
    """All رقم N لسنة Y pairs in text as ASCII (num, year), order preserved."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in _CITATION.finditer(text or ""):
        pair = ascii_num_year(m.group(1), m.group(2))
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def core_title(title: str) -> str:
    """
    Matching key: strip رقم/لسنة, parentheticals, trailing year-only crumbs,
    then normalize_ar. Empty if too generic.
    """
    t = title or ""
    t = _STRIP_NUM_YEAR.sub("", t)
    t = _STRIP_PARENS.sub("", t)
    t = _YEAR_ONLY.sub("", t)
    t = re.sub(r"\s*[–—-]\s*$", "", t)
    t = normalize_ar(t)
    t = re.sub(r"\s+", " ", t).strip(" -–—،,")
    if len(t) < 8 or t in _GENERIC_CORES:
        return ""
    return t


def core_contained_in(core: str, hay: str) -> bool:
    """
    Token-boundary-safe containment: ``core`` must appear in ``hay`` without
    an Arabic letter immediately before/after (rejects العمل ⊂ العملة).

    Both sides should already be normalize_ar'd (or will be here).
    """
    c = normalize_ar(core or "")
    h = normalize_ar(hay or "")
    if not c or len(c) < 8 or c not in h:
        return False
    start = 0
    while True:
        i = h.find(c, start)
        if i < 0:
            return False
        before_ok = i == 0 or not _AR_WORD_CHAR.match(h[i - 1])
        after_i = i + len(c)
        after_ok = after_i >= len(h) or not _AR_WORD_CHAR.match(h[after_i])
        if before_ok and after_ok:
            return True
        start = i + 1


def _instrument_kind(text: str) -> str:
    n = normalize_ar(text or "")
    for kind in ("تعليمات", "نظام", "مرسوم", "بيان", "قرار", "قانون"):
        if kind in n:
            return kind
    return ""


def _class_blob(rec: dict) -> str:
    return normalize_ar(
        " ".join(
            str(rec.get(k) or "")
            for k in ("classification", "lawIndex", "category", "lawTitle")
        )
    )


def _own_identity(rec: dict) -> tuple[str, str] | None:
    code = ascii_digits(str(rec.get("lawCode") or "")).lstrip("0")
    year = ascii_digits(str(rec.get("lawYear") or ""))[:4]
    if code and year and len(year) == 4:
        return code, year
    # Fall back to first citation in the amendment's own title as identity
    # only when lawCode is empty — still used to *exclude* self-cites.
    title_cites = parse_citations(rec.get("lawTitle") or "")
    if title_cites and year:
        # If title has two cites, first is often the base; last may be own.
        # Prefer lawYear-matching cite as own when present.
        for n, y in title_cites:
            if y == year:
                return n, y
    return (code, year) if code and year else None


@dataclass
class AmenderRef:
    law_book_id: int
    title: str
    year: str
    method: str
    confidence: str

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class AmendmentLinkRow:
    base_law_book_id: int
    base_title: str
    base_num: str
    base_year: str
    classification: str = ""
    amended_by: list[AmenderRef] = field(default_factory=list)
    replaced_by: list[AmenderRef] = field(default_factory=list)
    builder_version: str = BUILDER_VERSION

    def to_json(self) -> dict:
        return {
            "base_law_book_id": self.base_law_book_id,
            "base_title": self.base_title,
            "base_num": self.base_num,
            "base_year": self.base_year,
            "classification": self.classification,
            "amended_by": [a.to_json() for a in self.amended_by],
            "replaced_by": [a.to_json() for a in self.replaced_by],
            "builder_version": self.builder_version,
        }


@dataclass
class _LawBrief:
    law_book_id: int
    title: str
    year: str
    flag: str
    num: str
    classification: str
    law_index: str
    category: str
    notes: str
    core: str
    kind: str
    class_blob: str
    raw: dict


def _brief(rec: dict) -> _LawBrief | None:
    try:
        lid = int(rec.get("lawBookID") or 0)
    except (TypeError, ValueError):
        return None
    if not lid:
        return None
    title = rec.get("lawTitle") or ""
    year = ascii_digits(str(rec.get("lawYear") or ""))[:4]
    num = ascii_digits(str(rec.get("lawCode") or "")).lstrip("0")
    if not num:
        cites = parse_citations(title)
        if cites:
            # Prefer cite whose year matches lawYear
            for n, y in cites:
                if y == year:
                    num = n
                    break
            if not num:
                num = cites[0][0]
    return _LawBrief(
        law_book_id=lid,
        title=title,
        year=year,
        flag=(rec.get("lawFlag") or "").strip(),
        num=num or "",
        classification=str(rec.get("classification") or ""),
        law_index=str(rec.get("lawIndex") or ""),
        category=str(rec.get("category") or ""),
        notes=str(rec.get("lawNotes") or ""),
        core=core_title(title),
        kind=_instrument_kind(title),
        class_blob=_class_blob(rec),
        raw=rec,
    )


def _disambiguate(
    candidates: list[_LawBrief],
    amender: _LawBrief,
) -> _LawBrief | None:
    """Pick one معدل among false-friend num/year collisions."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    # 1) Title containment of candidate core in amendment title/notes
    hay = normalize_ar(f"{amender.title} {amender.notes}")
    contained = [
        c for c in candidates
        if c.core and len(c.core) >= 8 and core_contained_in(c.core, hay)
    ]
    if len(contained) == 1:
        return contained[0]
    if contained:
        contained.sort(key=lambda c: len(c.core), reverse=True)
        if len(contained) == 1 or len(contained[0].core) > len(contained[1].core):
            return contained[0]
        candidates = contained

    # 2) Same instrument kind (قانون vs نظام vs …)
    if amender.kind:
        kind_hits = [c for c in candidates if c.kind == amender.kind]
        if len(kind_hits) == 1:
            return kind_hits[0]
        if kind_hits:
            candidates = kind_hits

    # 3) Classification / lawIndex token overlap
    am_blob = amender.class_blob
    if am_blob:
        scored: list[tuple[int, _LawBrief]] = []
        for c in candidates:
            tokens = [t for t in re.split(r"\s+", c.class_blob) if len(t) >= 3]
            score = sum(1 for t in tokens if t in am_blob)
            # Boost exact classification equality
            if c.classification and normalize_ar(c.classification) in am_blob:
                score += 3
            if c.law_index and normalize_ar(c.law_index) in am_blob:
                score += 2
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] > 0:
            if len(scored) == 1 or scored[0][0] > scored[1][0]:
                return scored[0][1]

    return None  # refuse ambiguous bare num/year


def match_amender_to_base(
    amender: _LawBrief,
    muadal_by_ny: dict[tuple[str, str], list[_LawBrief]],
    muadal_list: list[_LawBrief],
) -> tuple[_LawBrief | None, str, str]:
    """
    Return (base, method, confidence) or (None, '', '').

    Never returns a link on ambiguous bare num/year without disambiguation.
    """
    own = _own_identity(amender.raw)
    hay_title = amender.title or ""
    hay_notes = amender.notes or ""

    # --- num/year citations (title preferred, then notes) ----------------
    title_cites = [
        p for p in parse_citations(hay_title)
        if not (own and p == own)
    ]
    notes_cites = [
        p for p in parse_citations(hay_notes)
        if not (own and p == own) and p not in title_cites
    ]

    for cites, src in ((title_cites, "title"), (notes_cites, "notes")):
        for pair in cites:
            cands = list(muadal_by_ny.get(pair) or [])
            # Drop self
            cands = [c for c in cands if c.law_book_id != amender.law_book_id]
            if not cands:
                continue
            if len(cands) == 1:
                return cands[0], "num_year", "high"
            picked = _disambiguate(cands, amender)
            if picked:
                return picked, "classification", "medium"
            # Ambiguous false friends — skip this pair, try others / containment

    # --- title containment (longest unique core, boundary-safe) ----------
    hay = normalize_ar(f"{hay_title} {hay_notes}")
    hits: list[_LawBrief] = []
    for m in muadal_list:
        if m.law_book_id == amender.law_book_id:
            continue
        if not m.core or len(m.core) < 8:
            continue
        if core_contained_in(m.core, hay):
            hits.append(m)
    if hits:
        hits.sort(key=lambda c: len(c.core), reverse=True)
        best_len = len(hits[0].core)
        top = [h for h in hits if len(h.core) == best_len]
        if len(top) == 1:
            return top[0], "title_contain", "high" if best_len >= 12 else "medium"
        picked = _disambiguate(top, amender)
        if picked:
            return picked, "title_contain", "medium"

    return None, "", ""


def _repeal_targets(
    rec_brief: _LawBrief,
    all_by_ny: dict[tuple[str, str], list[_LawBrief]],
    all_by_id: dict[int, _LawBrief],
) -> list[tuple[_LawBrief, str, str]]:
    """
    Optional: lawNotes that explicitly replace / repeal another instrument.
    Only unique رقم/لسنة matches (no low-confidence disambiguation).
    """
    notes = rec_brief.notes or ""
    if not notes or not any(c in notes for c in _REPEAL_CUES):
        return []
    own = _own_identity(rec_brief.raw)
    out: list[tuple[_LawBrief, str, str]] = []
    for pair in parse_citations(notes):
        if own and pair == own:
            continue
        cands = [c for c in (all_by_ny.get(pair) or [])
                 if c.law_book_id != rec_brief.law_book_id]
        if len(cands) == 1:
            out.append((cands[0], "law_notes", "medium"))
        # Ambiguous → skip (same false-friend rule as amendment matching)
    return out


def build_amendment_links(
    source: Path,
    limit: int = 0,
) -> list[AmendmentLinkRow]:
    """Scan JSONL and return one row per معدل that has ≥1 amender or replacement."""
    muadal: list[_LawBrief] = []
    taadil: list[_LawBrief] = []
    all_briefs: list[_LawBrief] = []

    for i, rec in enumerate(iter_records(source)):
        if limit and i >= limit:
            break
        b = _brief(rec)
        if not b:
            continue
        all_briefs.append(b)
        if b.flag == "معدل":
            muadal.append(b)
        elif b.flag == "تعديل":
            taadil.append(b)

    muadal_by_ny: dict[tuple[str, str], list[_LawBrief]] = {}
    all_by_ny: dict[tuple[str, str], list[_LawBrief]] = {}
    all_by_id = {b.law_book_id: b for b in all_briefs}

    def _index(brief: _LawBrief, dest: dict) -> None:
        if brief.num and brief.year:
            dest.setdefault((brief.num, brief.year), []).append(brief)
        for pair in parse_citations(brief.title):
            dest.setdefault(pair, [])
            if brief not in dest[pair]:
                dest[pair].append(brief)

    for m in muadal:
        _index(m, muadal_by_ny)
    for b in all_briefs:
        _index(b, all_by_ny)

    # base_id -> amender refs
    amended_by: dict[int, list[AmenderRef]] = {}
    replaced_by: dict[int, list[AmenderRef]] = {}
    seen_edges: set[tuple[int, int]] = set()

    for am in taadil:
        base, method, conf = match_amender_to_base(am, muadal_by_ny, muadal)
        if base is None:
            continue
        edge = (base.law_book_id, am.law_book_id)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        amended_by.setdefault(base.law_book_id, []).append(
            AmenderRef(
                law_book_id=am.law_book_id,
                title=am.title,
                year=am.year,
                method=method,
                confidence=conf,
            )
        )

    # Optional repeal notes on any record
    for b in all_briefs:
        for replaced, method, conf in _repeal_targets(b, all_by_ny, all_by_id):
            edge = (replaced.law_book_id, b.law_book_id)
            if edge in seen_edges and b.flag == "تعديل":
                # already linked as amendment; still record replace if cue present
                pass
            replaced_by.setdefault(replaced.law_book_id, []).append(
                AmenderRef(
                    law_book_id=b.law_book_id,
                    title=b.title,
                    year=b.year,
                    method=method,
                    confidence=conf,
                )
            )

    # Sort amenders newest-first
    def _year_key(a: AmenderRef) -> int:
        try:
            return int(a.year or "0")
        except ValueError:
            return 0

    rows: list[AmendmentLinkRow] = []
    base_ids = set(amended_by) | set(replaced_by)
    # Include معدل with no links? No — keep sidecar lean.
    # But also include معدل that appear only as bases via title index:
    for mid in sorted(base_ids):
        base = all_by_id.get(mid)
        if not base:
            continue
        ams = sorted(amended_by.get(mid, []), key=_year_key, reverse=True)
        reps = sorted(replaced_by.get(mid, []), key=_year_key, reverse=True)
        # Dedupe replaced_by against amended_by ids
        am_ids = {a.law_book_id for a in ams}
        reps = [r for r in reps if r.law_book_id not in am_ids]
        rows.append(
            AmendmentLinkRow(
                base_law_book_id=base.law_book_id,
                base_title=base.title,
                base_num=base.num,
                base_year=base.year,
                classification=base.classification or base.law_index,
                amended_by=ams,
                replaced_by=reps,
            )
        )
    return rows


def save_amendment_links(
    rows: list[AmendmentLinkRow],
    path: Path | None = None,
) -> Path:
    out = path or AMENDMENT_LINKS_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.to_json(), ensure_ascii=False) + "\n")
    return out


def load_amendment_links(
    path: Path | None = None,
) -> dict[int, dict]:
    """
    Map base_law_book_id → link row dict.
    Missing/empty file → {}.
    """
    p = path or AMENDMENT_LINKS_FILE
    if not p.exists():
        return {}
    out: dict[int, dict] = {}
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                lid = int(row.get("base_law_book_id") or 0)
            except (TypeError, ValueError):
                continue
            if lid:
                out[lid] = row
    return out


class AmendmentIndex:
    """In-memory query helper over the sidecar."""

    def __init__(self, by_base: dict[int, dict] | None = None):
        self.by_base = by_base or {}

    @classmethod
    def load(cls, path: Path | None = None) -> "AmendmentIndex":
        return cls(load_amendment_links(path))

    def get(self, law_book_id: int | str | None) -> dict | None:
        try:
            lid = int(law_book_id or 0)
        except (TypeError, ValueError):
            return None
        return self.by_base.get(lid)

    def amended_by(self, law_book_id: int | str | None) -> list[dict]:
        row = self.get(law_book_id)
        if not row:
            return []
        return list(row.get("amended_by") or [])

    def warning_entries(self, rows: Iterable[dict]) -> list[dict]:
        """
        For retrieved chunks: one entry per معدل law_book_id with amended_by.
        Also includes تعديل/معدل titles that lack links (warning-only).
        """
        seen: set[int] = set()
        entries: list[dict] = []
        for r in rows:
            flag = (r.get("law_flag") or "").strip()
            if flag not in ("معدل", "تعديل"):
                continue
            try:
                lid = int(r.get("law_book_id") or 0)
            except (TypeError, ValueError):
                lid = 0
            if lid and lid in seen:
                continue
            if lid:
                seen.add(lid)
            title = (r.get("title") or "")[:120]
            amenders = self.amended_by(lid) if flag == "معدل" else []
            entries.append({
                "law_book_id": lid,
                "title": title,
                "law_flag": flag,
                "amended_by": [
                    {
                        "law_book_id": a.get("law_book_id"),
                        "title": (a.get("title") or "")[:100],
                        "year": a.get("year") or "",
                        "method": a.get("method") or "",
                    }
                    for a in amenders[:12]
                ],
            })
        return entries

    def format_warning_lines(self, entries: list[dict]) -> list[str]:
        lines: list[str] = []
        for e in entries:
            title = e.get("title") or ""
            flag = e.get("law_flag") or ""
            amenders = e.get("amended_by") or []
            if amenders:
                names = []
                for a in amenders[:6]:
                    t = (a.get("title") or "")[:70]
                    y = a.get("year") or ""
                    names.append(f"{t}" + (f" ({y})" if y else ""))
                extra = f" (+{len(amenders) - 6})" if len(amenders) > 6 else ""
                lines.append(f"{title}  ⚠[{flag}]")
                lines.append("    ← معدّل بـ: " + "؛ ".join(names) + extra)
            else:
                lines.append(f"{title}  ⚠[{flag}]")
        return lines


_INDEX: AmendmentIndex | None = None


def get_amendment_index(path: Path | None = None, reload: bool = False) -> AmendmentIndex:
    global _INDEX
    if _INDEX is None or reload or path is not None:
        _INDEX = AmendmentIndex.load(path)
    return _INDEX


def _row_arts(r: dict) -> set[str]:
    from common import article_nums_list, extract_article_numbers
    padded = r.get("article_nums") or extract_article_numbers(r.get("text") or "")
    return set(article_nums_list(padded))


def pull_amender_article_chunks(
    table,
    rows: list[dict],
    index: AmendmentIndex | None = None,
    *,
    qvec: list[float] | None = None,
    where: str | None = None,
    max_extra: int = 2,
) -> list[dict]:
    """
    Cheap LanceDB pull: for retrieved معدل chunks, fetch up to max_extra
    chunks from linked amenders that share an article_nums label.
    Reuses the question embedding when provided (no extra embed/LLM).
    Marks extras with _amendment_pull=True.

    Disabled on sidecars built before 1.1.0 (unbounded substring false
    friends like العمل ⊂ العملة). ⚠ amended_by listing still works.
    """
    if max_extra <= 0 or not rows or qvec is None:
        return rows
    idx = index or get_amendment_index()
    if not idx.by_base:
        return rows
    # Gate same-article pull until boundary-safe builder version is on disk.
    sample = next(iter(idx.by_base.values()), {})
    ver = str(sample.get("builder_version") or "0")
    try:
        parts = tuple(int(x) for x in ver.split(".")[:3])
    except ValueError:
        parts = (0,)
    while len(parts) < 3:
        parts = parts + (0,)
    if parts < (1, 1, 0):
        return rows

    seen = {r.get("chunk_id") for r in rows if r.get("chunk_id")}
    extras: list[dict] = []

    has_article_col = True
    try:
        has_article_col = "article_nums" in {f.name for f in table.schema}
    except Exception:
        pass

    for r in rows:
        if len(extras) >= max_extra:
            break
        if (r.get("law_flag") or "").strip() != "معدل":
            continue
        try:
            base_id = int(r.get("law_book_id") or 0)
        except (TypeError, ValueError):
            continue
        amenders = idx.amended_by(base_id)
        if not amenders:
            continue
        arts = _row_arts(r)
        if not arts:
            continue
        for am in amenders:
            if len(extras) >= max_extra:
                break
            try:
                aid = int(am.get("law_book_id") or 0)
            except (TypeError, ValueError):
                continue
            if not aid:
                continue
            for art in sorted(arts, key=lambda x: int(x) if x.isdigit() else 0)[:4]:
                if len(extras) >= max_extra:
                    break
                if has_article_col:
                    clause = (
                        f"law_book_id = {aid} AND "
                        f"article_nums LIKE '%,{art},%'"
                    )
                else:
                    clause = f"law_book_id = {aid}"
                if where:
                    clause = f"({where}) AND ({clause})"
                try:
                    hits = (
                        table.search(qvec)
                        .metric("cosine")
                        .where(clause, prefilter=True)
                        .limit(1)
                        .to_list()
                    )
                except Exception:
                    hits = []
                for h in hits:
                    cid = h.get("chunk_id")
                    if cid and cid in seen:
                        continue
                    if cid:
                        seen.add(cid)
                    h = dict(h)
                    h["_amendment_pull"] = True
                    h["_amends_base_id"] = base_id
                    extras.append(h)
                    break

    if not extras:
        return rows
    return list(rows) + extras


def resolve_amendment_source(explicit: Path | None = None) -> Path:
    """Prefer explicit → local master → sibling masters → sample fixture."""
    if explicit is not None:
        return explicit
    candidates = [
        ROOT / "sources" / "laws_master.jsonl",
        Path(r"C:\iraqi-law-rag\sources\laws_master.jsonl"),
        Path(r"C:\iraqi-legislation-rag\sources\laws_master.jsonl"),
        ROOT / "sources" / "sample_laws.jsonl",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def summarize_build(rows: list[AmendmentLinkRow]) -> dict[str, Any]:
    n_links = sum(len(r.amended_by) for r in rows)
    n_replace = sum(len(r.replaced_by) for r in rows)
    methods: dict[str, int] = {}
    for r in rows:
        for a in r.amended_by:
            methods[a.method] = methods.get(a.method, 0) + 1
    majors = {}
    for num, year, label in MAJOR_BASES:
        hit = next(
            (r for r in rows if r.base_num == num and r.base_year == year),
            None,
        )
        majors[label] = {
            "base_law_book_id": hit.base_law_book_id if hit else None,
            "amended_by": len(hit.amended_by) if hit else 0,
        }
    return {
        "bases_with_links": len(rows),
        "amendment_edges": n_links,
        "replace_edges": n_replace,
        "methods": methods,
        "majors": majors,
    }
