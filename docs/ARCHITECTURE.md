# Architecture — Iraqi Legislation RAG

End-to-end path strangers can run with only `OPENROUTER_API_KEY`:

```mermaid
flowchart LR
  jsonl[JSONL corpus]
  ingest[ingest --api]
  lance[LanceDB + FTS]
  routes[law_routes]
  art[article_index]
  ask[ask / FastAPI]
  jsonl --> ingest --> lance --> ask
  jsonl --> routes
  jsonl --> art
  routes --> ask
  art --> ask
```

## Components

| Module | Role |
|--------|------|
| `common.py` | Paths, chunking, article extraction, OpenRouter model lists, `.env` loader |
| `ingest.py` | Embed JSONL → LanceDB (`--api` default path); `--build-fts` for BM25 |
| `build_law_registry.py` | Offline title/alias registry + `law_routes` embeddings |
| `law_registry.py` | Instrument phrases, seed aliases, registry I/O |
| `ask.py` | Hybrid BM25+vector retrieve, confidence-gated routing, answer + verify |
| `rag_service.py` | Same engine for CLI and HTTP |
| `setup_store.py` | One command: ingest → FTS → routes → article index → verify |
| `build_article_index.py` | Deterministic `cache/article_index.jsonl` (`defines` vs `mentions`) |
| `scripts/verify_store.py` | Presence checks for FTS / routes / article_index |
| `eval_recall.py` | Recall@k gold (sample suite auto-selected on small stores) |
| `web/app.py` | `GET /health`, `POST /api/ask` only |

## P0 — article index (`defines` vs `mentions`)

Today's chunk `article_nums` conflates "this text *is* article 438" with
"this text *cites* 438". The free offline fix:

```powershell
python build_article_index.py --source sources/sample_laws.jsonl
python scripts/verify_store.py --sample --skip-registry
pytest tests/test_article_index.py -q
```

Output: `cache/article_index.jsonl` — one row per defining article (label,
ASCII, char span, body text, `mentions_articles`) plus `role=mentions` rows
for in-body citations. Wired into `setup_store.py` after routes (skip with
`--skip-article-index`). No OpenRouter spend; no law cards.

## Retrieval

1. Embed the question (`baai/bge-m3` via OpenRouter).
2. Hybrid: LanceDB vector + FTS (`RRFReranker`); Arabic FTS without English stemming defaults.
3. Optional law routing: registry phrase/seed match + title vectors; **high** confidence (named statute) puts routed chunks ahead of hybrid; **low** (topical) keeps hybrid first.
4. Exact-article lookups can short-circuit generation and return verbatim text.
5. Answers cite retrieved text; default filter is in-force (`ساري`) only.

## Data

- Schema: `schemas/law_record.schema.json`
- Git ships `sources/sample_laws.jsonl` only (synthetic).
- Full `laws_master.jsonl` comes from Releases / HF / scraper (Phase 3) — not git.

## Scope

Search and drafting aids, **not** legal advice. Disclaimer strings are attached in CLI/API output regardless of model cooperation.
