# Hard / precision eval — 2026-07-31 (late)

Complements the saturated **recall@6** suite in [`eval_recall.py`](../eval_recall.py)
(12/12). This harness (`eval_precision.py`) requires the **correct
`law_book_id`** (and article **on that law**) inside a tight rank window, plus
outrank forbids for تعديل / wrong-law traps.

```powershell
$env:IRAQI_RAG_DB_DIR = "C:\iraqi-law-rag\lancedb"
python eval_precision.py                 # hybrid, cards on
python eval_precision.py --no-cards      # A/B
python eval_precision.py --ids alias_edu_colloquial amend_trap_penal
python eval_precision.py --strict        # exit 1 on FAIL (CI gate when green)
```

Cost: question embeds only (≪$0.01 for 17 cases). No answer LLM. No card rebuild.

## Store / suite

| Field | Value |
|---|---|
| Store | `C:\iraqi-law-rag\lancedb` (**99,433** chunks) |
| Cases | **17** |
| Default windows | `law_at=3`, `article_at=6` (exact-article cases use `article_at=3`) |
| Mode | hybrid, cards **on** (full ~38k cards on disk) |

## Latest scores (2026-07-31 night — after routing demotions + amendment 1.1.0)

| Metric | Score |
|---|---:|
| PASS | **17/17** |
| FAIL | **0** |
| XFAIL | **0** |
| correct_law@3 | **17/17 (100%)** |

Before (evening board): **10 PASS / 7 FAIL**. After deterministic routing fixes
(title secondary demotions عسكري/ذيل/تعديل/سريان, overview gating, seed
title_any tightening, companies gold 5\|22): **17/17**.

`amend_link_penal_meta`: **PASS** (sidecar builder **1.1.0**, boundary-safe
containment; ⚠ `amended_by` listing; same-article amender pull gated until
`builder_version >= 1.1.0`).

Amendment false-edge **gone**: labor 32566 ↛ currency amender 27527
(`العمل` ⊂ `العملة` rejected). Sidecar copied to Masadir `cache/` and resolved
via `resolve_amendment_links_file()` (sibling toolkit fallback).

Cost: embeds only (≪$0.01).

## Fixes that closed the 7 FAILs

| id | Fix |
|---|---|
| `card_footgun_named_penal` / `alias_criminal_procedure` | Demote عسكري; اصول `title_any` requires `…الجزائية رقم` (excludes العسكري) |
| `alias_civil_service` | Demote ذيل/ذيول when not in query |
| `amend_trap_penal` | `is_overview_question` no longer fires on topical «ما هي عقوبة…»; primary-title fit |
| `alias_edu_colloquial` | Drop bare جامعات needle from colloquial seed; demote تعديل |
| `card_footgun_personal_not_procedure` | Prefer bare base title; demote سريان/للاجانب |
| `companies_personality` | Gold arts **5\|22** (personality); art 8 is formation only |

## Case catalog

| id | Stress | Gold |
|---|---|---|
| `alias_edu_colloquial` | Colloquial تعليم أهلي; تعديل must not win | lids 35340 / 30735 / 23716 @3 |
| `alias_edu_uni_land` | Alias + ارض distractor | 35340 + arts 6\|51 |
| `alias_civil_service` | Bare الخدمة المدنية vs ذيول | 3589 @3 |
| `alias_criminal_procedure` | اصول vs عسكري hijack | 8766 @3 |
| `amend_trap_penal` | Base عقوبات + theft arts | 25860 + 438–445 |
| `amend_trap_personal` | أحوال base before تعديلات | 12294 @3 |
| `amend_trap_labor_bayan` | 2015 work code; not 1936 bayan | 32566 + 74–78 |
| `exact_labor_75_not_foreign` | Exact art scoped to work code | 32566 + 75 @3 |
| `exact_civil_741_scoped` | Exact art scoped to civil | 27297 + 741 @3 |
| `wrong_law_art_trap` | Civil art 75; labour must not win | 27297 + 75; forbid 32566 |
| `near_miss_penal_438` | 438 not only neighbour 439 | 25860 + 438 |
| `near_miss_labor_74` | 74 not only popular 75 | 32566 + 74 |
| `card_footgun_personal_not_procedure` | سريان/أجانب vs base | 12294; forbid 25832 |
| `card_footgun_named_penal` | Named عقوبات; not عسكري | 25860 + 405–408 |
| `companies_personality` | شركات 21/1997 personality | 22025 + 5\|22 |
| `rent_named_statute` | Named إيجار العقار | 16586 @3 |
| `amend_link_penal_meta` | Sidecar `amended_by` for base | 25860 + amendment index |

## Relation to recall@6

[`docs/BASELINES.md`](BASELINES.md) remains the recall gate (12/12). Hard eval is
now green on this store; keep `--strict` optional until CI points at the full
Masadir LanceDB.
