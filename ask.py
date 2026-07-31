"""
ask.py — the RAG loop. Ask a question in Arabic (or English), get an answer
grounded in the retrieved Iraqi laws, WITH citations.

Everything routes through OpenRouter — ONE key covers both the embedding and
the answer, so no separate Anthropic account is needed.

    python ask.py "ما هي عقوبة السرقة؟"           # one-shot
    python ask.py                                  # interactive
    python ask.py --show-chunks "..."              # print retrieved text
    python ask.py --compare "..."                  # same question, 3 models
    python ask.py --all "..."                      # include repealed laws
    python ask.py --k 10 "..."                     # retrieve more chunks
    python ask.py --answer-model deepseek/deepseek-v4-flash "..."
    python ask.py --no-verify "..."                # skip citation check (faster/cheaper)

By DEFAULT it retrieves only IN-FORCE laws (status_label = ساري) so the model
never presents a repealed (ملغى) law as current — a legal-correctness rule.
Pass --all to search everything (repealed results are clearly marked).

Setup:
    $env:OPENROUTER_API_KEY = "sk-or-v1-..."

--local-embed embeds the question with the local GPU model instead of the
API. Only use it if the store was built WITHOUT `ingest.py --api`: the query
must be embedded the same way the store was.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import lancedb

from common import (
    ROOT, DB_DIR, TABLE_NAME, ARTICLES_TABLE_NAME, EMBED_MODEL, MAX_SEQ_LEN, USE_FP16_ON_CUDA,
    OPENROUTER_URL, OPENROUTER_EMBED_MODEL,
    OPENROUTER_CHAT_URL, OPENROUTER_MODELS_URL,
    ANSWER_MODEL_OR, ANSWER_MODEL_CANDIDATES,
    CACHE_DIR, ANSWER_CACHE_FILE,
    parse_article_query, extract_article_numbers, article_nums_list,
    is_exact_lookup_question, normalize_ar,
    title_search_needles, is_overview_question,
    prefer_instrument_titled_rows, prefer_law_id_rows,
    load_dotenv, set_use_law_cards,
)
from query_plan import (
    Shape, plan_query, fuse_legs, parse_citation_key, route_vector_margin,
)

load_dotenv()

TOP_K = 6


class UsageMeter:
    """Accumulate OpenRouter usage across the calls that make up one query."""

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, kind: str, model: str, usage: dict | None):
        if not usage:
            return
        entry = {
            "kind": kind,
            "model": model,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            # OpenRouter returns cost in USD when usage.include=true
            "cost": float(usage.get("cost") or 0),
        }
        self.calls.append(entry)

    def total_cost_usd(self) -> float:
        return sum(c["cost"] for c in self.calls)

    def total_tokens(self) -> tuple[int, int]:
        prompt = sum(c["prompt_tokens"] for c in self.calls)
        completion = sum(c["completion_tokens"] for c in self.calls)
        return prompt, completion

    def cost_report(self, lang: str = "ar", *, cached: bool = False) -> dict:
        """Structured cost for API/UI demos (this call + ×10 / ×100)."""
        cost = 0.0 if cached else self.total_cost_usd()
        prompt, completion = (0, 0) if cached else self.total_tokens()
        parts = (
            []
            if cached
            else [
                f"{c['kind']}={c['prompt_tokens']}+{c['completion_tokens']}"
                for c in self.calls
            ]
        )
        line = format_cost_demo_line(cost, lang=lang, cached=cached)
        if not cached and (prompt or completion or parts):
            detail = f"tokens {prompt} in / {completion} out"
            if parts:
                detail += f" [{', '.join(parts)}]"
            line = f"{line}  ·  {detail}"
        return {
            "usd": round(cost, 8),
            "usd_x10": round(cost * 10, 8),
            "usd_x100": round(cost * 100, 8),
            "display": format_usd(cost),
            "display_x10": format_usd(cost * 10),
            "display_x100": format_usd(cost * 100),
            "cents": format_cents(cost),
            "cents_x10": format_cents(cost * 10),
            "cents_x100": format_cents(cost * 100),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "calls": list(self.calls) if not cached else [],
            "cached": cached,
            "line": line,
            "line_short": format_cost_demo_line(cost, lang=lang, cached=cached),
        }

    def summary_line(self, lang: str = "ar") -> str:
        if not self.calls:
            return (
                "cost: (no usage data)"
                if lang == "en"
                else "التكلفة: (لا بيانات استخدام)"
            )
        return self.cost_report(lang=lang)["line"]


def format_usd(amount: float) -> str:
    if amount <= 0:
        return "$0"
    if amount < 1e-6:
        return f"${amount:.8f}"
    if amount < 0.01:
        return f"${amount:.6f}"
    if amount < 1:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


def format_cents(amount_usd: float) -> str:
    """Human cents string for demo projections (from USD)."""
    if amount_usd <= 0:
        return "0¢"
    cents = amount_usd * 100
    if cents < 0.01:
        return f"{cents:.4f}¢"
    if cents < 1:
        return f"{cents:.3f}¢"
    if cents < 100:
        return f"{cents:.2f}¢"
    return format_usd(amount_usd)


def format_cost_demo_line(
    cost_usd: float,
    *,
    lang: str = "ar",
    cached: bool = False,
) -> str:
    if cached:
        return (
            "Cost: $0 (cached)"
            if lang == "en"
            else "التكلفة: $0 (من الذاكرة المؤقتة)"
        )
    c10, c100 = cost_usd * 10, cost_usd * 100
    this_s, this_c = format_usd(cost_usd), format_cents(cost_usd)
    x10 = f"{format_usd(c10)} ≈ {format_cents(c10)}"
    x100 = f"{format_usd(c100)} ≈ {format_cents(c100)}"
    if lang == "en":
        return (
            f"This call: {this_s} (≈ {this_c})  ·  "
            f"10 same ≈ {x10}  ·  100 same ≈ {x100}"
        )
    return (
        f"هذا الطلب: {this_s} (≈ {this_c})  ·  "
        f"١٠ مثلها ≈ {x10}  ·  ١٠٠ مثلها ≈ {x100}"
    )


def _row_article_set(r: dict) -> set[str]:
    padded = r.get("article_nums") or extract_article_numbers(r.get("text") or "")
    return set(article_nums_list(padded))


def fabricated_citations(answer: str, rows: list[dict]) -> list[str]:
    """
    Free local gate: article numbers the answer cites that appear in NO
    retrieved chunk. True fabrications — no false positives, $0.
    """
    cited = article_nums_list(extract_article_numbers(answer))
    if not cited:
        return []
    present: set[str] = set()
    for r in rows:
        present |= _row_article_set(r)
    return [a for a in cited if a not in present]


def chunks_for_cited_articles(answer: str, rows: list[dict]) -> list[dict]:
    """
    Shrink verification context to only chunks that carry an article the
    answer actually cited. Falls back to all rows if no citations parsed.
    """
    cited = set(article_nums_list(extract_article_numbers(answer)))
    if not cited:
        return rows
    matched = [r for r in rows if cited & _row_article_set(r)]
    return matched or rows


def answer_cache_key(question: str, rows: list[dict], model: str,
                     detailed: bool, lang: str = "ar") -> str:
    payload = {
        "q": normalize_ar(question),
        "chunks": sorted(r.get("chunk_id") or "" for r in rows),
        "model": model,
        "detailed": detailed,
        "lang": lang,
        # Bump when system prompts change so stale refusals don't stick.
        "prompt_v": 5,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class AnswerCache:
    """Append-only JSONL cache. Loaded fully into memory (head-heavy traffic)."""

    def __init__(self, path: Path = ANSWER_CACHE_FILE):
        self.path = path
        self._mem: dict[str, dict] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = rec.get("key")
                    if key:
                        self._mem[key] = rec

    def get(self, key: str) -> dict | None:
        return self._mem.get(key)

    def put(self, key: str, answer: str, model: str, question: str):
        rec = {
            "key": key,
            "answer": answer,
            "model": model,
            "question": question,
        }
        self._mem[key] = rec
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


CONCISE_SYSTEM_PROMPT = """أنت مساعد قانوني متخصص في التشريعات العراقية. مهمتك الإجابة على الأسئلة القانونية بالاعتماد الحصري على النصوص القانونية المقدمة إليك أدناه.

قواعد صارمة:
- اعتمد فقط على المقاطع القانونية المرفقة. لا تخترع مواد أو أرقام قوانين غير موجودة فيها.
- إذا ظهر أكثر من تشريع لنفس الموضوع بأسماء متقاربة (مثلاً «نظام التعليم الأهلي» و«قانون التعليم العالي الأهلي»)، ميّز بينهما في الجواب ولا تدمجهما في تشريع واحد.
- ابدأ دائماً بما في النصوص من أحكام (عقوبة/حق/إجراء + رقم المادة إن وُجد). الاعتذار أو بيان حدود التغطية يأتي في جملة لاحقة فقط، وليس في افتتاح الجواب.
- إذا كان السؤال أضيق أو بصياغة مختلفة عن النص (مثلاً «تبليغ» بينما النص يتكلم عن تسجيل أو شهادة تأسيس)، انقل الحكم الأقرب الموجود في المقاطع ووضّح الفرق بجملة واحدة.
- فقط إذا خلت المقاطع من أي حكم يمكن ربطه بسؤال المستخدم ولو جزئياً: اذكر أن المصادر المسترجعة لا تغطي السؤال، واذكر بإيجاز أقرب موضوع ظهر فيها إن وُجد. لا تستخدم صيغاً نمطية جاهزة للاعتذار.
- إذا كان أحد النصوص مُلغى (status: ملغى)، نبّه المستخدم صراحةً إلى أنه غير نافذ.
- أجب بالعربية الفصحى الواضحة.
- هذه معلومات قانونية عامة وليست استشارة قانونية.

أسلوب الإجابة (مهم لضبط الطول):
- ابدأ بما تعرفه من النصوص، لا بالاعتذار.
- إذا كان للسؤال إجابات متعددة حسب الظروف (كعقوبات متدرجة)، لخّص النتائج في قائمة مختصرة:
  العقوبة — الشرط الموجب لها بإيجاز — رقم المادة. سطر واحد لكل حالة، دون إعادة كتابة نص المادة كاملا.
- اجمع الحالات التي تؤدي لنفس العقوبة معا بدل تكرارها، واذكر أرقام موادها معا
  (مثال: "الحبس حتى ١٠ سنوات — عدة ظروف مشددة كالإكراه أو السرقة ليلا، المواد ٤٤٢-٤٤٣").
- اختصر شرط كل حالة إلى عبارة قصيرة تلتقط جوهره، ولا تسرد كل كلمة من نص المادة.
- لا داعي لذكر كل التفاصيل الآن؛ إذا أراد المستخدم تفصيل ظرف معين سيسأل عنه لاحقا."""

DETAILED_SYSTEM_PROMPT = """أنت مساعد قانوني متخصص في التشريعات العراقية. مهمتك الإجابة على الأسئلة القانونية بالاعتماد الحصري على النصوص القانونية المقدمة إليك أدناه.

قواعد صارمة:
- اعتمد فقط على المقاطع القانونية المرفقة. لا تخترع مواد أو أرقام قوانين غير موجودة فيها.
- ابدأ دائماً بما في النصوص من أحكام مع الاستشهاد؛ بيان حدود التغطية جملة لاحقة فقط.
- إذا اختلفت صياغة السؤال عن النص مع بقاء الموضوع قريباً، انقل الحكم الأقرب ووضّح الفرق.
- فقط عند غياب أي حكم قابل للربط: قل إن المصادر لا تغطي السؤال واذكر أقرب موضوع ظهر. لا تفتح بالاعتذار إن وُجد حكم ذو صلة.
- استشهد بعنوان القانون ورقم المادة عند كل معلومة، بين قوسين.
- إذا كان أحد النصوص مُلغى (status: ملغى)، نبّه المستخدم صراحةً إلى أنه غير نافذ.
- أجب بالعربية الفصحى الواضحة.
- هذه معلومات قانونية عامة وليست استشارة قانونية."""


def build_context(rows: list[dict]) -> str:
    """Prefer article-granularity rows; emit title once per law group when possible."""
    parts = []
    last_title = None
    for i, r in enumerate(rows, 1):
        status = r.get("status_label") or ""
        flag = r.get("law_flag") or ""
        bits = []
        if status:
            bits.append(f"حالة: {status}")
        if flag in ("معدل", "تعديل"):
            bits.append(f"تنبيه: نص قد يكون معدّلا ({flag})")
        if r.get("granularity") == "article" or r.get("role") == "defines":
            lab = r.get("article_label") or ""
            if lab:
                bits.append(f"مادة: {lab}")
        flag_str = (" | " + " | ".join(bits)) if bits else ""
        title = r.get("title", "") or ""
        header = f"[مصدر {i}] {title} (سنة {r.get('year','')}{flag_str}) — {r.get('source_url','')}"
        if title and title == last_title:
            header = f"[مصدر {i}] (نفس القانون{flag_str})"
        else:
            last_title = title
        parts.append(f"{header}\n{r.get('text','')}")
    return "\n\n---\n\n".join(parts)


def _status_filter(include_all: bool) -> str | None:
    return None if include_all else "status_label = 'ساري'"


def _merge_rows(*lists: list[dict], limit: int) -> list[dict]:
    """Dedupe by chunk_id, preserving earlier-list priority."""
    seen: set[str] = set()
    out: list[dict] = []
    for rows in lists:
        for r in rows:
            cid = r.get("chunk_id") or id(r)
            if cid in seen:
                continue
            seen.add(cid)
            out.append(r)
            if len(out) >= limit:
                return out
    return out


def _overview_rank(r: dict) -> tuple:
    """Prefer defining articles (1–3) and newer instruments."""
    arts = r.get("article_nums") or ""
    score = 0
    if ",1," in arts:
        score += 5
    if ",2," in arts:
        score += 3
    if ",3," in arts:
        score += 1
    try:
        year = int(str(r.get("year") or "0")[:4])
    except ValueError:
        year = 0
    text_len = len(r.get("text") or "")
    return (-score, -year, text_len)


def _title_match_rows(table, qvec: list[float], question: str,
                      where: str | None, limit: int) -> list[dict]:
    """
    Pull chunks whose title contains the named instrument.

    Hybrid/vector alone often ranks a *related* longer title higher
    (e.g. قانون التعليم العالي الاهلي for a سؤال about التعليم الاهلي).
    Title LIKE fixes that when the corpus title actually contains the phrase.
    """
    needles = title_search_needles(question)
    if not needles:
        return []
    matched: list[dict] = []
    seen: set[str] = set()
    for needle in needles:
        # Escape single quotes for SQL string literal
        safe = needle.replace("'", "''")
        clause = f"title LIKE '%{safe}%'"
        if where:
            clause = f"({where}) AND ({clause})"
        try:
            rows = (
                table.search(qvec)
                .metric("cosine")
                .where(clause, prefilter=True)
                .limit(max(limit * 2, 8))
                .to_list()
            )
        except Exception:
            continue
        for r in rows:
            cid = r.get("chunk_id") or id(r)
            if cid in seen:
                continue
            # Defense: require normalized needle in normalized title so a
            # LIKE quirk can't pull unrelated rows.
            if normalize_ar(needle) not in normalize_ar(r.get("title") or ""):
                continue
            seen.add(cid)
            matched.append(r)
        if len(matched) >= limit:
            break
    if is_overview_question(question):
        matched.sort(key=_overview_rank)
    return matched[:limit]


ROUTES_TABLE_NAME = "law_routes"

# Legacy absolute distance threshold — kept for unit-test compat / A/B.
# Live retrieve() uses QueryPlan + route_vector_margin instead.
ROUTE_DIST_HIGH = 0.32

# Function words stripped before in-law content-token overlap scoring.
_CONTENT_STOPWORDS = {
    "ما", "هي", "هو", "في", "من", "على", "علي", "الى", "الي", "عن", "هل",
    "يجوز", "يمكن", "حسب", "وفق", "بموجب", "و", "او", "هذا", "هذه", "ذلك",
    "تلك", "التي", "الذي", "ان", "كان", "يكون", "بين", "مع", "كل", "اي",
    "لا", "لم", "لن", "قد", "عند", "بعد", "قبل", "غير", "فقط", "بناء",
    "قانون", "نظام", "تعليمات", "قرار", "بيان", "المادة", "المواد",
    "الاحكام", "احكام", "بشان", "بخصوص", "what", "is", "the", "a", "an",
    "of", "for", "and", "or", "to", "in", "on", "how", "when", "where",
}


def article_hit_to_row(art: dict) -> dict:
    """Normalize an articles-table / article_index row into retrieve() shape."""
    label = str(art.get("article_label") or "")
    try:
        lid = int(art.get("law_book_id") or 0)
    except (TypeError, ValueError):
        lid = 0
    text = art.get("text") or ""
    return {
        "chunk_id": art.get("chunk_id") or art.get("article_id") or f"art:{lid}:{label}",
        "law_book_id": lid,
        "title": art.get("title") or "",
        "category": art.get("category") or "",
        "status_label": art.get("status_label") or "",
        "law_flag": art.get("law_flag") or "",
        "year": art.get("year") or "",
        "source_url": art.get("source_url") or "",
        "text": text,
        "article_nums": art.get("article_nums") or (f",{label}," if label else ""),
        "article_label": label,
        "granularity": "article",
        "role": art.get("role") or "defines",
        "_distance": art.get("_distance"),
    }


def _registry_citation_ids(question: str) -> list[int]:
    """Hard-scope candidates for رقم N لسنة Y against the offline registry."""
    key = parse_citation_key(question)
    if not key:
        return []
    num, year = key
    try:
        from law_registry import load_registry
        rows = load_registry()
    except Exception:
        return []
    hits: list[int] = []
    for r in rows:
        title = r.get("title") or ""
        # Match ASCII digits inside title (registry titles mix Arabic/ASCII)
        from query_plan import ascii_digits
        t = ascii_digits(title)
        if num in t and year in t and ("رقم" in title or "رقم" in normalize_ar(title)):
            try:
                hits.append(int(r["law_book_id"]))
            except (KeyError, TypeError, ValueError):
                continue
    return hits


def _article_exact_from_index(
    article_label: str,
    *,
    law_ids: list[int] | None = None,
    include_all: bool = False,
    limit: int = 6,
) -> list[dict]:
    """Prefer article_index *defines* over chunk article_nums (mentions-safe)."""
    try:
        from article_index import load_article_index, lookup_defines
        from law_registry import load_registry
    except Exception:
        return []
    defs = lookup_defines(load_article_index(), article_label=str(article_label))
    if law_ids:
        allow = {int(x) for x in law_ids}
        defs = [d for d in defs if int(d.get("law_book_id") or -1) in allow]
    # Attach titles from registry when index rows lack them
    titles: dict[int, dict] = {}
    try:
        for r in load_registry():
            try:
                titles[int(r["law_book_id"])] = r
            except (KeyError, TypeError, ValueError):
                continue
    except Exception:
        pass
    out: list[dict] = []
    for d in defs:
        if not include_all:
            meta = titles.get(int(d.get("law_book_id") or -1), {})
            # If we know status and it's not ساري, skip; unknown → keep
            st = meta.get("status_label")
            if st and st != "ساري":
                continue
        row = article_hit_to_row({
            **d,
            "title": (titles.get(int(d.get("law_book_id") or -1), {}) or {}).get("title")
                     or d.get("title") or "",
            "year": (titles.get(int(d.get("law_book_id") or -1), {}) or {}).get("year")
                    or "",
            "status_label": (titles.get(int(d.get("law_book_id") or -1), {}) or {})
                            .get("status_label") or "",
        })
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _search_articles_table(
    db, qvec: list[float], question: str, *,
    where: str | None, law_ids: list[int] | None,
    article_label: str | None, limit: int,
    defining_only_low: bool = False,
) -> list[dict]:
    """Vector search over LanceDB `articles` (defines). Falls back to []."""
    if ARTICLES_TABLE_NAME not in _route_table_names(db):
        return []
    try:
        table = db.open_table(ARTICLES_TABLE_NAME)
    except Exception:
        return []
    clauses: list[str] = []
    if where:
        # where is written for laws table (`status_label = 'ساري'`) — same col name
        clauses.append(f"({where})")
    if article_label:
        safe = str(article_label).replace("'", "''")
        clauses.append(f"article_label = '{safe}'")
    if law_ids:
        ids = ",".join(str(int(x)) for x in law_ids[:12])
        clauses.append(f"law_book_id IN ({ids})")
    if defining_only_low:
        # Prefer art 1–3 for overview/definitional asks
        clauses.append("(article_label = '1' OR article_label = '2' OR article_label = '3')")
    clause = " AND ".join(clauses) if clauses else None
    try:
        search = table.search(qvec).metric("cosine")
        if clause:
            search = search.where(clause, prefilter=True)
        rows = search.limit(max(limit, 4)).to_list()
    except Exception as e:
        print(f"  (articles search unavailable — {e})")
        return []
    return [article_hit_to_row(r) for r in rows][:limit]


def _route_table_names(db) -> list[str]:
    try:
        listed = db.list_tables()
        if hasattr(listed, "tables") and listed.tables is not None:
            return list(listed.tables)
        if isinstance(listed, (list, tuple)):
            return list(listed)
    except Exception:
        pass
    try:
        return list(db.table_names())
    except Exception:
        return []


def _routing_confidence(
    question: str,
    phrase_ids: list[int],
    seed_ids: list[int],
    vector_ranked: list[tuple[float, int]],
) -> str:
    """
    high → named statute / strong colloquial alias: routed laws lead merge.
    low  → topical ask: hybrid leads; routes only fill gaps.
    """
    from law_registry import extract_instrument_phrases, strongest_seed_alias_len

    if extract_instrument_phrases(question) or phrase_ids:
        return "high"
    # Long colloquial aliases only («قانون التعليم الاهلي», not bare «العقوبات»)
    if strongest_seed_alias_len(question) >= 10:
        return "high"
    if vector_ranked and vector_ranked[0][0] <= ROUTE_DIST_HIGH:
        return "high"
    return "low"


def _route_law_ids(db, qvec: list[float], question: str, limit: int = 12
                   ) -> tuple[list[int], str, dict]:
    """
    Candidate law_book_ids + legacy conf string + routing signals for QueryPlan.

    Order: registry instrument phrases → seed aliases → law-card aliases →
    title-vector hits. Reuses the question embedding for law_routes ($0 extra).
    """
    phrase_ids: list[int] = []
    seed_ids: list[int] = []
    card_ids: list[int] = []
    card_alias_len = 0
    alias_len = 0
    try:
        from law_registry import (
            load_registry,
            laws_matching_instrument_phrases,
            laws_matching_seed_aliases,
            laws_matching_card_aliases,
            strongest_seed_alias_len,
        )
        reg = load_registry()
        phrase_ids = laws_matching_instrument_phrases(question, reg)
        seed_ids = laws_matching_seed_aliases(question, reg)
        card_ids, card_alias_len = laws_matching_card_aliases(question)
        alias_len = strongest_seed_alias_len(question)
    except Exception:
        phrase_ids, seed_ids, card_ids = [], [], []

    vector_ranked: list[tuple[float, int]] = []
    try:
        if ROUTES_TABLE_NAME in _route_table_names(db):
            routes = db.open_table(ROUTES_TABLE_NAME)
            hits = (
                routes.search(qvec)
                .metric("cosine")
                .limit(max(limit * 2, 16))
                .to_list()
            )
            for r in hits:
                lid = int(r.get("law_book_id") or 0)
                if not lid:
                    continue
                dist = float(
                    r.get("_distance") if r.get("_distance") is not None else 9.0
                )
                vector_ranked.append((dist, lid))
    except Exception as e:
        print(f"  (law_routes unavailable — {e})")

    ids: list[int] = []
    seen: set[int] = set()
    for lid in phrase_ids + seed_ids + card_ids:
        if lid not in seen:
            seen.add(lid)
            ids.append(lid)
    for _, lid in vector_ranked:
        if lid not in seen:
            seen.add(lid)
            ids.append(lid)

    conf = _routing_confidence(question, phrase_ids, seed_ids, vector_ranked)
    signals = {
        "phrase_ids": phrase_ids,
        "seed_ids": seed_ids,
        "card_ids": card_ids,
        "card_alias_hit": bool(card_ids) and card_alias_len >= 5,
        "alias_len": alias_len,
        "vector_ranked": vector_ranked,
        "citation_law_ids": _registry_citation_ids(question),
    }
    return ids[:limit], conf, signals


def content_tokens(question: str) -> list[str]:
    """Distinctive question tokens for in-law chunk overlap (no fixed topic list)."""
    qn = normalize_ar(question or "")
    parts = re.split(r"[\s،,.؟?!:؛؛/\\|()\[\]{}\"']+", qn)
    out: list[str] = []
    seen: set[str] = set()
    for t in parts:
        t = t.strip()
        if len(t) < 3 or t in _CONTENT_STOPWORDS or t.isdigit():
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    # Prefer longer (more specific) tokens first for LIKE rescue
    out.sort(key=len, reverse=True)
    return out


def _chunk_topic_bonus(question: str, text: str) -> float:
    """Distance discount when chunk shares content tokens with the question."""
    tokens = content_tokens(question)
    if not tokens:
        return 0.0
    tn = normalize_ar(text or "")
    bonus = 0.0
    for tok in tokens[:12]:
        if tok in tn:
            bonus += 0.06
    return min(bonus, 0.30)


def _chunks_for_laws(table, qvec: list[float], law_ids: list[int],
                     where: str | None, question: str = "",
                     per_law: int = 3, total_cap: int = 12) -> list[dict]:
    """Vector-pull best chunks inside routed laws; keep law-rank priority."""
    rank = {int(lid): i for i, lid in enumerate(law_ids)}
    topic_kws = content_tokens(question)[:2]
    out: list[dict] = []
    seen: set[str] = set()

    def _add(rows: list[dict]) -> None:
        for r in rows:
            cid = r.get("chunk_id") or id(r)
            if cid in seen:
                continue
            seen.add(cid)
            out.append(r)

    for i, lid in enumerate(law_ids):
        # Pull extra from the top-ranked law — that's the named instrument.
        lim = 8 if i == 0 else per_law
        clause = f"law_book_id = {int(lid)}"
        if where:
            clause = f"({where}) AND ({clause})"
        try:
            rows = (
                table.search(qvec)
                .metric("cosine")
                .where(clause, prefilter=True)
                .limit(lim)
                .to_list()
            )
            _add(rows)
        except Exception:
            continue
        # Keyword rescue inside the top law using question-derived tokens.
        if i == 0 and topic_kws:
            for kw in topic_kws:
                safe = kw.replace("'", "''")
                try:
                    kclause = f"{clause} AND text LIKE '%{safe}%'"
                    extra = (
                        table.search(qvec)
                        .metric("cosine")
                        .where(kclause, prefilter=True)
                        .limit(2)
                        .to_list()
                    )
                    _add(extra)
                except Exception:
                    continue

    out.sort(key=lambda r: (
        rank.get(int(r.get("law_book_id") or 0), 99),
        float(r.get("_distance") if r.get("_distance") is not None else 9.0)
        - _chunk_topic_bonus(question, r.get("text") or ""),
    ))
    return out[:total_cap]


def retrieve(table, qvec: list[float], question: str, k: int,
             include_all: bool, vector_only: bool) -> list[dict]:
    """
    Hybrid BM25+vector + article defines + law routing, fused via QueryPlan
    quotas (ARCHITECTURE §5). Hybrid chunks remain the fallback when the
    articles table / index is missing.
    """
    where = _status_filter(include_all)
    hybrid_rows: list[dict] = []
    vector_rows: list[dict] = []

    if not vector_only:
        try:
            from lancedb.rerankers import RRFReranker
            search = (
                table.search(query_type="hybrid")
                .vector(qvec)
                .text(question)
                .rerank(reranker=RRFReranker())
                .metric("cosine")
            )
            if where:
                search = search.where(where)
            hybrid_rows = search.limit(k).to_list()
        except Exception as e:
            print(f"  (hybrid search unavailable — {e}; "
                  f"falling back to vector. Run: "
                  f"python ingest.py --build-fts)")

    if not hybrid_rows:
        search = table.search(qvec).metric("cosine")
        if where:
            search = search.where(where)
        vector_rows = search.limit(k).to_list()

    base = hybrid_rows or vector_rows

    # Routing signals (phrases / seeds / cards / title vectors)
    law_ids: list[int] = []
    signals: dict = {
        "phrase_ids": [], "seed_ids": [], "card_ids": [],
        "card_alias_hit": False, "alias_len": 0,
        "vector_ranked": [], "citation_law_ids": [],
    }
    try:
        import lancedb as _ldb
        db = _ldb.connect(str(DB_DIR))
        law_ids, _conf, signals = _route_law_ids(db, qvec, question, limit=8)
    except Exception as e:
        print(f"  (routing skipped — {e})")
        db = None

    plan = plan_query(
        question,
        k=k,
        phrase_ids=signals.get("phrase_ids") or [],
        seed_ids=(signals.get("seed_ids") or []) + (signals.get("card_ids") or []),
        vector_ranked=signals.get("vector_ranked") or [],
        citation_law_ids=signals.get("citation_law_ids") or [],
        card_alias_hit=bool(signals.get("card_alias_hit")),
        alias_len=int(signals.get("alias_len") or 0),
    )

    # Hard scope for citation-key shape
    scope_ids = list(plan.scope_doc_ids) or list(law_ids)
    if plan.shape == Shape.CITATION_KEY and plan.scope_doc_ids:
        scope_ids = list(plan.scope_doc_ids)

    art_label = plan.article_label

    # --- article_exact / defining_articles (prefer defines) --------------
    article_exact: list[dict] = []
    defining_articles: list[dict] = []
    if art_label:
        # Scope exact defines to routed laws when available. Previously
        # EXACT_ARTICLE forced law_ids=None, so art=75 from empty-title /
        # unrelated statutes crowded out قانون العمل.
        scoped = scope_ids[:8] if scope_ids else None
        article_exact = _article_exact_from_index(
            art_label,
            law_ids=scoped,
            include_all=include_all,
            limit=k,
        )
        if not article_exact and scoped:
            loose = _article_exact_from_index(
                art_label,
                law_ids=None,
                include_all=include_all,
                limit=max(k * 3, 12),
            )
            # Named instrument in the question → drop unrelated art=N hits
            article_exact = prefer_instrument_titled_rows(
                loose, question, hard=True,
            )
        else:
            article_exact = prefer_instrument_titled_rows(
                article_exact, question,
            )
        if db is not None:
            vect_arts = _search_articles_table(
                db, qvec, question,
                where=where, law_ids=scoped,
                article_label=art_label, limit=k,
            )
            if not vect_arts and scoped:
                vect_arts = prefer_instrument_titled_rows(
                    _search_articles_table(
                        db, qvec, question,
                        where=where, law_ids=None,
                        article_label=art_label, limit=max(k * 2, 8),
                    ),
                    question,
                    hard=True,
                )
            # Prefer index defines first, then vector article hits
            seen = {r.get("chunk_id") for r in article_exact}
            for r in vect_arts:
                if r.get("chunk_id") not in seen:
                    article_exact.append(r)
                    seen.add(r.get("chunk_id"))
        # Chunk fallback when no defines (legacy article_nums)
        if not article_exact:
            has_article_col = "article_nums" in {f.name for f in table.schema}
            if has_article_col:
                art_where = f"article_nums LIKE '%,{art_label},%'"
                if where:
                    art_where = f"({where}) AND ({art_where})"
                try:
                    loose = (
                        table.search(qvec).metric("cosine")
                        .where(art_where, prefilter=True)
                        .limit(max(k * 5, 20))
                        .to_list()
                    )
                except Exception:
                    loose = []
                titled = prefer_instrument_titled_rows(
                    loose, question, hard=True,
                )
                scoped_rows = prefer_law_id_rows(
                    titled or loose, scoped, hard=bool(scoped),
                )
                article_exact = (scoped_rows or titled or loose)[:k]

    if plan.shape == Shape.DEFINITIONAL and db is not None:
        defining_articles = _search_articles_table(
            db, qvec, question,
            where=where, law_ids=scope_ids[:6] if scope_ids else None,
            article_label=None, limit=max(k, 4),
            defining_only_low=True,
        )
        if not defining_articles and scope_ids:
            # Index fallback: arts 1–3 for scoped laws
            for lab in ("1", "2", "3"):
                defining_articles.extend(_article_exact_from_index(
                    lab, law_ids=scope_ids[:4], include_all=include_all, limit=2,
                ))

    title_rows = _title_match_rows(table, qvec, question, where, limit=k)

    routed_rows: list[dict] = []
    card_route_rows: list[dict] = []
    if scope_ids:
        try:
            routed_rows = _chunks_for_laws(
                table, qvec, scope_ids, where,
                question=question,
                per_law=3, total_cap=max(k, 10),
            )
            if is_overview_question(question):
                routed_rows.sort(key=_overview_rank)
            # Prefer article vectors inside scoped laws when available
            if db is not None:
                art_scoped = _search_articles_table(
                    db, qvec, question,
                    where=where, law_ids=scope_ids[:6],
                    article_label=None, limit=max(k, 6),
                )
                if art_scoped:
                    # Prepend article hits into law_scoped stream (deduped in fuse)
                    routed_rows = art_scoped + routed_rows
        except Exception as e:
            print(f"  (law-scoped pull skipped — {e})")

    # card_route leg: laws matched only via law_cards aliases
    card_only = signals.get("card_ids") or []
    if card_only and db is not None:
        try:
            card_route_rows = _chunks_for_laws(
                table, qvec, card_only[:4], where,
                question=question, per_law=2, total_cap=4,
            )
        except Exception:
            card_route_rows = []

    leg_hits: dict[str, list[dict]] = {
        "hybrid": base,
        "article_exact": article_exact,
        "defining_articles": defining_articles,
        "law_scoped": routed_rows,
        "title_like": title_rows,
        "card_route": card_route_rows,
    }

    fused = fuse_legs(
        leg_hits,
        plan,
        k=k,
        guaranteed_law_ids=scope_ids[:4] if plan.shape == Shape.MULTI_INSTRUMENT else None,
    )
    if fused:
        return fused
    # Absolute fallback: old order-based merge
    return _merge_rows(article_exact, base, title_rows, routed_rows, limit=k)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="*", help="the legal question")
    ap.add_argument("--k", type=int, default=TOP_K,
                    help="how many chunks to retrieve")
    ap.add_argument("--all", action="store_true",
                    help="include repealed laws (default: in-force only)")
    ap.add_argument("--answer-model", default=ANSWER_MODEL_OR,
                    help=f"OpenRouter model slug (default: {ANSWER_MODEL_OR}). "
                         "Verify slugs at https://openrouter.ai/models")
    ap.add_argument("--compare", action="store_true",
                    help="run the same question through several models so you "
                         "can judge Arabic legal quality side by side")
    ap.add_argument("--show-chunks", action="store_true",
                    help="print the full retrieved text, not just titles. USE "
                         "THIS while judging retrieval — the answer reads fine "
                         "even when the wrong law was retrieved.")
    ap.add_argument("--local-embed", action="store_true",
                    help="embed the question on the local GPU instead of the "
                         "API. Only if the store was built WITHOUT --api.")
    ap.add_argument("--list-models", action="store_true",
                    help="fetch OpenRouter's LIVE model catalog and print "
                         "cheap chat-capable models, then exit. Use this "
                         "whenever a model slug stops working — slugs get "
                         "renamed/delisted regularly.")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the citation-verification pass (saves one "
                         "cheap call per question). Verification is ON by "
                         "default — it catches article numbers or law titles "
                         "in the answer that don't actually appear in the "
                         "retrieved context.")
    ap.add_argument("--detailed", action="store_true",
                    help="exhaustively enumerate every clause with an "
                         "individual citation, instead of the default concise "
                         "grouped-by-outcome summary. Produces much longer, "
                         "more expensive answers — use for broad questions "
                         "like penalty ranges only when you actually want the "
                         "full enumeration.")
    ap.add_argument("--vector-only", action="store_true",
                    help="disable hybrid BM25+vector retrieval; use pure "
                         "vector search only (old behaviour).")
    ap.add_argument("--no-cache", action="store_true",
                    help="skip the on-disk answer cache (force a fresh "
                         "generation even if this question+chunks was "
                         "answered before).")
    ap.add_argument("--no-cards", action="store_true",
                    help="skip law_cards/alias_lexicon in routing (A/B vs "
                         "seed aliases + instrument phrases only). Same as "
                         "IRAQI_RAG_NO_CARDS=1.")
    args = ap.parse_args()

    if args.no_cards:
        set_use_law_cards(False)

    if args.list_models:
        import requests
        or_key = os.environ.get("OPENROUTER_API_KEY")
        if not or_key:
            sys.exit('Set OPENROUTER_API_KEY first.')
        r = requests.get(OPENROUTER_MODELS_URL,
                          headers={"Authorization": f"Bearer {or_key}"}, timeout=30)
        r.raise_for_status()
        models = r.json().get("data", [])
        # filter to text-output chat models, sort by prompt price ascending
        rows = []
        for m in models:
            mid = m.get("id", "?")
            # Exclude OpenRouter's own routing/utility meta-endpoints
            # (auto-router, body-builder, etc.) — not real completion models
            # you'd point this RAG loop at.
            if mid.startswith("openrouter/"):
                continue
            arch = m.get("architecture") or {}
            if "text" not in (arch.get("output_modalities") or []):
                continue
            pricing = m.get("pricing") or {}
            try:
                prompt_price = float(pricing.get("prompt") or 0) * 1_000_000
            except (TypeError, ValueError):
                prompt_price = 0
            # Some entries report a negative sentinel (e.g. -1) meaning
            # "dynamic/not directly quotable" rather than an actual price —
            # exclude rather than let them sort to the (fake) cheapest spot.
            if prompt_price < 0:
                continue
            rows.append((prompt_price, mid))
        rows.sort()
        print(f"{'model id':55} {'$/M input tok':>15}")
        print("-" * 72)
        for price, mid in rows[:40]:
            print(f"{mid:55} {price:>15.4f}")
        print(f"\n({len(rows)} text-output chat models total. Cheapest 40 shown.)")
        print("Update ANSWER_MODEL_CANDIDATES in common.py with whatever's current.")
        return


    if not DB_DIR.exists():
        sys.exit(f"No vector store at {DB_DIR}. Run ingest.py first.")

    import requests

    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        sys.exit('Set OPENROUTER_API_KEY first:\n'
                 '  $env:OPENROUTER_API_KEY = "sk-or-v1-..."')

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
    })
    meter = UsageMeter()
    cache = AnswerCache()

    # --- question embedding: MUST match how the store was built ----------
    if args.local_embed:
        from sentence_transformers import SentenceTransformer
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _model = SentenceTransformer(EMBED_MODEL, device=device)
        _model.max_seq_length = MAX_SEQ_LEN
        if device == "cuda" and USE_FP16_ON_CUDA:
            _model.half()

        def embed_question(q: str) -> list[float]:
            v = _model.encode([q], normalize_embeddings=True)[0]
            return [float(x) for x in v]
    else:
        def embed_question(q: str) -> list[float]:
            r = session.post(
                OPENROUTER_URL,
                json={"model": OPENROUTER_EMBED_MODEL, "input": [q]},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
            # embeddings endpoint may also return usage
            meter.record("embed", OPENROUTER_EMBED_MODEL, data.get("usage"))
            return data["data"][0]["embedding"]

    def _chat(model_slug: str, system: str, user: str, max_tokens: int = 8000,
               exclude_reasoning: bool = True,
               kind: str = "answer") -> tuple[str, int, str | None]:
        """Low-level call: (text, effective_status, finish_reason).
        finish_reason is the API's own signal for why generation stopped —
        'stop' (complete), 'length' (hit max_tokens, i.e. TRUNCATED), or
        None if the call didn't get that far. Checking this instead of
        guessing from the text is how we tell a genuinely finished answer
        from one that got cut off mid-sentence."""
        r = session.post(
            OPENROUTER_CHAT_URL,
            json={
                "model": model_slug,
                "max_tokens": max_tokens,
                # Ask OpenRouter to return token counts + USD cost so we can
                # log real per-query spend (AGENTS.md §9 — instrument first).
                "usage": {"include": True},
                # Skip extended thinking by default (fast, cheap, and avoids
                # the empty-content bug some reasoning models have — see
                # exclude_reasoning param). Verification turns this OFF on
                # purpose: comparing a paraphrase against source text closely
                # is a harder task than fluent generation, and a cheap model
                # rushing through it produces exactly the kind of confused,
                # self-contradictory false-positive seen in testing.
                "reasoning": {"exclude": exclude_reasoning},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=180,
        )
        try:
            data = r.json()
        except Exception:
            return f"[non-JSON response, HTTP {r.status_code}]", r.status_code, None
        meter.record(kind, model_slug, data.get("usage"))
        if "choices" in data:
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content")
            finish_reason = choice.get("finish_reason")
            if content:
                return content, 200, finish_reason
            return "[empty response — likely all tokens spent on hidden " \
                   "reasoning, not the visible answer]", -1, finish_reason
        err = data.get("error", data)
        code = err.get("code") if isinstance(err, dict) else None
        effective = code if isinstance(code, int) else r.status_code
        return f"[API error] {err}", effective, None

    def call_model(model_slug: str, context: str, question: str) -> tuple[str, int, str | None]:
        system = DETAILED_SYSTEM_PROMPT if args.detailed else CONCISE_SYSTEM_PROMPT
        user = (
            f"النصوص القانونية المتاحة:\n\n{context}\n\nالسؤال: {question}\n\n"
            "(ابدأ جوابك بمضمون ما في المصادر أعلاه — العقوبة أو الحكم "
            "ورقم المادة إن وُجد. أي توضيح لحدود التغطية يأتي بعد ذلك فقط.)"
        )
        return _chat(model_slug, system, user, kind="answer")

    VERIFY_SYSTEM = ("أنت مدقق قانوني. مهمتك الوحيدة هي التحقق مما إذا كان "
                      "مضمون كل استشهاد (الحكم القانوني المنسوب لرقم مادة "
                      "معين) في نص معين يطابق فعلا مضمون تلك المادة في نصوص "
                      "مرجعية أخرى — وليس فقط أن رقم المادة مذكور في مكان ما. "
                      "لا تُبد رأيك القانوني، تحقق من التطابق فقط.")

    def verify_citations(rows: list[dict], answer: str,
                         model_slug: str) -> str | None:
        """
        Two-stage verification:
          1. Free regex gate — article numbers cited but absent from every
             retrieved chunk are fabrications (report, no model call).
          2. Model check — only against chunks that carry a cited article
             number, so we don't re-send the whole 6-chunk context.
        """
        fake = fabricated_citations(answer, rows)
        local_notes = []
        if fake:
            local_notes.append(
                "أرقام مواد مذكورة في الجواب وغير موجودة في أي مصدر مسترجع: "
                + "، ".join(fake)
            )

        cited_rows = chunks_for_cited_articles(answer, rows)
        # If every citation is a fabrication, skip the paid call.
        present_cited = set(article_nums_list(extract_article_numbers(answer))) - set(fake)
        if not present_cited:
            return "\n".join(local_notes) if local_notes else None

        context = build_context(cited_rows)
        user = f"""النصوص المرجعية (المصادر المسترجعة — المقتصرة على المواد المستشهد بها):
{context}

النص المطلوب التحقق من استشهاداته:
{answer}

لكل استشهاد برقم مادة ورد في النص أعلاه: تحقق هل مضمون تلك المادة في النصوص
المرجعية يطابق فعلا الحكم القانوني المنسوب إليها في النص، وليس فقط أن رقم
المادة مذكور في مكان ما. اقرأ نص المادة كاملا بعناية قبل الحكم.

أجب بهذا التنسيق فقط:
- إذا كانت كل الاستشهادات صحيحة (الرقم ومضمونها متطابقان مع المصدر)، اكتب كلمة واحدة فقط: سليم
- إذا وجدت استشهادا واحدا أو أكثر لا يتطابق مضمونه مع ما نُسب إليه، أو رقم مادة
  غير موجود إطلاقا، اكتب كل حالة بهذه الصيغة بالضبط (سطرين لكل حالة):
  [رقم المادة المذكور] — المشكلة: [وصف قصير جدا لسبب عدم التطابق]
  الدليل من المصدر: "[اقتبس هنا الجملة الفعلية من النص المرجعي التي تثبت وجود مشكلة]"
لا تكرر أو تسرد الاستشهادات الصحيحة إطلاقا — فقط الحالات المشكوك فيها. لا تُصدر
حكما بوجود مشكلة دون اقتباس الدليل الفعلي من النص المرجعي أولا."""
        text, status, _ = _chat(model_slug, VERIFY_SYSTEM, user, max_tokens=1200,
                                 exclude_reasoning=False, kind="verify")
        model_note = None
        if status == 200:
            stripped = text.strip()
            if not (stripped == "سليم" or stripped.startswith("سليم")):
                model_note = text
        parts = local_notes + ([model_note] if model_note else [])
        return "\n".join(parts) if parts else None

    def generate(preferred_slug: str, context: str, question: str) -> tuple[str, str | None]:
        """
        Try the preferred slug first, then fall through ANSWER_MODEL_CANDIDATES
        if it returns a bad-slug error OR an empty answer. Slugs churn on
        OpenRouter often enough — and reasoning models eat their own budget
        often enough — that a single hardcoded model shouldn't be a hard
        failure. Returns (text, finish_reason) so the caller can tell a
        complete answer from one that got cut off at max_tokens.
        """
        tried = []
        order = [preferred_slug] + [m for m in ANSWER_MODEL_CANDIDATES if m != preferred_slug]
        text, finish_reason = "", None
        for slug in order:
            text, status, finish_reason = call_model(slug, context, question)
            tried.append(slug)
            if status == 200:
                if slug != preferred_slug:
                    print(f"  (note: '{preferred_slug}' failed, used '{slug}' instead)")
                return text, finish_reason
            if status not in (400, -1):
                # not a "bad slug" or "empty answer" situation (e.g. rate
                # limit, no credit) — don't burn every candidate on the same
                # real problem
                return text, finish_reason
        return f"[all candidates failed: {tried}]\nLast error: {text}", finish_reason

    def looks_truncated(text: str) -> bool:
        """
        Heuristic fallback for when finish_reason itself doesn't reliably
        signal truncation (seen in practice — some providers/proxies don't
        normalize it consistently through OpenRouter). Not a certainty, just
        a second signal: real answers end in sentence-final punctuation;
        text cut off mid-clause usually doesn't.
        """
        t = text.strip()
        if not t:
            return False
        return t[-1] not in ".؟!\u061f\u06d4\":\u201d)\u201f»"

    db = lancedb.connect(str(DB_DIR))
    table = db.open_table(TABLE_NAME)

    def answer_one(question: str):
        meter.calls.clear()
        qvec = embed_question(question)
        rows = retrieve(table, qvec, question, args.k, args.all, args.vector_only)
        if not rows:
            print("لا توجد نتائج مطابقة في قاعدة البيانات.")
            return

        # Sources FIRST. This is the part you are actually evaluating right
        # now — a fluent answer over the wrong laws is worse than no answer.
        print("\n" + "=" * 70)
        print("المصادر المسترجعة (اقرأها أولاً — هل هي القوانين الصحيحة؟)")
        print("=" * 70)
        amended_titles: list[str] = []
        for i, r in enumerate(rows, 1):
            status = r.get("status_label") or ""
            flag = r.get("law_flag") or ""
            mark = "" if status == "ساري" else f"  [{status}]"
            if flag in ("معدل", "تعديل"):
                mark += f"  ⚠[{flag}]"
                title = (r.get("title") or "")[:75]
                if title and title not in amended_titles:
                    amended_titles.append(title)
            dist = r.get("_distance")
            rel = r.get("_relevance_score")
            if isinstance(rel, float):
                sc = f"  (rrf {rel:.4f})"
            elif isinstance(dist, float):
                sc = f"  (distance {dist:.4f})"
            else:
                sc = ""
            arts = r.get("article_nums") or ""
            art_note = ""
            if arts:
                shown = arts.strip(",").replace(",", "، ")
                art_note = f"  [مواد: {shown}]"
            print(f"\n  {i}. {r.get('title','')[:75]} ({r.get('year','')}){mark}{sc}{art_note}")
            print(f"     {r.get('source_url','')}")
            if args.show_chunks:
                for line in (r.get("text") or "").strip().splitlines():
                    print(f"       | {line}")

        if amended_titles:
            print("\n⚠ تنبيه: بعض المصادر المسترجعة معلّمة كتعديل/معدّل. "
                  "نص المادة في قاعدة البيانات قد يسبق تعديلا لاحقا — "
                  "تحقق من الجريدة الرسمية قبل الاعتماد:")
            for t in amended_titles:
                print(f"   • {t}")

        # --- Exact-article fast path: no generation, no verify ----------
        # Prefer article_index *defines* (or articles table) over fat chunks.
        art = parse_article_query(question)
        if art and is_exact_lookup_question(question):
            exact = [
                r for r in rows
                if art in _row_article_set(r)
                or str(r.get("article_label") or "") == str(art)
            ]
            # Prefer granularity=article / role=defines
            exact.sort(
                key=lambda r: (
                    0 if r.get("granularity") == "article" or r.get("role") == "defines" else 1,
                    0 if str(r.get("article_label") or "") == str(art) else 1,
                )
            )
            if exact:
                print("\n" + "=" * 70)
                print(f"نص المادة {art} (استرجاع مباشر — بدون توليد)")
                print("=" * 70)
                for r in exact[:3]:
                    print(f"\n— {r.get('title','')} ({r.get('year','')})")
                    print(r.get("source_url") or "")
                    print((r.get("text") or "").strip())
                print("\n" + "-" * 70)
                print("⚠ معلومات قانونية عامة وليست استشارة قانونية. "
                      "يرجى مراجعة محامٍ مختص بشأن حالتك الخاصة.")
                print(f"\n[{meter.summary_line()}]")
                return

        context = build_context(rows)
        models = ANSWER_MODEL_CANDIDATES if args.compare else [args.answer_model]

        for slug in models:
            print("\n" + "=" * 70)
            print(f"الجواب — {slug}")
            print("=" * 70)

            ckey = answer_cache_key(question, rows, slug, args.detailed)
            cached = None if args.no_cache else cache.get(ckey)
            if cached:
                answer_text = cached["answer"]
                finish_reason = "stop"
                print(f"  (cache hit — skipped generation)")
                print(answer_text)
            else:
                try:
                    answer_text, finish_reason = generate(slug, context, question)
                    print(answer_text)
                except Exception as e:
                    print(f"[failed: {e}]")
                    continue
                if (not answer_text.startswith("[")
                        and not args.compare
                        and not args.no_cache):
                    cache.put(ckey, answer_text, slug, question)

            # Different providers don't all normalize this field the same
            # way through OpenRouter — check case-insensitively against known
            # "hit the token ceiling" values rather than an exact string.
            reported_truncated = (finish_reason or "").strip().lower() in ("length", "max_tokens")
            if reported_truncated:
                print("\n⚠ الجواب طويل جدا ولم يكتمل ضمن الحد الأقصى للنص. "
                      "لإجابة كاملة، جرّب سؤالا أكثر تحديدا (مثلا حدد ظرفا "
                      "معينا بدل السؤال العام) أو استخدم --k أقل لتقليل عدد "
                      "المصادر المسترجعة.")
            elif not answer_text.startswith("[") and looks_truncated(answer_text):
                # finish_reason didn't clearly say "length", but the text
                # doesn't end on a sentence boundary either — softer wording
                # since this is a heuristic guess, not a confirmed signal.
                print("\n⚠ قد لا يكون الجواب مكتملا (لا ينتهي بعلامة ترقيم "
                      "واضحة). جرّب سؤالا أكثر تحديدا إن بدا الجواب ناقصا.")

            # Guaranteed disclaimer — printed regardless of whether the model
            # remembered to include the one from SYSTEM_PROMPT. Never rely on
            # an LLM to reliably self-police this.
            print("\n" + "-" * 70)
            print("⚠ معلومات قانونية عامة وليست استشارة قانونية. "
                  "يرجى مراجعة محامٍ مختص بشأن حالتك الخاصة.")

            if not args.no_verify and not answer_text.startswith("[") and not cached:
                warning = verify_citations(rows, answer_text, slug)
                if warning:
                    print("\n⚠ تنبيه من التحقق الآلي — استشهاد قد لا يكون "
                          "مدعوما بالكامل من المصادر المسترجعة:")
                    print(f"   {warning}")

            print(f"\n[{meter.summary_line()}]")

    if args.question:
        answer_one(" ".join(args.question))
    else:
        print("اطرح سؤالك القانوني (اكتب 'خروج' للإنهاء):")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q in ("خروج", "exit", "quit", ""):
                break
            answer_one(q)


if __name__ == "__main__":
    main()
