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

## Full in-force corpus (2026-07-31, user said `full 38k`)

User override of prior NO-GO. Single long-lived resumable process (not `overnight_p1`, not Task Scheduler):

```powershell
cd C:\iraqi-legislation-rag
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -u build_law_cards.py --workers 4
# log: cache/full_law_cards_2026-07-31.log (+ .err.log)
```

| Checkpoint | Unique cards | Todo remaining | Est. remaining $ |
|---|---:|---:|---:|
| start of full run | **2,438** | **35,584** | ~$7.8–8.0 @ $0.00022/card |
| mid-run (~10 min) | **~3,250** | ~34.7k | ~$7.6; this-run spend ~$0.18 |
| (update after Done line) | — | — | — |

Mid-run hybrid eval@6 (Masadir `lancedb`, 12 cases): **cards on 12/12 · cards off 12/12** (delta still none).

**Process:** PID **23724** (worker; parent wrapper **22748**). Log: `cache/full_law_cards_2026-07-31.log`.

```powershell
# monitor
Get-Content C:\iraqi-legislation-rag\cache\full_law_cards_2026-07-31.log -Tail 20 -Wait
# kill (single run — do not leave a scheduler)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'build_law_cards' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Alias safety from `1d362e4` remains ON. `cache/` is gitignored — do not commit `law_cards.jsonl`.
