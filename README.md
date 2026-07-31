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

### Reuse an existing LanceDB (no re-embed)

If you already paid for a full-corpus store elsewhere (e.g. a sibling Masadir
checkout), point this toolkit at it — **$0**, no copy required:

```powershell
$env:IRAQI_RAG_DB_DIR = "C:\iraqi-law-rag\lancedb"
# optional: copy that repo's cache/law_registry.jsonl into this cache/
python eval_recall.py --full
python ask.py "ما هي عقوبة السرقة؟" --no-verify
```

`IRAQI_RAG_DB_DIR` is read from the environment or `.env` at import time.

### Law-card A/B

Optional LLM law cards (`cache/law_cards.jsonl`) feed routing aliases only.
Disable them to measure seed/instrument routing alone:

```powershell
python eval_recall.py --no-cards
python ask.py --no-cards "ما هو قانون التعليم الاهلي؟" --no-verify
# or:  $env:IRAQI_RAG_NO_CARDS = "1"
```

### Full corpus (Release download)

Published snapshot (do **not** scrape for cold start):

- **Release:** [corpus-2026-07-31](https://github.com/sohaibbabajan/iraqi-legislation-rag/releases/tag/corpus-2026-07-31)
- **Assets:** `laws_master.jsonl` (~259 MB) + `.sha256` + `.manifest.json`
- **Records:** 43,840 · **SHA-256:** `bfd1d1396a3c7ec3aa5a4a7d98be2459eef7cd921dde683e4e8fa1d4355d7aeb`

```powershell
# download laws_master.jsonl from the Release, verify hash, then:
# place at sources/laws_master.jsonl (gitignored) or pass --source
python setup_store.py --source sources/laws_master.jsonl
python eval_recall.py --full
```

Full-corpus embedding historically costs about **$0.50–0.75** once
(`baai/bge-m3` via OpenRouter). Do **not** commit the 259MB file or `lancedb/`.
See [docs/CORPUS_SYNC.md](docs/CORPUS_SYNC.md) for maintainer incremental refresh.

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
amendment_links.py               معدل ← تعديل sidecar ($0)
setup_store.py                   ingest → FTS → routes → links
eval_recall.py                   recall@k (no answer LLM)
rag_service.py / web/app.py      shared engine + thin FastAPI
schemas/ + sources/sample_*.jsonl
scraper/                         maintainer scrape (releases preferred)
scripts/package_corpus_release.py
```

Rebuild amendment links anytime (no API):

```powershell
python build_amendment_links.py
pytest tests/test_amendment_links.py -q
```

## Scraper / corpus releases

**Most users should download a JSONL release**, not scrape. iraqld sits behind
Cloudflare; unattended cron is **not** a supported guarantee — see
[docs/SCRAPING.md](docs/SCRAPING.md) and [docs/CORPUS_SYNC.md](docs/CORPUS_SYNC.md).

```powershell
python -m scraper probe                          # live CF honesty check
python -m scraper sync --limit 5 -o sources/laws_master.jsonl   # incremental new laws
python -m scraper merge --into sources/laws_master.jsonl sources/delta.jsonl
python -m scraper scrape --limit 5 -o sources/scrape_smoke.jsonl  # full-walk smoke
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
