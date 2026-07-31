# Hard / precision eval — 2026-07-31

Complements the saturated **recall@6** suite in [`eval_recall.py`](../eval_recall.py)
(12/12). This harness (`eval_precision.py`) requires the **correct
`law_book_id`** (and article **on that law**) inside a tight rank window, plus
outrank forbids for تعديل / wrong-law traps.

```powershell
$env:IRAQI_RAG_DB_DIR = "C:\iraqi-law-rag\lancedb"
python eval_precision.py                 # hybrid, cards on
python eval_precision.py --no-cards      # A/B
python eval_precision.py --ids alias_edu_colloquial amend_trap_penal
```

Cost: question embeds only (≪$0.01 for 17 cases). No answer LLM. No card rebuild.

## Store / suite

| Field | Value |
|---|---|
| Store | `C:\iraqi-law-rag\lancedb` (**99,433** chunks) |
| Cases | **17** |
| Default windows | `law_at=3`, `article_at=6` (exact-article cases use `article_at=3`) |
| Mode | hybrid, cards **on** (full ~38k cards on disk) |

## Latest scores (2026-07-31 evening)

| Metric | Score |
|---|---:|
| PASS | **10/17** (9 retrieval + amendment sidecar meta) |
| FAIL | **7** |
| XFAIL | **0** |
| correct_law@3 | **13/17 (76%)** |

Full-suite footer (cards on, hybrid; before sidecar meta check on last case):

```
precision suite: PASS 9/17  FAIL 7  XFAIL 1  correct_law@3 13/17 (76%)
```

`amend_link_penal_meta` alone (sidecar on disk, 30 amenders for lid 25860): **PASS**.
Combined board: **10 PASS / 7 FAIL / 0 XFAIL**.

Commits: toolkit [`dd4948a`](https://github.com/sohaibbabajan/iraqi-legislation-rag/commit/dd4948a) · Masadir [`93b8fac`](https://github.com/sohaibbabajan/iraqi-law-rag/commit/93b8fac).
Cost: embeds only (≪$0.01).

## Notable FAILs (precision gaps recall@6 misses)

| id | must@ | Fail reason |
|---|---|---|
| `alias_edu_colloquial` | 2 | تعديل الجامعات والكليات (20865) outranks نظام أهلي 1968 |
| `alias_civil_service` | 4 | ذيول الخدمة المدنية occupy top-3; base 3589 at #4 |
| `alias_criminal_procedure` | — | عسكري 22/2016 crowds out اصول 23/1971 entirely |
| `amend_trap_penal` | 2 | Base عقوبات in top-3 but only art **1** chunk — theft arts 438–445 missing |
| `card_footgun_personal_not_procedure` | 5 | سريان كوردستان / أجانب / اتفاقيات before base 12294 |
| `card_footgun_named_penal` | 5 | عقوبات **عسكري** outranks 111/1969 on named «قانون العقوبات» |
| `companies_personality` | 1 | Correct law 22025 but arts 4–5 only; art **8** is on شركات عامة in top-k |

## PASSes (precision already holds)

`alias_edu_uni_land`, `amend_trap_personal`, `amend_trap_labor_bayan`, all four exact/near-miss article cases, `wrong_law_art_trap`, `rent_named_statute`, (+ `amend_link_penal_meta` with sidecar).

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
| `companies_personality` | شركات 21/1997 art 8 | 22025 + 8 |
| `rent_named_statute` | Named إيجار العقار | 16586 @3 |
| `amend_link_penal_meta` | Sidecar `amended_by` for base | 25860 + amendment index |

## Relation to recall@6

[`docs/BASELINES.md`](BASELINES.md) remains the recall gate (12/12). Hard eval is
**allowed to stay red**; treat regressions on previously-PASS cases as the
signal, not “must be 17/17 before merge.”
