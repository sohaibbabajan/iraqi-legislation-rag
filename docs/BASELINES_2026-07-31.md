# Recall baselines — 2026-07-31 night

Canonical copy: see [`docs/BASELINES.md`](BASELINES.md).

### Gate (pre exact-article scope)

| Config | recall@6 | Fail |
|---|---|---|
| hybrid + cards | **11/12** | `article_exact_labor` |
| hybrid + `--no-cards` | **11/12** | `article_exact_labor` |
| `--vector-only` + cards | **11/12** | `article_exact_labor` |
| `--vector-only` + `--no-cards` | **11/12** | `article_exact_labor` |

Cards: **no delta**.

### After exact-article named-law scope (`99228cd`)

| Config | recall@6 |
|---|---|
| hybrid + `--no-cards` | **12/12** |
| hybrid + cards | **12/12** |
