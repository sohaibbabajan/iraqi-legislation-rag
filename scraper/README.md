# Scraper — iraqld → law_record JSONL

Fetch + normalize Iraqi legislation catalog records into `sources/*.jsonl`
matching [`schemas/law_record.schema.json`](../schemas/law_record.schema.json).

## Supported path for most users: releases, not scraping

**Prefer downloading a published corpus snapshot** (GitHub Release / Hugging Face)
and running `scripts/package_corpus_release.py` only if you are *producing* a
release. Cold-start RAG users should not need this package.

This scraper exists for **maintainers** refreshing the snapshot.

## Cloudflare honesty

Target host: [`https://iraqld.e-sjc-services.iq/`](https://iraqld.e-sjc-services.iq/)
(قاعدة التشريعات العراقية), fronted by **Cloudflare**.

| Claim | Status |
|---|---|
| Unattended cron / headless bot that always works | **Not claimed.** Historical private-stack notes and CF bot challenges mean this can fail by IP/ASN/time. |
| `http` mode (stdlib) | Works *sometimes* from some networks (including maintainer probes that got real HTML/JSON). Treat success as lucky, not guaranteed. |
| `playwright` mode (headed, semi-attended) | Supported maintainer path: open Chromium, clear any challenge by hand, then continue with cookies. |
| Public product path | **Ship JSONL releases** with checksum + `corpus_version` manifest. |

See [`docs/SCRAPING.md`](../docs/SCRAPING.md).

## Ethics / ToS

- Respect the site’s terms of use and robots/expectations. There is no
  `robots.txt` at the root today (404 observed); that does **not** mean
  unlimited automation is welcome.
- Use polite rate limits (`--delay`, default 1s). Do not hammer the origin.
- Attribute المصدر via `source_url` / `pdf_url` on each record.
- Legislation text is typically publishable; this project still claims **no
  official status** — see [`DATA_NOTICE.md`](../DATA_NOTICE.md).
- Prefer official dumps if the publisher ever offers one.

## Install

Base install (HTTP mode, stdlib networking):

```powershell
cd C:\iraqi-legislation-rag
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Optional Playwright (attended mode):

```powershell
pip install playwright
playwright install chromium
```

## How to run

Probe connectivity + print a Cloudflare assessment (safe, ~2 requests):

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m scraper probe
python -m scraper probe --mode playwright
```

Smoke scrape (few records → local JSONL, gitignored under `sources/`):

```powershell
python -m scraper scrape --limit 5 -o sources/scrape_smoke.jsonl --delay 1.5
```

Metadata-only inventory (no detail pages / empty `full_text`):

```powershell
python -m scraper scrape --metadata-only --limit 20 -o sources/catalog_smoke.jsonl
```

Full maintainer scrape (long-running; resume-safe):

```powershell
python -m scraper scrape -o sources/laws_master.jsonl --delay 1.5
# If Cloudflare blocks:
python -m scraper scrape --mode playwright -o sources/laws_master.jsonl
```

Resume / status:

```powershell
python -m scraper status -o sources/laws_master.jsonl
# State lives in cache/scraper/state.json (+ changelog.jsonl)
```

Idempotency: existing `lawBookID`s already present in the output JSONL are
skipped. Ctrl-C and re-run safely. State tracks `last_page` and failures.

## Normalize shape

Each line is a `law_record`: `lawBookID`, `lawTitle`, `status_label`
(`ساري`/`ملغى` from `lawValid`), `full_text` (from embedded `var articles` on
the detail page), `source_url`, `pdf_url`, gazette fields, etc.

## Package a release (after you have JSONL)

```powershell
python scripts/package_corpus_release.py sources/laws_master.jsonl `
  --corpus-version 2026-07-31 --scrape-date 2026-07-31 --out-dir releases/
```

Attach `laws_master.jsonl` + `*.manifest.json` + `*.sha256` to the Release.
Do **not** commit the full ~259MB corpus into git.
