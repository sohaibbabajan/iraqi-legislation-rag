# AGENTS.md — Iraqi Legislation RAG (public toolkit)

## Permissions (blanket approval)

For work on this public Iraqi legislation toolkit / plan `public_iraqi_rag_toolkit`: proceed without asking the user to re-confirm routine permissions until revoked.

**Allowed without further confirmation:** edit project files; `git commit` and push to GitHub (including public repo `sohaibbabajan/iraqi-legislation-rag`); install deps; run tests; OpenRouter via existing `.env` / `OPENROUTER_API_KEY`; network for GitHub / Hugging Face / OpenRouter.

**Still never:** print or exfiltrate secrets; force-push `main`/`master`; commit `.env` or API keys.

See also `.cursor/rules/public-iraqi-rag-toolkit-permissions.mdc`.

## Model routing

Default to Auto / cheaper models for coding, tests, pushes, and overnight follow-ups. Escalate to Opus 5 (`claude-opus-5-thinking-high`) only for hard calls (architecture GO/NO-GO, spend ≳$5, legal/product scope, irreversible data strategy, resolving approach conflicts). After spend review agent `18516f77`, do not stay on expensive models for implementation unless the user asks or those criteria apply — follow Opus `SPEND_REVIEW` / architecture verdicts. See `.cursor/rules/model-routing.mdc`.

## Ported stack (OpenRouter-first)

- `setup_store.py` — ingest `--api` → FTS → `build_law_registry.py` → `build_article_index.py` → `embed_articles.py` → `scripts/verify_store.py`
- `build_law_cards.py` — optional P1 LLM cards + `alias_lexicon.jsonl` (routing/UI only; never answer context)
- Retrieval: `query_plan.py` quotas + article defines (`lancedb/articles`); hybrid chunks as fallback
- Optional: `cache/law_cards.jsonl` aliases via `law_registry.laws_matching_card_aliases`
- Default corpus: `laws_master.jsonl` if present, else `sample_laws.jsonl`
- Slim API: `GET /health`, `POST /api/ask` in `web/app.py`
- Do not dump private Masadir dad-demo / tunnel polish here
- Never commit `.env`, `lancedb/`, or full corpus JSONL
