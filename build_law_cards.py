#!/usr/bin/env python3
"""
build_law_cards.py — one JSON-constrained OpenRouter call per in-force law.

Produces:
  cache/law_cards.jsonl      — scope / tags / aliases / likely questions
  cache/alias_lexicon.jsonl  — alias → law_book_id feed for routing

CRITICAL: these artifacts are for routing/UI only. Never inject cards into
the answer LLM context (see law_cards.py / docs/ARCHITECTURE.md).

Resumable by law_book_id (skips ids already in the cards file).

    python build_law_cards.py --sample
    python build_law_cards.py --limit 20
    python build_law_cards.py --priority --limit 100
    python build_law_cards.py --rebuild-lexicon-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from common import (
    ROOT,
    SAMPLE_LAWS,
    ANSWER_MODEL_CANDIDATES,
    OPENROUTER_CHAT_URL,
    default_corpus_path,
    is_priority,
    load_dotenv,
)
from law_cards import (
    ALIAS_LEXICON_FILE,
    CARD_SYSTEM_PROMPT,
    LAW_CARD_JSON_SCHEMA,
    LAW_CARDS_FILE,
    append_law_card,
    build_card_user_prompt,
    existing_card_ids,
    load_law_cards,
    parse_card_payload,
    save_alias_lexicon,
)
from law_registry import iter_law_records

# Prefer sibling private corpus on this machine when present.
_SIBLING_MASTER = Path(r"C:\iraqi-law-rag\sources\laws_master.jsonl")
_SIBLING_ENV = Path(r"C:\iraqi-law-rag\.env")


def _bootstrap_env() -> None:
    load_dotenv()
    load_dotenv(ROOT / ".env")
    if _SIBLING_ENV.exists():
        load_dotenv(_SIBLING_ENV)


_bootstrap_env()


def _log(msg: str) -> None:
    print(msg, flush=True)


def resolve_source(*, sample: bool, source: str | None) -> Path:
    if sample:
        return SAMPLE_LAWS
    if source:
        return Path(source)
    if _SIBLING_MASTER.exists():
        return _SIBLING_MASTER
    local_master = ROOT / "sources" / "laws_master.jsonl"
    if local_master.exists():
        return local_master
    return default_corpus_path()


def collect_candidates(
    source: Path,
    *,
    priority: bool,
    limit: int,
    sari_only: bool = True,
) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for rec in iter_law_records(source):
        if sari_only and (rec.get("status_label") or "").strip() != "ساري":
            continue
        if not (rec.get("full_text") or "").strip():
            continue
        if priority and not is_priority(rec):
            continue
        lid = int(rec.get("lawBookID") or 0)
        if not lid or lid in seen:
            continue
        seen.add(lid)
        out.append(rec)
        if limit and len(out) >= limit:
            break
    return out


def _is_bad_model_err(err: object) -> bool:
    s = str(err).lower()
    return "not a valid model" in s or "invalid model" in s or "no endpoints" in s


def _chat_json(
    session: requests.Session,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 700,
) -> tuple[str, dict | None, str | None]:
    """
    Returns (content, usage_dict_or_None, error_message_or_None).
    Retries on 429 / transient errors with exponential backoff.
    """
    last_err = None
    for attempt in range(7):
        try:
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "usage": {"include": True},
                "reasoning": {"exclude": True},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "law_card",
                        "strict": True,
                        "schema": LAW_CARD_JSON_SCHEMA,
                    },
                },
            }
            r = session.post(OPENROUTER_CHAT_URL, json=payload, timeout=120)
            if r.status_code == 429:
                wait = min(2 ** attempt, 45)
                _log(f"  429 rate limit — sleep {wait}s")
                time.sleep(wait)
                continue
            try:
                data = r.json()
            except Exception:
                last_err = f"non-JSON HTTP {r.status_code}"
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code >= 400 or "error" in data:
                err = data.get("error", data)
                last_err = err
                if _is_bad_model_err(err):
                    return "", None, f"bad_model:{err}"
                if r.status_code in (500, 502, 503):
                    time.sleep(min(2 ** attempt, 30))
                    continue
                return "", data.get("usage"), f"http_{r.status_code}:{err}"
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if not content.strip():
                last_err = "empty content"
                time.sleep(min(2 ** attempt, 15))
                continue
            return content, data.get("usage"), None
        except requests.RequestException as e:
            last_err = e
            time.sleep(min(2 ** attempt, 20))
    return "", None, f"failed after retries: {last_err}"


def generate_card(
    session: requests.Session,
    rec: dict,
    models: list[str],
) -> tuple[dict | None, float, int, int]:
    """
    Returns (card_or_None, cost_usd, prompt_tokens, completion_tokens).
    """
    lid = int(rec.get("lawBookID") or 0)
    title = (rec.get("lawTitle") or "").strip()
    user = build_card_user_prompt(rec)
    cost = 0.0
    pin = pout = 0
    for model in models:
        content, usage, err = _chat_json(
            session, model=model, system=CARD_SYSTEM_PROMPT, user=user,
        )
        if usage:
            cost += float(usage.get("cost") or 0)
            pin += int(usage.get("prompt_tokens") or 0)
            pout += int(usage.get("completion_tokens") or 0)
        if err and str(err).startswith("bad_model:"):
            _log(f"  model {model} unavailable — trying next")
            continue
        if err:
            _log(f"  error on {model}: {err}")
            continue
        try:
            card = parse_card_payload(content, law_book_id=lid, title=title)
            card["model"] = model
            return card, cost, pin, pout
        except (json.JSONDecodeError, ValueError) as e:
            _log(f"  parse fail on {model}: {e}")
            continue
    return None, cost, pin, pout


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build LLM law cards + alias lexicon (routing/UI only)."
    )
    ap.add_argument("--source", default=None,
                    help="JSONL corpus (default: sibling laws_master or local)")
    ap.add_argument("--sample", action="store_true",
                    help="use sources/sample_laws.jsonl")
    ap.add_argument("--priority", action="store_true",
                    help="major codes + recent in-force only (is_priority)")
    ap.add_argument("--limit", type=int, default=0,
                    help="max laws to process this run")
    ap.add_argument("--out", default=str(LAW_CARDS_FILE),
                    help="cards JSONL path")
    ap.add_argument("--lexicon-out", default=str(ALIAS_LEXICON_FILE),
                    help="alias lexicon JSONL path")
    ap.add_argument("--sleep", type=float, default=0.15,
                    help="seconds between successful API calls")
    ap.add_argument("--dry-run", action="store_true",
                    help="list candidates only; no API calls")
    ap.add_argument("--rebuild-lexicon-only", action="store_true",
                    help="rewrite alias lexicon from existing cards; no API")
    ap.add_argument("--model", default=None,
                    help="OpenRouter model slug (default: cheap fallback chain)")
    args = ap.parse_args()

    cards_path = Path(args.out)
    lex_path = Path(args.lexicon_out)

    if args.rebuild_lexicon_only:
        n = save_alias_lexicon(path=lex_path, cards_path=cards_path)
        _log(f"Rebuilt lexicon: {n} aliases from {cards_path} -> {lex_path}")
        return

    source = resolve_source(sample=args.sample, source=args.source)
    if not source.exists():
        sys.exit(f"Source not found: {source}")

    candidates = collect_candidates(
        source, priority=args.priority, limit=args.limit,
    )
    done = existing_card_ids(cards_path)
    todo = [r for r in candidates if int(r.get("lawBookID") or 0) not in done]
    already = len(candidates) - len(todo)

    _log(f"Source: {source}")
    _log(
        f"Candidates: {len(candidates)}  already done: {already}  "
        f"todo: {len(todo)}"
    )

    if args.dry_run:
        for r in todo[:50]:
            _log(f"  {r.get('lawBookID')}: {r.get('lawTitle')}")
        if len(todo) > 50:
            _log(f"  … and {len(todo) - 50} more")
        return

    if not todo:
        n = save_alias_lexicon(path=lex_path, cards_path=cards_path)
        _log(f"Nothing to do. Lexicon rows: {n}")
        return

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set (load .env or sibling iraqi-law-rag/.env)")

    models = [args.model] if args.model else list(ANSWER_MODEL_CANDIDATES)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/sohaibbabajan/iraqi-legislation-rag",
        "X-Title": "iraqi-legislation-rag law cards",
    })

    written = 0
    failed = 0
    total_cost = 0.0
    total_in = 0
    total_out = 0
    t0 = time.time()

    for i, rec in enumerate(todo, 1):
        lid = int(rec.get("lawBookID") or 0)
        title = (rec.get("lawTitle") or "")[:60]
        _log(f"[{i}/{len(todo)}] {lid} {title}")
        card, cost, pin, pout = generate_card(session, rec, models)
        total_cost += cost
        total_in += pin
        total_out += pout
        if card is None:
            failed += 1
            _log("  FAILED")
            continue
        append_law_card(card, cards_path)
        written += 1
        n_alias = len(card.get("colloquial_aliases") or [])
        _log(
            f"  ok tags={len(card['subject_tags'])} aliases={n_alias} "
            f"cost=${cost:.6f} tok={pin}/{pout}"
        )
        if args.sleep > 0:
            time.sleep(args.sleep)

    n_lex = save_alias_lexicon(path=lex_path, cards_path=cards_path)
    elapsed = time.time() - t0
    total_cards = len(load_law_cards(cards_path))
    _log(
        f"Done. wrote={written} failed={failed} cards_file={total_cards} "
        f"lexicon_rows={n_lex} tokens={total_in}/{total_out} "
        f"cost=${total_cost:.6f} elapsed={elapsed:.1f}s"
    )
    _log(
        "Resume full corpus: python build_law_cards.py "
        "(skips law_book_ids already in cache/law_cards.jsonl)"
    )


if __name__ == "__main__":
    main()
