# Corpus sync — incremental scrape + merge

Maintainer path for keeping the legislation JSONL current without
re-scraping ~44k instruments every time, and without duplicating rows when
merging. Cold-start users still **download a Release** — see
[SCRAPING.md](SCRAPING.md).

**Current public snapshot:**
[corpus-2026-07-31](https://github.com/sohaibbabajan/iraqi-legislation-rag/releases/tag/corpus-2026-07-31)
(43,840 records; SHA-256 `bfd1d1396a3c7ec3…` — full digest in the Release
`laws_master.sha256` asset). Do not git-commit the JSONL.

## Identity (dedupe key)

| Source | Primary key | Notes |
|---|---|---|
| iraqld legislation | `iraqld:lawBookID:{id}` | Site `lawBookID` is stable and sparse (~13…~55k). |
| Future non-legislation | `{source_type}:corpus_id:{id}` | Textbooks, books, other datasets. |
| Fallback only | `{source_type}:title_year:{num}|{year}|{title_norm}` | When no site id / corpus_id. |

`source_type` defaults to `iraqld` on scraper output; other corpora set it
explicitly (e.g. `textbook`). Schema allows extra fields
(`additionalProperties: true`); ingest keys chunks as `{lawBookID}#n` today —
future sources should mint a numeric or string id that stays unique per
`source_type`.

**Upsert rule:** same identity → replace if `content_fingerprint` differs;
otherwise skip. Never append a second line for the same identity.

Fingerprint covers: title, status, flag, dates, year, code, notes, and
SHA-256 of `full_text` (so metadata-only vs full-text refreshes both count).

## New-law discovery

Observed catalog behavior (maintainer probes + smoke JSONL): **page 1 of
`SearchLegislations` returns recently published instruments** (high
`lawBookID`, recent `date_iso`). Full backfill still walks pages
ascending from 1…N for completeness.

| Mode | Command | Strategy |
|---|---|---|
| **Incremental** | `python -m scraper sync` | Walk from page 1; fetch unknown ids; stop after a streak of already-known ids (default 2× page size) or `--limit`. |
| **Date window** | `sync --from-date YYYY-MM-DD` | Same walk, but catalog filter `fromDate` (and optional `--to-date`). |
| **Full backfill** | `python -m scraper scrape` | Resume by `last_page` + skip existing ids (legacy path). |

**Watermark** (in `cache/scraper/state.json`):

- `watermark_lawBookID` — max id successfully merged
- `watermark_date_iso` — max non-empty `date_iso` seen
- `catalog_total_count` — last observed `totalCount`
- `last_sync_at` — Unix time of last successful sync

Watermark is informational and used as a soft stop hint; **authoritative
dedupe is the identity set in the master JSONL**, not the watermark alone
(ids are sparse; catalog order can change).

## Merge without copies

```text
delta / scrape output  ──►  scraper.merge  ──►  laws_master.jsonl (atomic rewrite)
                                │
                                └── optional sources/delta_YYYYMMDD.jsonl
```

```powershell
python -m scraper merge --into sources/laws_master.jsonl sources/scrape_smoke.jsonl
python -m scraper sync -o sources/laws_master.jsonl --delta sources/delta_latest.jsonl --limit 5
```

`merge` loads the master index by identity, applies incoming upserts, writes
via temp file + replace. `package_corpus_release.py` then emits
`sha256` + manifest for the Release asset.

## Cloudflare / release honesty

Unchanged from [SCRAPING.md](SCRAPING.md):

- Releases are the **primary** public path (do not git-commit ~259MB JSONL).
- `sync` / `scrape` are **maintainer** tools; HTTP may work from some
  networks; Playwright is semi-attended when CF challenges.
- Do **not** document unattended cron as supported.

## GitHub stays current

1. Maintainer runs `sync` (or rare full `scrape`) → updated local master.
2. `python scripts/package_corpus_release.py sources/laws_master.jsonl --corpus-version YYYY-MM-DD …`
3. Attach JSONL + `.manifest.json` + `.sha256` to a **GitHub Release**
   (and/or Hugging Face). Optional small `delta_*.jsonl` may live in git
   for transparency; the full master does not.
4. CI stays offline (schema + unit tests). Publishing Releases is a
   human / manual Actions step until CF-safe automation is proven.

## Future sources (textbooks, books, …)

Do **not** build textbook ingest yet. Extension point:

1. Emit JSONL rows with `source_type` + `corpus_id` (and a stable
   `lawBookID`-shaped or parallel id if ingest requires it).
2. Reuse `record_identity` / `merge_jsonl` — same master file or a sibling
   `sources/{corpus_id}_master.jsonl` merged at ingest via multiple
   `--source` passes (`ingest.py` already accepts `--source`).
3. Chunk ids remain idempotent per record id; no scraper redesign required.

## CLI cheat sheet

```powershell
python -m scraper probe
python -m scraper sync --limit 5 -o sources/laws_master.jsonl
python -m scraper sync --from-date 2026-07-01 --limit 20
python -m scraper merge --into sources/laws_master.jsonl sources/delta.jsonl
python -m scraper status -o sources/laws_master.jsonl
python scripts/package_corpus_release.py sources/laws_master.jsonl `
  --corpus-version 2026-07-31 --out-dir releases/
```
