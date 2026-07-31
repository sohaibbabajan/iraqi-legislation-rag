# Recall baselines — 2026-07-31 night

Store: `IRAQI_RAG_DB_DIR=C:\iraqi-law-rag\lancedb` · **99,377** chunks · suite=full (12 cases) · k=6  
`SEED_ALIAS_RULES` = **46** · cards on disk ≈ **2,278** · cost = question embeddings only (no answer LLM)

## SPEND_REVIEW $0 gate (pre exact-article fix)

Measured on clean code with DB_DIR override + `--no-cards` + expanded seeds, **before** commit `99228cd` (exact-article law scope).

| Config | Mode | Cards | recall@6 | Fail |
|---|---|---|---|---|
| hybrid + cards | hybrid | on | **11/12 (92%)** | `article_exact_labor` |
| hybrid + `--no-cards` | hybrid | off | **11/12 (92%)** | `article_exact_labor` |
| `--vector-only` + cards | vector | on | **11/12 (92%)** | `article_exact_labor` |
| `--vector-only` + `--no-cards` | vector | off | **11/12 (92%)** | `article_exact_labor` |

**Cards A/B:** no delta (on == off for hybrid and vector). Existing 2,278 cards do not move this suite.

**Sole failure:** `article_exact_labor` (`المادة 75 قانون العمل`). Exact `art=75` rows returned, but titles were empty / unrelated (e.g. health convention), not `قانون العمل`. Law-scoped exact-article filter gap — not card routing and not article-vector semantics.

## After $0 exact-article scope fix (`99228cd`)

`EXACT_ARTICLE` / `ARTICLE_ANALYTICAL` now inherit phrase+seed law scope; defines prefer instrument-titled rows. Smoke on HEAD: `المادة 75 قانون العمل` → `قانون العمل رقم ٣٧ لسنة ٢٠١٥` (`arts=,75,`). Sibling reported hybrid `--no-cards` **12/12** after this fix. Re-run the four cells on HEAD if you need a fresh post-fix table; do not treat a WIP-tree 12/12 as the gate baseline above.

## Spend decision

No full card corpus; no `overnight_p1`. Seeds expanded. Cards remain **NO-GO** for the long tail until a precision metric + scored alias penalty exist.

### Reproduce

```powershell
$env:IRAQI_RAG_DB_DIR = "C:\iraqi-law-rag\lancedb"
python eval_recall.py --full
python eval_recall.py --full --no-cards
python eval_recall.py --full --vector-only
python eval_recall.py --full --vector-only --no-cards
# or: python scripts/run_baselines.py all
```

Raw logs (local only, not committed): `cache/baselines/`.
