"""
Shared config + helpers for the Iraqi Law RAG toolkit.

Everything else (ingest.py, ask.py) imports from here so there is ONE place
that defines: where the vector store lives, which embedding model to use, and
how a source record becomes retrievable chunks.

Design goal: the corpus is PLUGGABLE. `iter_records()` reads any *.jsonl file
in sources/ that has the same field shape as laws_master.jsonl. To add news,
more laws, court decisions later, you just drop another .jsonl into sources/
(or point --source at it) and re-run ingest.py — no code changes needed.
"""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from dataclasses import dataclass

# --- Paths ---------------------------------------------------------------
ROOT = Path(__file__).parent
SOURCES_DIR = ROOT / "sources"          # drop *.jsonl corpus files here
DB_DIR = ROOT / "lancedb"               # the vector store (created on ingest)
TABLE_NAME = "laws"
CACHE_DIR = ROOT / "cache"              # answer cache (jsonl), created on first hit
ANSWER_CACHE_FILE = CACHE_DIR / "answers.jsonl"
ARTICLE_INDEX_FILE = CACHE_DIR / "article_index.jsonl"
LAWS_MASTER = SOURCES_DIR / "laws_master.jsonl"
SAMPLE_LAWS = SOURCES_DIR / "sample_laws.jsonl"


def default_corpus_path() -> Path:
    """
    Prefer a full release snapshot; fall back to the committed sample fixture
    so cold-start works without downloading ~259MB first.
    """
    if LAWS_MASTER.exists():
        return LAWS_MASTER
    if SAMPLE_LAWS.exists():
        return SAMPLE_LAWS
    return LAWS_MASTER


def load_dotenv(path: Path | None = None) -> None:
    """
    Load KEY=VALUE pairs from .env into os.environ if not already set.
    No python-dotenv dependency. .env is gitignored — never commit keys.
    """
    env_path = path or (ROOT / ".env")
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


load_dotenv()

# --- Embedding model -----------------------------------------------------
# bge-m3: strong multilingual model, good on Arabic. 1024-dim vectors.
# Runs on the GTX 1650 (needs ~2GB VRAM for inference). Same model MUST be
# used at ingest time and query time — never mix models in one store.
EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024

# bge-m3 defaults to an 8192-token window. Our chunks cap at CHUNK_CHAR_MAX
# (2500 chars ~= 700-900 Arabic tokens), so 1024 truncates NOTHING but stops
# attention from allocating for 8192 positions. This is the single biggest
# reason the 4GB GTX 1650 can run this at all. Do not raise it without also
# lowering BATCH in ingest.py.
MAX_SEQ_LEN = 1024

# Use fp16 on CUDA (halves VRAM). Never on CPU — fp16 on CPU is slow/unsupported
# for some ops. ingest.py and ask.py both check the device before applying it.
USE_FP16_ON_CUDA = True

# --- Alternative: embed via OpenRouter instead of local GPU ---------------
# Same bge-m3 weights, hosted remotely. Full 99,377-chunk corpus costs
# roughly $0.50-$0.75 at $0.01/M tokens — removes the GPU bottleneck
# entirely. Requires OPENROUTER_API_KEY in the environment.
# IMPORTANT: don't mix local-GPU-embedded chunks with API-embedded chunks in
# the same store — different hosting providers can pool/normalize slightly
# differently even for "the same" model. Pick one path per store and stick
# to it; if switching, wipe lancedb/ and re-ingest everything the new way.
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
OPENROUTER_EMBED_MODEL = "baai/bge-m3"
OPENROUTER_BATCH = 64          # texts per API request
OPENROUTER_WORKERS = 3         # concurrent requests (8 hit OpenRouter 429s)

# --- Answer model (the LLM that writes the reply) ------------------------
# Routed through OpenRouter too, so ONE key ($10 balance) covers both
# embedding and answering — no separate Anthropic account needed.
#
# Cost per query with ~6 retrieved chunks (~3,250 in / ~700 out tokens):
#   qwen3-235b-a22b-instruct-2507   ~$0.0006   ~17,000 queries per $10
#   deepseek-v4-flash               ~$0.0006   ~16,500 queries per $10
#   kimi-k2.5                       ~$0.0026    ~3,800 queries per $10
#   anthropic/claude-sonnet-5       ~$0.0135      ~740 queries per $10
#
# Default is deliberately a cheap one: at this stage you are testing
# RETRIEVAL, and burning the balance on a premium model tells you nothing
# about whether the right law came back. Switch with --answer-model once
# retrieval is good and you're judging answer quality.
#
# VERIFY SLUGS at https://openrouter.ai/models before relying on them —
# model slugs get renamed and delisted regularly.
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Model slugs get renamed/delisted on OpenRouter regularly — one source
# reported 7 endpoints delisted in a single 9-day span in July 2026. Don't
# trust a single hardcoded slug. ask.py tries these IN ORDER and automatically
# falls through to the next one if OpenRouter says a slug is invalid, so a
# rename doesn't break the whole script — it just costs one wasted call.
#
# Verify/refresh this list anytime with:  python ask.py --list-models
ANSWER_MODEL_CANDIDATES = [
    "google/gemini-2.5-flash-lite",   # confirmed working, cheapest ($0.10/M in)
    "deepseek/deepseek-v4-flash",
    "moonshotai/kimi-k2.5",
    "qwen/qwen3.6-27b",   # kept last: repeatedly returned empty content in
                          # testing (thinking-mode budget issue) even with
                          # reasoning.exclude — likely unsupported by this
                          # provider. Fine as a last resort, not a first pick.
]
ANSWER_MODEL_OR = ANSWER_MODEL_CANDIDATES[0]   # kept for backward-compat references

# --- Chunking ------------------------------------------------------------
# Most laws are tiny (avg ~2900 chars); ~74% are under 2000 chars -> 1 chunk.
# Only ~182 laws exceed 50k chars; those get split on Arabic-digit article
# markers (lines that are just a number like ١ ٢ ٣). We pack articles up to
# this many characters per chunk.
CHUNK_CHAR_TARGET = 1500
CHUNK_CHAR_MAX = 2500          # never emit a single chunk bigger than this without trying to split

# A line that is ONLY an article number (Arabic-Indic or ASCII digits),
# used as a split point inside big laws.
_ARTICLE_MARKER = re.compile(r"^[\s]*[٠-٩0-9]{1,4}[\s]*$")

# Explicit article references inside running text:
#   المادة 438 / المادة - ٣١ - / المادتين 74 و 75 / المواد 438-449
_ARTICLE_REF = re.compile(
    r"(?:المادة|المادتين|المادتان|المواد|ماد[ةه])\s*[-–—]?\s*"
    r"([٠-٩0-9]{1,4})(?:\s*[-–—]\s*([٠-٩0-9]{1,4}))?",
)


def normalize_arabic_digits(s: str) -> str:
    """Convert Arabic-Indic digits ٠-٩ to ASCII 0-9. Useful for display/sort."""
    if not s:
        return s
    table = {ord("٠") + i: str(i) for i in range(10)}
    return s.translate(table)


def _ascii_article_num(raw: str) -> str:
    """Normalize one article-number token to ASCII without leading zeros."""
    n = normalize_arabic_digits(raw).strip()
    n = re.sub(r"[^0-9]", "", n)
    if not n:
        return ""
    return str(int(n))  # drop leading zeros; "0438" -> "438"


def extract_article_numbers(text: str) -> str:
    """
    Pull article numbers out of a chunk for metadata / exact lookup.

    Returns a comma-padded ASCII string like ',31,57,188,' so SQL filters can
    match exactly with `article_nums LIKE '%,31,%'` without hitting 310.
    Empty string if none found.
    """
    if not text:
        return ""
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(raw: str) -> None:
        n = _ascii_article_num(raw)
        if n and n not in seen:
            seen.add(n)
            ordered.append(n)

    for m in _ARTICLE_REF.finditer(text):
        _add(m.group(1))
        if m.group(2):
            # Expand inclusive ranges lightly: المواد 438-449 → endpoints only
            # (full expansion of big ranges would bloat metadata).
            a = _ascii_article_num(m.group(1))
            b = _ascii_article_num(m.group(2))
            if a and b:
                try:
                    lo, hi = int(a), int(b)
                    if 0 < hi - lo <= 30:  # small ranges only
                        for n in range(lo, hi + 1):
                            _add(str(n))
                    else:
                        _add(b)
                except ValueError:
                    _add(b)
    for ln in text.split("\n"):
        if _ARTICLE_MARKER.match(ln):
            _add(ln.strip())

    if not ordered:
        return ""
    return "," + ",".join(ordered) + ","


def article_nums_list(padded: str) -> list[str]:
    """',31,57,' → ['31', '57']. Empty/malformed → []."""
    if not padded:
        return []
    return [x for x in padded.strip(",").split(",") if x]


def parse_article_query(question: str) -> str | None:
    """
    If the user asked for a specific article ('المادة 47' / 'م ٤٣٨'), return
    its ASCII number; otherwise None. Used to boost exact-lookup hits.
    """
    if not question:
        return None
    m = _ARTICLE_REF.search(question)
    if m:
        return _ascii_article_num(m.group(1)) or None
    # bare "م 47" / "م٤٧" shorthand
    m2 = re.search(r"(?<![^\s])م\s*[-–—]?\s*([٠-٩0-9]{1,4})(?![٠-٩0-9])", question)
    if m2:
        return _ascii_article_num(m2.group(1)) or None
    return None


def normalize_ar(s: str) -> str:
    """
    Light Arabic normalization for MATCHING ONLY (never for stored text):
    strip diacritics/tatweel, unify alef forms, ya/alef-maqsura, ta-marbuta.
    Needed because the same law title appears as both الأحوال and الاحوال.
    """
    if not s:
        return ""
    s = re.sub(r"[\u064B-\u0652\u0640]", "", s)      # harakat + tatweel
    s = re.sub(r"[\u0622\u0623\u0625\u0671]", "\u0627", s)  # آأإٱ -> ا
    s = s.replace("\u0649", "\u064A")                 # ى -> ي
    s = s.replace("\u0629", "\u0647")                 # ة -> ه
    return re.sub(r"\s+", " ", s).strip()


def is_overview_question(question: str) -> bool:
    """True for 'what is law X' style asks — prefer defining articles."""
    q = normalize_ar(question or "")
    return any(
        q.startswith(normalize_ar(p)) or f" {normalize_ar(p)}" in f" {q}"
        for p in (
            "ما هو", "ما هي", "ما المقصود", "عرف", "ما تعريف",
            "what is", "define",
        )
    )


def title_search_needles(question: str) -> list[str]:
    """
    Phrases to LIKE-match against law titles when the user names an instrument.

    Corpus titles are usually stored without hamza (الاهلي not الأهلي), so
    needles are normalize_ar'd. Returns longest-first unique needles.
    """
    from law_registry import extract_instrument_phrases

    q = normalize_ar(question or "")
    q = q.replace("؟", "").replace("?", "").strip()
    q = re.sub(
        r"^(ما هو|ما هي|ما المقصود ب|ما المقصود|ما احكام|احكام|عرف|اشرح|وضح|هل يجوز|هل يمكن)\s+",
        "",
        q,
    ).strip()

    needles: list[str] = []
    needles.extend(extract_instrument_phrases(question or ""))
    if len(q) >= 6 and len(q) <= 80:
        needles.append(q)
    for pref in ("قانون", "نظام", "تعليمات", "قرار", "بيان"):
        pn = normalize_ar(pref)
        if q.startswith(pn + " "):
            rest = q[len(pn) + 1:].strip()
            # take first ~6 tokens of rest as a short needle
            rest_short = " ".join(rest.split()[:6])
            if len(rest_short) >= 6:
                needles.append(rest_short)
            break

    generic = {normalize_ar(x) for x in ("القانون", "النظام", "العراقي", "العراق")}
    out: list[str] = []
    seen: set[str] = set()
    for n in needles:
        n = normalize_ar(n)
        if n in seen or n in generic or len(n) < 6:
            continue
        # skip whole-sentence needles that are mostly about facts not titles
        if len(n.split()) > 10:
            continue
        seen.add(n)
        out.append(n)
    out.sort(key=len, reverse=True)
    return out


def is_exact_lookup_question(question: str) -> bool:
    """
    True when the question is basically "show me article N [of law X]",
    not an analytical ask that happens to mention an article number.
    Used to skip generation and print the verbatim chunk instead.
    """
    if not question or not parse_article_query(question):
        return False
    stripped = _ARTICLE_REF.sub(" ", question)
    stripped = re.sub(
        r"(?<![^\s])م\s*[-–—]?\s*[٠-٩0-9]{1,4}(?![٠-٩0-9])", " ", stripped
    )
    leftover = normalize_ar(stripped)
    analytical = (
        "ما ", "ماهي", "ما هي", "كيف", "متى", "هل ", "لماذا", "لم ",
        "عقوبه", "احكام", "شرح", "فسر", "وضح", "قارن", "الفرق",
    )
    return not any(w in leftover for w in analytical)


# --- Priority corpus -----------------------------------------------------
# `ingest.py --priority` embeds only these first, so you get a usable system
# in a few hours instead of waiting for all 99,377 chunks. These are the codes
# most real questions land on. The rest still ingests later (it's resumable),
# and nothing here is destructive — it only changes WHAT GETS EMBEDDED FIRST.
#
# VERIFY THESE AGAINST YOUR ACTUAL DATA before trusting the count:
#   python ingest.py --priority --dry-run
PRIORITY_TITLE_PATTERNS = [
    "قانون العقوبات",
    "القانون المدني",
    "الاحوال الشخصيه",
    "قانون العمل",
    "المرافعات المدنيه",
    "اصول المحاكمات الجزائيه",
    "قانون التجاره",
    "قانون الشركات",
    "قانون الاثبات",
    "قانون الاستثمار",
    "قانون التنفيذ",
    "دستور",
]
# Anything enacted from this year on is also treated as priority.
PRIORITY_YEAR_FROM = 2010


def is_priority(rec: dict) -> bool:
    """
    True if this record belongs in the fast first pass: an IN-FORCE law
    that's either one of the major codes or reasonably recent.

    Requires status_label == 'ساري'. Without this, title substring matching
    also catches historical amendment acts to superseded predecessor codes —
    e.g. 1920s-40s amendments to the old Baghdadi Penal Code match
    "قانون العقوبات" just as well as the current 1969 code does, and those
    are almost certainly repealed by now.
    """
    if (rec.get("status_label") or "").strip() != "ساري":
        return False
    title = normalize_ar(rec.get("lawTitle") or "")
    for pat in PRIORITY_TITLE_PATTERNS:
        if normalize_ar(pat) in title:
            return True
    try:
        if int(str(rec.get("lawYear") or "0")[:4]) >= PRIORITY_YEAR_FROM:
            return True
    except (ValueError, TypeError):
        pass
    return False


@dataclass
class Chunk:
    chunk_id: str          # f"{lawBookID}#{n}"
    law_book_id: int
    title: str
    category: str
    law_index: str         # subject area
    status_label: str      # ساري / ملغى
    law_valid: str         # Y / N
    law_flag: str          # تعديل / معدل / ملغى / ""
    year: str
    date_iso: str
    source_url: str
    pdf_url: str
    text: str              # the actual chunk text that gets embedded
    article_nums: str = "" # ',31,57,' — see extract_article_numbers()


def iter_records(source_path: Path):
    """Yield parsed JSON records from one .jsonl file, skipping blanks/bad lines."""
    with source_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _split_big_law(full_text: str) -> list[str]:
    """
    Split a large law into ~CHUNK_CHAR_TARGET pieces on article-number markers.
    Falls back to paragraph packing if there are no clear markers.
    """
    lines = full_text.split("\n")
    # Build segments that each start at an article marker.
    segments: list[list[str]] = []
    current: list[str] = []
    for ln in lines:
        if _ARTICLE_MARKER.match(ln) and current:
            segments.append(current)
            current = [ln]
        else:
            current.append(ln)
    if current:
        segments.append(current)

    # Pack consecutive segments up to the char target.
    chunks: list[str] = []
    buf = ""
    for seg in segments:
        seg_text = "\n".join(seg).strip()
        if not seg_text:
            continue
        if buf and len(buf) + len(seg_text) > CHUNK_CHAR_TARGET:
            chunks.append(buf.strip())
            buf = seg_text
        else:
            buf = (buf + "\n\n" + seg_text) if buf else seg_text
        # Hard cap: if a single segment is enormous, hard-wrap it.
        while len(buf) > CHUNK_CHAR_MAX:
            chunks.append(buf[:CHUNK_CHAR_MAX].strip())
            buf = buf[CHUNK_CHAR_MAX:]
    if buf.strip():
        chunks.append(buf.strip())
    return [c for c in chunks if c]


def record_to_chunks(rec: dict) -> list[Chunk]:
    """
    Turn one law record into 1+ retrievable chunks.

    Small laws -> a single chunk (title + full_text).
    Big laws   -> multiple chunks split on article markers, EACH prefixed with
                  the law title so a retrieved fragment still knows what law
                  it belongs to.
    Records with empty full_text are skipped (nothing to retrieve).
    """
    full_text = (rec.get("full_text") or "").strip()
    if not full_text:
        return []

    law_id = rec.get("lawBookID")
    title = (rec.get("lawTitle") or "").strip()

    meta = dict(
        law_book_id=law_id,
        title=title,
        category=rec.get("category") or "",
        law_index=rec.get("lawIndex") or "",
        status_label=rec.get("status_label") or "",
        law_valid=rec.get("lawValid") or "",
        law_flag=rec.get("lawFlag") or "",
        year=str(rec.get("lawYear") or ""),
        date_iso=rec.get("date_iso") or "",
        source_url=rec.get("source_url") or "",
        pdf_url=rec.get("pdf_url") or "",
    )

    if len(full_text) <= CHUNK_CHAR_MAX:
        body = f"{title}\n\n{full_text}" if title else full_text
        return [Chunk(
            chunk_id=f"{law_id}#0", text=body,
            article_nums=extract_article_numbers(body), **meta,
        )]

    pieces = _split_big_law(full_text)
    chunks = []
    for i, piece in enumerate(pieces):
        # Prefix each fragment with the title for standalone context.
        body = f"{title}\n\n{piece}" if title else piece
        chunks.append(Chunk(
            chunk_id=f"{law_id}#{i}", text=body,
            article_nums=extract_article_numbers(body), **meta,
        ))
    return chunks
