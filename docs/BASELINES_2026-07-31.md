# Recall baselines — 2026-07-31 night

Store: `IRAQI_RAG_DB_DIR=C:\iraqi-law-rag\lancedb` · **99,377** chunks · suite=full (12 cases) · k=6

| Run | Mode | Cards | recall@6 | Fail |
|---|---|---|---|---|
| hybrid_cards / hybrid_cards_on | hybrid | on | **11/12 (92%)** | `article_exact_labor` title miss |
| hybrid_nocards / hybrid_cards_off | hybrid | off | **11/12 (92%)** | same |
| vector_cards | vector-only | on | **11/12 (92%)** | same |

**Cards A/B:** no delta (on == off). Existing 2,278 cards do not move this suite.

**Only failure:** `المادة 75 قانون العمل` — exact art=75 rows return, but titles are empty / unrelated (e.g. health convention), not `قانون العمل`. Law-scoped exact-article filter, not card routing and not article-vector semantics.

**Spend decision tonight:** no paid cards / no article-embed sample. Seeds already expanded (~47 rules). Morning: $0 fix for exact-article + law title; optionally run capped article embed (`--limit 2000`, ~$0.20) now that HTTP 400 bisect+cap is in `embed_articles.py`.
