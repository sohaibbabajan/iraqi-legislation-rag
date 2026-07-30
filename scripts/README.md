# Scripts

| Script | Role |
|---|---|
| `validate_laws_schema.py` | Validate a JSONL against `schemas/law_record.schema.json` |
| `package_corpus_release.py` | SHA-256 + record counts + `corpus_version` manifest for Releases/HF |

```powershell
python scripts/validate_laws_schema.py sources/sample_laws.jsonl
python scripts/package_corpus_release.py sources/sample_laws.jsonl `
  --corpus-version 0.1.0-sample --scrape-date 2026-07-31 --out-dir docs/examples
```

Scraper CLI lives in the `scraper/` package (`python -m scraper …`).
