# Iraqi Legislation RAG

Open toolkit to build a **retrieval-augmented** search stack over Iraqi
legislation: normalize a corpus → embed via OpenRouter → hybrid retrieve →
cited Arabic answers.

**Search + drafting aids, not legal advice.** See [DATA_NOTICE.md](DATA_NOTICE.md).
A disclaimer is always attached to Ask output.

## Cold start (sample fixture, ~cents)

You only need a Python 3.10+ venv and an [OpenRouter](https://openrouter.ai/settings/keys) key.

**PowerShell**

```powershell
cd iraqi-legislation-rag
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env   # then edit: OPENROUTER_API_KEY=sk-or-v1-...
# or:  $env:OPENROUTER_API_KEY = "sk-or-v1-..."

python setup_store.py                  # ingest sample → FTS → law routes
python ask.py "ما هي عقوبة السرقة؟" --no-verify
python eval_recall.py --sample         # recall@k on the sample gold set
```

**bash**

```bash
cd iraqi-legislation-rag
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit OPENROUTER_API_KEY=...
# or:  export OPENROUTER_API_KEY="sk-or-v1-..."

python setup_store.py
python ask.py "ما هي عقوبة السرقة؟" --no-verify
python eval_recall.py --sample
```

`setup_store.py` defaults to `sources/sample_laws.jsonl` when no full
`laws_master.jsonl` is present. Sample records are **synthetic** CI fixtures,
not real statutes — good enough to prove the pipeline.

### Full corpus (later)

When a JSONL release is available, place it at `sources/laws_master.jsonl`
(or pass `--source`) and re-run:

```powershell
python setup_store.py --source sources/laws_master.jsonl
python eval_recall.py --full
```

Full-corpus embedding historically costs about **$0.50–0.75** once
(`baai/bge-m3` via OpenRouter). Do **not** commit the 259MB file or `lancedb/`.

## Self-hosted API

```powershell
python -m uvicorn web.app:app --host 127.0.0.1 --port 7860
```

| Method | Path | Role |
|--------|------|------|
| `GET` | `/health` (also `/api/health`) | Store ok + chunk count |
| `POST` | `/api/ask` | `{ "question": "…", "k": 6, "verify": false }` |

There is **no free hosted answering** — you pay OpenRouter with your own key.
Cloudflare / product UIs are out of scope for this toolkit.

## Costs (order of magnitude)

| Step | What | Rough cost |
|------|------|------------|
| Embed sample (~35 laws) | one-time | cents |
| Embed full corpus (~99k chunks) | one-time | ~$0.50–0.75 |
| Ask (embed + answer) | per query | ~$0.0006 default model |
| Citation verify | per query (optional) | often ≥ answer call |

Use `--no-verify` while iterating on retrieval; leave verify on when judging answer fidelity.

## Layout

```
common.py / ingest.py / ask.py   OpenRouter RAG core
law_registry.py / build_*.py     confidence-gated law routing
setup_store.py                   ingest → FTS → routes
eval_recall.py                   recall@k (no answer LLM)
rag_service.py / web/app.py      shared engine + thin FastAPI
schemas/ + sources/sample_*.jsonl
scraper/                         maintainer scrape (releases preferred)
scripts/package_corpus_release.py
```

## Scraper / corpus releases

**Most users should download a JSONL release**, not scrape. iraqld sits behind
Cloudflare; unattended cron is **not** a supported guarantee — see
[docs/SCRAPING.md](docs/SCRAPING.md).

```powershell
python -m scraper probe                          # live CF honesty check
python -m scraper scrape --limit 5 -o sources/scrape_smoke.jsonl
python scripts/package_corpus_release.py sources/sample_laws.jsonl `
  --corpus-version 0.1.0-sample --out-dir docs/examples
```

Maintainer docs: [scraper/README.md](scraper/README.md).

## Tests / CI

```powershell
python scripts/validate_laws_schema.py
pytest -q
```

CI runs schema validation + unit tests only (no live OpenRouter, no full ingest).

## License / notice

- Code: [MIT](LICENSE)
- Legislation / corpus caveats: [DATA_NOTICE.md](DATA_NOTICE.md)
- Architecture notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
