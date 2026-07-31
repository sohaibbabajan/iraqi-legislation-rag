"""
rag_service.py — shared retrieve/answer engine for CLI + web.

Keeps per-query cost levers in one place (cache, cheap verify, exact lookup).
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any

import lancedb
import requests

from common import (
    DB_DIR, TABLE_NAME,
    OPENROUTER_URL, OPENROUTER_EMBED_MODEL,
    OPENROUTER_CHAT_URL, ANSWER_MODEL_OR, ANSWER_MODEL_CANDIDATES,
    parse_article_query, extract_article_numbers, article_nums_list,
    is_exact_lookup_question, load_dotenv,
)
from ask import (
    TOP_K, CONCISE_SYSTEM_PROMPT, DETAILED_SYSTEM_PROMPT,
    UsageMeter, AnswerCache, answer_cache_key,
    build_context, retrieve, fabricated_citations, chunks_for_cited_articles,
    _row_article_set,
)

load_dotenv()


def _looks_truncated(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return t[-1] not in ".؟!\u061f\u06d4\":\u201d)\u201f»"


DISCLAIMER_AR = (
    "⚠ معلومات قانونية عامة وليست استشارة قانونية. "
    "يرجى مراجعة محامٍ مختص بشأن حالتك الخاصة."
)
DISCLAIMER_EN = (
    "⚠ General legal information, not legal advice. "
    "Consult a qualified lawyer for your own situation."
)


@dataclass
class AskResult:
    question: str
    sources: list[dict]
    answer: str | None
    mode: str  # "lookup" | "generated" | "cached" | "empty"
    finish_reason: str | None = None
    verify_warning: str | None = None
    truncated: bool = False
    amended_titles: list[str] = field(default_factory=list)
    amendment_links: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_line: str = ""
    cost: dict = field(default_factory=dict)
    lang: str = "ar"


class RagEngine:
    """One OpenRouter session + one LanceDB table. Safe to reuse across requests."""

    def __init__(self):
        if not DB_DIR.exists():
            raise FileNotFoundError(f"No vector store at {DB_DIR}. Run ingest.py first.")
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError(
                'Set OPENROUTER_API_KEY first:\n'
                '  $env:OPENROUTER_API_KEY = "sk-or-v1-..."'
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })
        self.db = lancedb.connect(str(DB_DIR))
        self.table = self.db.open_table(TABLE_NAME)
        self.cache = AnswerCache()
        self.meter = UsageMeter()

    def embed(self, text: str) -> list[float]:
        r = self.session.post(
            OPENROUTER_URL,
            json={"model": OPENROUTER_EMBED_MODEL, "input": [text]},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        self.meter.record("embed", OPENROUTER_EMBED_MODEL, data.get("usage"))
        return data["data"][0]["embedding"]

    def _chat(self, model_slug: str, system: str, user: str,
              max_tokens: int = 8000, exclude_reasoning: bool = True,
              kind: str = "answer",
              response_format: dict | None = None) -> tuple[str, int, str | None]:
        payload: dict[str, Any] = {
            "model": model_slug,
            "max_tokens": max_tokens,
            "usage": {"include": True},
            "reasoning": {"exclude": exclude_reasoning},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        r = self.session.post(
            OPENROUTER_CHAT_URL,
            json=payload,
            timeout=180,
        )
        try:
            data = r.json()
        except Exception:
            return f"[non-JSON response, HTTP {r.status_code}]", r.status_code, None
        self.meter.record(kind, model_slug, data.get("usage"))
        if "choices" in data:
            choice = data["choices"][0]
            content = choice.get("message", {}).get("content")
            finish_reason = choice.get("finish_reason")
            if content:
                return content, 200, finish_reason
            return ("[empty response — likely all tokens spent on hidden "
                    "reasoning, not the visible answer]"), -1, finish_reason
        err = data.get("error", data)
        code = err.get("code") if isinstance(err, dict) else None
        effective = code if isinstance(code, int) else r.status_code
        return f"[API error] {err}", effective, None

    def generate(self, preferred_slug: str, context: str, question: str,
                 detailed: bool = False, lang: str = "ar") -> tuple[str, str | None]:
        system = DETAILED_SYSTEM_PROMPT if detailed else CONCISE_SYSTEM_PROMPT
        if lang == "en":
            system = (
                system
                + "\n\nLanguage: Answer in clear English. Keep Iraqi law titles "
                "and article numbers exactly as they appear in the sources "
                "(Arabic is fine for citations). Still ground every point in "
                "the provided texts only. Lead with what the sources do say; "
                "if the question is narrower/broader than the texts, state the "
                "scope gap in one sentence after the substance — do not open "
                "with a refusal when related provisions are present."
            )
        user = f"النصوص القانونية المتاحة:\n\n{context}\n\nالسؤال: {question}"
        if lang == "en":
            user += (
                "\n\n(Respond in English as instructed. Lead with what the "
                "sources say; scope caveats only after the substance.)"
            )
        else:
            user += (
                "\n\n(ابدأ جوابك بمضمون ما في المصادر أعلاه — العقوبة أو الحكم "
                "ورقم المادة إن وُجد. أي توضيح لحدود التغطية يأتي بعد ذلك فقط.)"
            )
        order = [preferred_slug] + [m for m in ANSWER_MODEL_CANDIDATES if m != preferred_slug]
        text, finish_reason = "", None
        tried = []
        for slug in order:
            text, status, finish_reason = self._chat(slug, system, user, kind="answer")
            tried.append(slug)
            if status == 200:
                return text, finish_reason
            if status not in (400, -1):
                return text, finish_reason
        return f"[all candidates failed: {tried}]\nLast error: {text}", finish_reason

    def verify(self, rows: list[dict], answer: str, model_slug: str) -> str | None:
        fake = fabricated_citations(answer, rows)
        local_notes = []
        if fake:
            local_notes.append(
                "أرقام مواد مذكورة في الجواب وغير موجودة في أي مصدر مسترجع: "
                + "، ".join(fake)
            )
        present_cited = (
            set(article_nums_list(extract_article_numbers(answer))) - set(fake)
        )
        if not present_cited:
            return "\n".join(local_notes) if local_notes else None

        cited_rows = chunks_for_cited_articles(answer, rows)
        context = build_context(cited_rows)
        verify_system = (
            "أنت مدقق قانوني. مهمتك الوحيدة هي التحقق مما إذا كان "
            "مضمون كل استشهاد في النص يطابق فعلا مضمون المادة في النصوص "
            "المرجعية. لا تُبد رأيا قانونيا — تحقق من التطابق فقط."
        )
        user = f"""النصوص المرجعية:
{context}

النص:
{answer}

أجب بكلمة سليم فقط إن كانت كل الاستشهادات صحيحة، وإلا اذكر المشكلة مع اقتباس."""
        text, status, _ = self._chat(
            model_slug, verify_system, user, max_tokens=1200,
            exclude_reasoning=False, kind="verify",
        )
        model_note = None
        if status == 200:
            stripped = text.strip()
            if not (stripped == "سليم" or stripped.startswith("سليم")):
                model_note = text
        parts = local_notes + ([model_note] if model_note else [])
        return "\n".join(parts) if parts else None

    def _source_view(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            arts = r.get("article_nums") or ""
            out.append({
                "chunk_id": r.get("chunk_id"),
                "title": r.get("title") or "",
                "year": r.get("year") or "",
                "status_label": r.get("status_label") or "",
                "law_flag": r.get("law_flag") or "",
                "source_url": r.get("source_url") or "",
                "article_nums": arts.strip(","),
                "text": r.get("text") or "",
                "_distance": r.get("_distance"),
                "_relevance_score": r.get("_relevance_score"),
            })
        return out

    def ask(
        self,
        question: str,
        *,
        k: int = TOP_K,
        include_all: bool = False,
        vector_only: bool = False,
        model: str = ANSWER_MODEL_OR,
        detailed: bool = False,
        verify: bool = True,
        use_cache: bool = True,
        show_full_text: bool = False,
        lang: str = "ar",
    ) -> AskResult:
        self.meter.calls.clear()
        qvec = self.embed(question)
        rows = retrieve(self.table, qvec, question, k, include_all, vector_only)

        def _pack(**kwargs) -> AskResult:
            report = self.meter.cost_report(lang=lang)
            return AskResult(
                cost_line=report["line"],
                cost=report,
                usage={"calls": list(self.meter.calls), **{k: report[k] for k in (
                    "usd", "usd_x10", "usd_x100", "cents", "cents_x10", "cents_x100",
                    "prompt_tokens", "completion_tokens", "cached",
                ) if k in report}},
                lang=lang,
                **kwargs,
            )

        if not rows:
            return _pack(
                question=question, sources=[], answer=None, mode="empty",
            )

        amended = []
        amendment_links: list[dict] = []
        try:
            from amendment_links import get_amendment_index
            _am_idx = get_amendment_index()
            amendment_links = _am_idx.warning_entries(rows)
            for e in amendment_links:
                t = (e.get("title") or "")[:75]
                if t and t not in amended:
                    amended.append(t)
        except Exception:
            for r in rows:
                if (r.get("law_flag") or "") in ("معدل", "تعديل"):
                    t = (r.get("title") or "")[:75]
                    if t and t not in amended:
                        amended.append(t)

        # Exact-article lookup — prefer article defines over fat chunks
        art = parse_article_query(question)
        if art and is_exact_lookup_question(question):
            exact = [
                r for r in rows
                if art in _row_article_set(r)
                or str(r.get("article_label") or "") == str(art)
            ]
            exact.sort(
                key=lambda r: (
                    0 if r.get("granularity") == "article" or r.get("role") == "defines" else 1,
                    0 if str(r.get("article_label") or "") == str(art) else 1,
                )
            )
            if exact:
                parts = []
                for r in exact[:3]:
                    parts.append(
                        f"— {r.get('title','')} ({r.get('year','')})\n"
                        f"{r.get('source_url') or ''}\n"
                        f"{(r.get('text') or '').strip()}"
                    )
                answer = "\n\n".join(parts)
                sources = self._source_view(exact[:3] if not show_full_text else exact[:3])
                if not show_full_text:
                    for s in sources:
                        s["text"] = (s["text"] or "")[:500]
                return _pack(
                    question=question,
                    sources=sources,
                    answer=answer,
                    mode="lookup",
                    amended_titles=amended,
                    amendment_links=amendment_links,
                )

        context = build_context(rows)
        ckey = answer_cache_key(question, rows, model, detailed, lang=lang)
        cached = self.cache.get(ckey) if use_cache else None
        finish_reason = None
        if cached:
            answer = cached["answer"]
            mode = "cached"
            finish_reason = "stop"
        else:
            answer, finish_reason = self.generate(
                model, context, question, detailed, lang=lang
            )
            mode = "generated"
            if not answer.startswith("[") and use_cache:
                self.cache.put(ckey, answer, model, question)

        reported_trunc = (finish_reason or "").strip().lower() in ("length", "max_tokens")
        heur_trunc = (not answer.startswith("[")) and _looks_truncated(answer)
        verify_warning = None
        if verify and mode == "generated" and not answer.startswith("["):
            verify_warning = self.verify(rows, answer, model)

        sources = self._source_view(rows)
        if not show_full_text:
            for s in sources:
                s["text"] = (s.get("text") or "")[:400]

        return _pack(
            question=question,
            sources=sources,
            answer=answer,
            mode=mode,
            finish_reason=finish_reason,
            verify_warning=verify_warning,
            truncated=reported_trunc or heur_trunc,
            amended_titles=amended,
            amendment_links=amendment_links,
        )

    def retrieve_only(self, question: str, k: int = 4,
                      include_all: bool = False) -> list[dict]:
        self.meter.calls.clear()
        qvec = self.embed(question)
        rows = retrieve(self.table, qvec, question, k, include_all, False)
        return self._source_view(rows)


_engine: RagEngine | None = None


def get_engine() -> RagEngine:
    global _engine
    if _engine is None:
        _engine = RagEngine()
    return _engine


def result_to_dict(res: AskResult) -> dict[str, Any]:
    disclaimer = DISCLAIMER_EN if res.lang == "en" else DISCLAIMER_AR
    return {
        "question": res.question,
        "sources": res.sources,
        "answer": res.answer,
        "mode": res.mode,
        "finish_reason": res.finish_reason,
        "verify_warning": res.verify_warning,
        "truncated": res.truncated,
        "amended_titles": res.amended_titles,
        "amendment_links": res.amendment_links,
        "disclaimer": disclaimer,
        "cost_line": res.cost_line,
        "cost": res.cost,
        "usage": res.usage,
        "lang": res.lang,
    }
