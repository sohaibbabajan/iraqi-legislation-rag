# Scripts

| Script | Role |
|---|---|
| `validate_laws_schema.py` | Validate a JSONL against `schemas/law_record.schema.json` |
| `package_corpus_release.py` | SHA-256 + record counts + `corpus_version` manifest for Releases/HF |
| `refresh_corpus.py` | One-shot sync → Masadir mirror → ingest → FTS → registry → cards-for-new-ids |

```powershell
python scripts/validate_laws_schema.py sources/sample_laws.jsonl
python scripts/package_corpus_release.py sources/sample_laws.jsonl `
  --corpus-version 0.1.0-sample --scrape-date 2026-07-31 --out-dir docs/examples

# Corpus freshness (manual / --once; see docs/CORPUS_SYNC.md)
python scripts/refresh_corpus.py --dry-run
python scripts/refresh_corpus.py --once --sync-limit 5 --skip-cards
```

Scraper CLI lives in the `scraper/` package (`python -m scraper …`).
