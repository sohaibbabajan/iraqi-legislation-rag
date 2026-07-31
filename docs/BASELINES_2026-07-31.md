# Recall baselines — 2026-07-31 night

Store: `IRAQI_RAG_DB_DIR=C:\iraqi-law-rag\lancedb` · **99,377** chunks · suite=full (12 cases) · k=6  
`SEED_ALIAS_RULES` = **46** · cards on disk ≈ **2,278** · cost = question embeds only

| Run | Mode | Cards | recall@6 | Fail |
|---|---|---|---|---|
| hybrid_cards | hybrid | on | **11/12 (92%)** | `article_exact_labor` title miss |
| hybrid_nocards | hybrid | off | **11/12 (92%)** | same |
| vector_cards | vector-only | on | **11/12 (92%)** | same |
| vector_nocards | vector-only | off | **12/12 (100%)** | — |

**Cards A/B (hybrid):** no delta (on == off). Existing cards do not move this suite.

**vector-only:** cards on matched hybrid’s 11/12; cards off reached **12/12** on this run.

**Shared failure (when present):** `المادة 75 قانون العمل` — exact art=75 rows return, but titles are empty / unrelated (e.g. health convention), not `قانون العمل`. Law-scoped exact-article filter, not card routing and not article-vector semantics.

**Spend decision:** no paid cards / no overnight restart. Seeds expanded. Morning options: $0 exact-article + law-title filter; optional capped article embed after HTTP 400 fix.

### Reproduce

```powershell
$env:IRAQI_RAG_DB_DIR = "C:\iraqi-law-rag\lancedb"
python eval_recall.py --full
python eval_recall.py --full --no-cards
python eval_recall.py --full --vector-only
python eval_recall.py --full --vector-only --no-cards
# or: python scripts/run_baselines.py all
```
