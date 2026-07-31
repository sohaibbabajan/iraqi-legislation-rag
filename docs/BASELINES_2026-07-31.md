# Recall baselines — 2026-07-31 night

Canonical copy: see [`docs/BASELINES.md`](BASELINES.md).

| Config | recall@6 | Fail |
|---|---|---|
| hybrid + cards | **11/12** | `article_exact_labor` |
| hybrid + `--no-cards` | **11/12** | `article_exact_labor` |
| `--vector-only` + cards | **11/12** | `article_exact_labor` |
| `--vector-only` + `--no-cards` | **11/12** | `article_exact_labor` |

Cards show **no delta**. An earlier “vector `--no-cards` 12/12” note was measured against an uncommitted exact-article WIP and is **not** a clean-`master` baseline.
