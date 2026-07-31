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
2. Keep **NO-GO** on 38k cards / `overnight_p1`.
3. Optional later ≤$0.20: article-embed `--limit 2000` **only** if a definitional/precision harness shows a gap seeds can't close.
4. Before any card spend: scored تعديل/بيان penalty on card aliases + `--priority` only.
