# Recall baselines — 2026-07-31 night

Store: `IRAQI_RAG_DB_DIR` / `DB_DIR` = `C:\iraqi-law-rag\lancedb`  
Chunks: **99,377** · suite: **full** (12 cases) · k=6  
Seeds: **46** `SEED_ALIAS_RULES` · cards on disk: **2,278**  
Paid OpenRouter tonight (beyond embeds): **$0**. Question embeds only (≪$0.01).

## Gate baselines (before exact-article scope fix)

| Mode | Cards | recall@6 | Fail |
|---|---|---|---|
| hybrid | on | **11/12** | `article_exact_labor` (art 75, empty/wrong title) |
| hybrid | off | **11/12** | same |
| vector-only | on | **11/12** | same |
| vector-only | off | **11/12** | same |

**Cards A/B delta: none.** Gate verdict: do **not** buy priority cards or article-embed sample.

## Post `$0` exact-article + named-law scope (commit `99228cd`)

| Mode | Cards | recall@6 |
|---|---|---|
| hybrid | off (`--no-cards`) | **12/12 (100%)** |
| hybrid | on | **12/12 (100%)** |

Still **no cards delta**. Unlock was law-scoped exact-article retrieval, not LLM cards / article vectors.

## Env / CLI shipped

- `IRAQI_RAG_DB_DIR` or short `DB_DIR` → reuse Masadir store ($0)
- `--no-cards` / `IRAQI_RAG_NO_CARDS` / `RAG_NO_CARDS`
- `SEED_ALIAS_RULES`: 5 → **46**
- `embed_articles.py`: text cap + HTTP 400 bisect (ready; **not spent**)

## Morning recommendation

1. Re-measure vector-only A/B under clean `master` (expect ~12/12; low priority).
2. Optional later ≤$0.20: article-embed `--limit 2000` **only** if a definitional/precision harness shows a gap seeds can't close.
3. Alias safety (تعديل/بيان scoring + base-code-hijack reject) must stay on for any card routing.

## Targeted priority cards (2026-07-31 night, after user GO)

Preconditions shipped first: تعديل/بيان scoring + base-code-hijack reject on card/lexicon
aliases; `--priority` candidate order prefers major codes over chronological تعديلات /
مراسيم.

| Run | Flags | New cards | Cost | Notes |
|---|---|---:|---:|---|
| targeted | `--priority --limit 200 --workers 4` | **180** | **$0.045** | log: `cache/targeted_priority_cards_2026-07-31.log` |

Cards on disk after run: **2,438** unique (`law_book_id`; 2,458 JSONL lines). Lexicon: **9,835** rows.

| Mode | Cards | recall@6 |
|---|---|---:|
| hybrid | off | **12/12** |
| hybrid | on | **12/12** |

**Cards A/B delta: still none.** Paid OpenRouter this experiment: **$0.045** (no article embeds). Overnight task / watchdog left disabled.

## Full in-force corpus (2026-07-31, user said `full 38k`) — **DONE**

User override of prior NO-GO. Resumable JSONL (skips existing `law_book_id`). Alias safety ON. No Task Scheduler.

| Phase | Workers | Unique (start→end) | Rate | Cost (that phase) | Log |
|---|---:|---|---:|---:|---|
| early / resume | **4** | 2,438 → **~6,981** | ~**19**/min | (prior + resume) | `cache/full_law_cards_2026-07-31*.log` |
| speed-up finish | **16** | 7,001 → **37,376** | ~**300**/min (peak ~370) | **$7.737** | `cache/full_law_cards_2026-07-31_fast.log` |

Final Done line (`--workers 16`): `wrote=30375 failed=646 cards_file=37376 lexicon_rows=147540 … cost=$7.737341 elapsed=5826.0s rate=5.21 cards/s`.

Registry in-force titles: **37,990**. Cards unique: **37,376** (~614 short of registry; matches failed≈646 this phase). Lexicon: **147,540** rows.

Code tweak kept for future runs: default `--workers 12`, thread-local `requests.Session` reuse + connection pool (no worker ceiling).

### Post-full hybrid eval@6 (`IRAQI_RAG_DB_DIR=C:\iraqi-law-rag\lancedb`)

| Mode | Cards | recall@6 | Notes |
|---|---|---|---|
| hybrid | off (`--no-cards`) | **12/12 (100%)** | clean solo run |
| hybrid | on | **≥11/12 PASS, 0 FAIL** | full 12/12 repeatedly interrupted mid-suite by concurrent `eval_recall` peers / LanceDB contention; earlier mid-corpus baseline was 12/12 |

**Cards A/B delta: still none on measured cases.** Alias safety from `1d362e4` remains ON. `cache/` is gitignored — do not commit `law_cards.jsonl`.

```powershell
# kill if a build is still running (none expected after Done)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'build_law_cards' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```
