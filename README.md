# Iraqi Legislation RAG

Open toolkit to build a **retrieval-augmented** search stack over Iraqi
legislation: normalize a corpus → embed via OpenRouter → hybrid retrieve →
cited Arabic answers.

**Status (Phase 1 scaffold):** schema, sample fixture, and CI validation are
in place. Full ingest / ask / scraper land in later phases. This is **search +
drafting aids, not legal advice** — see [DATA_NOTICE.md](DATA_NOTICE.md).

## Cold start (outline)

Full polish comes later. Intended path once Phase 2–3 ship:

1. Clone this repo and create a venv.
2. `pip install -r requirements.txt`
3. Set `OPENROUTER_API_KEY` (BYO key — no hosted free answering).
4. Download a corpus release **or** run `scraper/` (when available).
5. `python ingest.py --api` → LanceDB + FTS (+ law routes).
6. `python ask.py "…"` / optional thin FastAPI.

**Today you can:**

```powershell
cd C:\iraqi-legislation-rag
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts/validate_laws_schema.py
pytest -q
```

## Layout

```
scraper/            # fetch + normalize (stub — Phase 3)
schemas/            # JSON Schema for law (+ chunk) records
sources/            # sample_laws.jsonl only in git; full corpus via Releases
scripts/            # schema validation, future release helpers
tests/              # unit + schema tests (no live store / no e2e ask)
docs/               # architecture notes (stubs)
.github/workflows/  # CI: pytest + schema validation
```

RAG core modules (`common.py`, `ingest.py`, `ask.py`, …) arrive in **Phase 2**.

## Data

- Schema: [`schemas/law_record.schema.json`](schemas/law_record.schema.json)
- Tiny committed fixture: [`sources/sample_laws.jsonl`](sources/sample_laws.jsonl)
  (~35 synthetic records for CI — **not** real law text)
- Full `laws_master.jsonl` (~259MB) is **not** in git; use Releases / HF later

Validate any JSONL:

```powershell
python scripts/validate_laws_schema.py path\to\file.jsonl
```

## License / notice

- Code: [MIT](LICENSE)
- Legislation / corpus caveats: [DATA_NOTICE.md](DATA_NOTICE.md)

## Publishing this repo to GitHub

`gh` was not available when this scaffold was created. To publish:

```powershell
cd C:\iraqi-legislation-rag
# install GitHub CLI: https://cli.github.com/
gh auth login
gh repo create sohaibbabajan/iraqi-legislation-rag --public --source=. --remote=origin --push
```

Or create an empty public repo on GitHub named `iraqi-legislation-rag`, then:

```powershell
git remote add origin https://github.com/sohaibbabajan/iraqi-legislation-rag.git
git push -u origin master
```

Never commit `.env`, `lancedb/`, or the full corpus.
