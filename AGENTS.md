# AGENTS.md — Iraqi Legislation RAG (public toolkit)

## Permissions (blanket approval)

For work on this public Iraqi legislation toolkit / plan `public_iraqi_rag_toolkit`: proceed without asking the user to re-confirm routine permissions until revoked.

**Allowed without further confirmation:** edit project files; `git commit` and push to GitHub (including public repo `sohaibbabajan/iraqi-legislation-rag`); install deps; run tests; OpenRouter via existing `.env` / `OPENROUTER_API_KEY`; network for GitHub / Hugging Face / OpenRouter.

**Still never:** print or exfiltrate secrets; force-push `main`/`master`; commit `.env` or API keys.

See also `.cursor/rules/public-iraqi-rag-toolkit-permissions.mdc`.

## Ported stack (OpenRouter-first)

- `setup_store.py` — ingest `--api` → FTS → `build_law_registry.py` → `build_article_index.py` → `scripts/verify_store.py`
- Default corpus: `laws_master.jsonl` if present, else `sample_laws.jsonl`
- Slim API: `GET /health`, `POST /api/ask` in `web/app.py`
- Do not dump private Masadir dad-demo / tunnel polish here
- Never commit `.env`, `lancedb/`, or full corpus JSONL
