# Scraper (Phase 3)

Fetch + normalize Iraqi legislation catalog records into `sources/*.jsonl`
matching [`schemas/law_record.schema.json`](../schemas/law_record.schema.json).

**Not implemented in Phase 1.** Planned:

- Target: قاعدة التشريعات / iraqld (confirm live host at implementation time)
- Idempotent resume, rate limits, robots/ToS notes
- Honesty about Cloudflare: if unattended scrape fails, ship **snapshot
  releases** as the primary path and keep an attended / Playwright mode for
  maintainers

Until then, use the synthetic fixture `sources/sample_laws.jsonl` for schema
CI only — not for real RAG answers.
