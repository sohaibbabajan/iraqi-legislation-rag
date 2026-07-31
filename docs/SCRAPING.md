# Scraping Iraqi legislation (iraqld)

## TL;DR

1. **Public / cold-start users:** download a **corpus release** (JSONL +
   manifest). Do not scrape.
2. **Maintainers:** use `python -m scraper` with polite delays; prefer
   **Playwright semi-attended** if Cloudflare challenges; package with
   `scripts/package_corpus_release.py`.
3. **Do not claim** that unattended cron scraping of iraqld “just works”
   unless you have proven it on your infrastructure for a sustained period.

## Target

| Item | Value |
|---|---|
| Site | [قاعدة التشريعات العراقية](https://iraqld.e-sjc-services.iq/) |
| Host | `iraqld.e-sjc-services.iq` |
| CDN / bot filter | **Cloudflare** (responses may include `cf-ray`) |
| Catalog API | `GET /Legislations/SearchLegislations` (same params as site JS) |
| Detail page | `GET /legislations/showlegislation?lawbookid={id}` |
| Full text | Embedded `var articles = [...]` in detail HTML (not a separate API) |

Observed catalog size on a 2026-07 maintainer probe: **~43,848** rows
(`totalCount`), ID span roughly in the low tens to ~55k (sparse).

## Cloudflare status assessment

From private-stack history ([HANDOFF](https://github.com/sohaibbabajan) /
local `iraqi-law-rag`): the legislation site sits behind Cloudflare’s bot
challenge; **server-side cron that never sees a browser challenge is not a
reliable design**.

From toolkit probes while building this scraper:

- Simple `urllib` GETs from some networks returned **HTTP 200** with real
  search JSON and detail HTML (no interstitial).
- That success is **environment-dependent**. Other IPs, datacenter ASNs,
  headless fingerprints, or future CF rules can flip to a challenge without
  notice.
- Therefore:

| Mode | Role |
|---|---|
| **Release JSONL** | **Primary** supported path for anyone building RAG |
| `scraper` `--mode http` | Maintainer convenience when CF allows |
| `scraper` `--mode playwright` | **Semi-attended**: human clears challenge, then scrape resumes with browser cookies |
| Unattended headless cron | **Unsupported** until proven; do not document as working |

`python -m scraper probe` prints a live assessment for *your* network.

## Attended vs release-primary

```text
iraqld  --(attended scrape)-->  sources/laws_master.jsonl
                                      |
                                      v
                         package_corpus_release.py
                                      |
                                      v
                    GitHub Release / Hugging Face  --(download)-->  users
                                                                      |
                                                                      v
                                                                   ingest.py
```

Users clone the toolkit, download the release artifact, ingest. Maintainers
occasionally refresh the snapshot.

## Rate limits & resume

- Default `--delay 1.0` second between requests.
- Output JSONL append + skip-by-`lawBookID` (full `scrape`).
- Incremental **`sync`**: newest-first discovery → upsert merge (no duplicate
  identities). Watermark + changelog under `cache/scraper/`.
- Design: [`docs/CORPUS_SYNC.md`](CORPUS_SYNC.md).

- State: `cache/scraper/state.json` (`last_page`, watermarks, fetched/failed ids).
- Changelog: `cache/scraper/changelog.jsonl` (new/updated ids).

## Packaging

```powershell
python scripts/package_corpus_release.py path\to\laws_master.jsonl `
  --corpus-version 2026-07-31 --scrape-date 2026-07-31 --out-dir releases/
```

Manifest fields include `corpus_version`, `scrape_date`, `record_count`,
`sha256`, and rough status/year stats. Example (sample fixture dry-run):
[`examples/sample_laws.manifest.json`](examples/sample_laws.manifest.json).

## Legal / ethics

See [`scraper/README.md`](../scraper/README.md) and
[`DATA_NOTICE.md`](../DATA_NOTICE.md). Attribute the catalog; no official
status; not legal advice.
