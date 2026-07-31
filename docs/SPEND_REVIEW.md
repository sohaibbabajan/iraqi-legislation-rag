# Spend Review — upfront structure for Iraqi legislation RAG

Date: 2026-07-31 · Reviewer: adversarial cost/benefit pass
Question: should ~$8–15 of OpenRouter budget go to LLM law cards (~38k laws)
and article-level embeddings?

---

## 1. Executive verdict

> ## **NO-GO** on the 38k law-card build as configured.
> ## **GO-CONDITIONAL** on ~$1.50 of targeted spend, gated on a $0 measurement first.

The card build was **running while this review was written** and was stopped at
**2,278 cards** (~6.0% of 37,990). Measured burn: **36 cards/min ≈ $0.48/hour**;
finishing would have taken **~16.7 hours and ~$8.0**. Nothing written was lost —
the build is resumable by `law_book_id`.

> ### It was an unbounded restart loop, not a one-shot overnight job
>
> Killing the processes was **not** enough — the build came back three times.
> There were **two independent respawn mechanisms**:
>
> **1. `scripts/overnight_p1_watchdog.bat`** (untracked, the real culprit):
>
> ```bat
> :loop
> "...\.venv\Scripts\python.exe" "...\scripts\overnight_p1.py"
> echo %DATE% %TIME% overnight exited %ERRORLEVEL%>> cache\overnight_p1.log
> timeout /t 30 /nobreak >nul
> goto loop
> ```
>
> An infinite loop with **no exit condition and no error handling** — it
> relaunches the whole pipeline every 30 s forever, ignoring the exit code. Run
> via `cmd /K`, so killing the Python children just meant a fresh spend cycle 30
> seconds later.
>
> **2. Scheduled task `IraqiLawRAG-OvernightP1`**, hourly repeating trigger,
> pointing at that same watchdog batch. It was **re-registered at 02:46:07**,
> minutes after being deleted at ~02:32.
>
> `Ctrl-C`, closing the terminal, or killing `python.exe` would none of them have
> stopped this. Roughly **420 extra cards were bought during the review itself.**
>
> **Resolution:** task unregistered (deleted, not merely disabled), watchdog
> renamed to `overnight_p1_watchdog.bat.disabled`, process tree killed, stale
> `cache/overnight_p1.lock` cleared. Verified clean: no task, no build process,
> **card count frozen at 2,278 with zero delta over 50 s**. The Masadir `uvicorn`
> demo was deliberately left running.
>
> Restore only after the Section 7 preconditions:
> `Rename-Item scripts\overnight_p1_watchdog.bat.disabled scripts\overnight_p1_watchdog.bat`
>
> **Caution:** the task was re-created *during* this review, commits `8a01de8` /
> `52b2b20` landed mid-session, and `ask.py` / `common.py` / `eval_recall.py` /
> `law_registry.py` became modified while it was being written. Another agent or
> shell session is active in this repo and may recreate both mechanisms. Confirm
> it is stopped before trusting the burn to stay stopped.
>
> **There is also a failure-driven amplifier.** `overnight_p1.py:272` returns
> non-zero if *any* stage failed, and stage C (`embed_articles`) fails
> deterministically on the HTTP 400 bug in Section 4. A permanently-failing final
> stage inside a 30-second restart loop means the pipeline re-enters stage A and
> buys more cards forever. **Fix the 400 before any scheduled re-run.**

Three findings drive the verdict, in order of severity:

1. **The benefit is currently unmeasurable.** The public repo's store is
   sample-scale (`lancedb/laws.lance` = 0.2 MB, 34 chunks; `cache/law_registry.jsonl`
   = 32 sample rows). `eval_recall.py` auto-selects the **7-case sample suite**
   when `n_chunks < 200`. There is no environment on this machine where the
   12-case suite can run against the real corpus, so **$8 was being spent on an
   artifact whose effect on recall cannot be observed.**
2. **The spend order is worst-first.** `collect_candidates()` walks the corpus in
   `lawBookID` order, which is roughly chronological. Of 1,428 cards analysed,
   **1,037 (73%) are 1910s–1930s laws; only 4 are from the 2000s.** `--priority`
   exists and was **not** used (build log: `Candidates: 37990`). The head of the
   real query distribution (عقوبات 111/1969, أحوال شخصية 188/1959, عمل, مدني,
   post-2010 laws) is at the **end** of a 36,000-item queue.
3. **Cards measurably risk a recall *regression*** by reintroducing AGENTS.md
   bug #1 at the alias layer, bypassing the exact guard built to prevent it
   (Section 5 below).

**Correction to a premise:** there is no recorded **12/12** result anywhere in
this repo. `EVAL_CASES` contains 12 cases, but no pass number has ever been
recorded for it. The only measured figure is `AGENTS.md` §6: **hybrid recall@6
9/9 (100%) on 9 cases**, against the *Masadir* store. Treat 12/12 as
*unmeasured*, not *achieved* — which strengthens the argument: at 9/9 the metric
is already saturated, so new structure has almost no headroom to prove itself on
and plenty of room to break things.

---

## 2. What already exists (and may make more spend redundant)

Everything below is **already paid for or free**, and is wired into retrieval.

| Asset | Scale / location | Cost status |
|---|---|---|
| Chunk corpus embeddings | `C:\iraqi-law-rag\lancedb\laws.lance` — **668.4 MB**, 99,377 chunks | **PAID** (~$0.50–0.75) |
| Title / route vectors | `law_routes.lance` — **161.7 MB**, ~37,990 in-force titles + aliases | **PAID** (~$0.02) |
| Full law registry | `C:\iraqi-law-rag\cache\law_registry.jsonl` — **37,990 rows**, 55.5 MB | **PAID** |
| **Article index** | `cache/article_index.jsonl` — **188,796 rows** (161,455 `defines` + 27,341 `mentions`) | **$0 — deterministic, no LLM** |
| QueryPlan | 9 shapes, weighted RRF, per-leg quotas, per-law diversity | **$0**, wired |
| Instrument-phrase routing | `extract_instrument_phrases` + scored title matching | **$0**, wired |
| Seed aliases | `SEED_ALIAS_RULES` — **only 5 rules** | **$0**, wired, authoritative |
| Exact-article fast path | `_article_exact_from_index` → `lookup_defines` | **$0**, and needs **no article vectors** |
| BM25 / FTS hybrid | Arabic-tuned LanceDB FTS fused by RRF | **PAID** |

The decisive point: the **article index — the single largest precision win in
the P0/P1 plan — is already built, at full corpus scale, for $0.** It was
produced deterministically with no LLM calls. The exact-article fast path and
the `article_exact` leg both read it directly and work **without** the
`articles` vector table.

**Hidden duplicate cost to avoid:** `DB_DIR` is hardcoded (`common.py:24`) to
`ROOT/"lancedb"` with **no env override**. As written, making the public repo
measurable means re-ingesting the full corpus into its own store — **paying the
$0.50–0.75 chunk embed a second time** for vectors that already exist next door.
Copying or repointing at the Masadir store costs **$0**.

---

## 3. Marginal value of law cards vs cheaper alternatives

Measured over the 1,428 real cards built (`scripts/analyze_card_value.py`):

| Alias category | Count | Share |
|---|---|---|
| Verbatim substring of the law title | 292 | **12.6%** |
| Token-subset of the title (no new tokens) | 805 | **34.6%** |
| **Redundant subtotal** | **1,097** | **47.2%** |
| Contains ≥1 token absent from the title | 1,228 | 52.8% |
| **Total aliases** (from 1,428 cards) | **2,325** | 1.6 / card |

**Nearly half of every dollar spent buys aliases that are restatements of the
title** — a string already covered, for $0 more, by the paid `law_routes` title
vectors and by `title_like` matching. And the "novel" half is not automatically
good; inspection shows many are wrong (Section 5) or trivial re-orderings.

Cheaper alternatives, ranked by value per dollar:

| Alternative | Cost | Assessment |
|---|---|---|
| **Expand `SEED_ALIAS_RULES`** | **$0** | **Best value available.** Only **5 rules** exist today, covering عقوبات / أحوال شخصية / عمل / تعليم أهلي. Hand-writing 30–50 rules for the actual head of the query distribution is an afternoon of typing, is *scored* (recency, قانون-vs-نظام, تعديل penalty), stays authoritative over cards, and is reviewable by a human. Cards are an expensive, unreviewable, unscored substitute for work better done by hand at this scale. |
| **Instrument phrases** | **$0** | Already generalises across all 37,990 titles; needs no per-law artifact. |
| **Title vectors** | **PAID** | Already covers the 47% of card aliases that are title restatements. |
| **Card aliases for the long tail** | ~$8 | Worst value: pays most for the laws nobody queries, and carries the regression risk. |

Note also that the non-alias card fields — `scope_summary`, `subject_tags`,
`likely_questions`, `title_en` — are **built only**. Nothing at query time reads
them; they are speculative UI inventory. So today roughly **half the tokens in
every card are paying for output with no consumer at all**, on top of the 47% of
aliases with no marginal signal.

---

## 4. Marginal value of article embeddings vs the $0 path

| Capability | Needs `articles` vectors? |
|---|---|
| `article_exact` leg | **No** — `article_index` defines is queried first |
| Exact-article fast path (zero-generation lookup) | **No** |
| `mentions` vs `defines` disambiguation (the P0 correctness fix) | **No** — pure metadata |
| Semantic search *over article bodies* (DEFINITIONAL / law-scoped) | **Yes** — falls back to arts 1–3 heuristic without it |

So the **precision win is already banked at $0.** Article vectors buy exactly one
incremental thing: semantic (not exact-match) search at article granularity,
which mainly serves the `DEFINITIONAL` shape. That is a real but narrow gain, and
`eval_recall.py`'s current gold is **recall-only with no precision measure** —
i.e. the harness cannot see the improvement article vectors are supposed to
deliver.

**This spend is bug-blocked, not budget-blocked.** The run died at
~1,088 / 161,455 articles with `HTTPError: 400` from `/embeddings`. Two causes,
both small fixes:

1. **No length cap on article text.** `embed_articles.py:172,185` embeds the full
   `defines` span verbatim. Unlike `ingest.py` (which chunks to ~2,500 chars), a
   long or mis-parsed article span can exceed bge-m3's ~8,192-token limit → HTTP 400.
2. **The bisect fallback is unreachable for this error.**

```253:262:C:\iraqi-legislation-rag\embed_articles.py
        except requests.exceptions.HTTPError:
            raise
        except Exception:
            if attempt >= 3:
                raise
            if len(texts) > 1:
                mid = len(texts) // 2
                return call_api(texts[:mid], attempt + 1) + \
                       call_api(texts[mid:], attempt + 1)
            raise
```

`HTTPError` is re-raised **before** the split-and-retry logic, so one oversized
article kills the whole run instead of being isolated. Fix ≈ 4 lines: cap text
(~6,000 chars) and let 400 fall through to the bisect. Do this **before**
spending, or the $1 buys another crash.

Also note the store this writes into is inconsistent: a full-corpus `articles`
table alongside a **34-chunk sample `laws` table**.

---

## 5. Risk: cards can make retrieval *worse* (AGENTS bug #1, reintroduced)

This is the finding that turns "low value" into "negative value."

`AGENTS.md` bug #1 documents that Iraqi legal naming reuses phrases like
«قانون تعديل قانون العقوبات» across eras, so 1920s–40s amendment acts
impersonate the modern code. The seed scorer defends against this explicitly:

```268:272:C:\iraqi-legislation-rag\law_registry.py
                if "تعديل" in tn and "تعديل" not in qn:
                    score -= 30
                elif any(x in tn for x in ("بيان تصحيح", "تعليمات", "قرار")):
                    if not any(x in qn for x in ("تعليمات", "قرار", "بيان")):
                        score -= 14
```

**Card aliases bypass that penalty entirely** — they are appended as a raw set,
unscored:

```285:290:C:\iraqi-legislation-rag\law_registry.py
    # Card / lexicon aliases fill gaps after seeds (deterministic fallback).
    card_ids, _ = laws_matching_card_aliases(question)
    for lid in card_ids:
        if lid not in seen:
            seen.add(lid)
            out.append(lid)
```

Measured false-friend rate (`scripts/analyze_card_risk.py`, 1,637 cards):

| Metric | Value |
|---|---|
| Titles carrying a تعديل / بيان / قرار / تصديق marker | **637 (38.9%)** |
| Aliases that **strip** the marker | **461** |
| …and claim a bare **base-code** name | **16 (~1.0% of cards)** |
| Extrapolated to 37,990 cards | **~370 false-friend aliases on the highest-traffic codes** |

Real examples now sitting in `cache/law_cards.jsonl`:

| Law (title) | LLM alias | Why it's harmful |
|---|---|---|
| `قانون رقم (٨٣) لسنة ٢٠٠١ (تعديل قانون المرافعات المدنية)` | **«قانون الأحوال الشخصية»** | Routes personal-status questions to a civil-procedure amendment. Simply wrong. |
| `قانون تعديل قانون الاحوال الشخصية رقم ١٨٨ لسنة ١٩٥٩` | **«قانون المواريث»** | An *amendment* claims the inheritance-law name, competing with base 188/1959. |
| `بيان - الموضوع قانون العمل رقم ٧٢ لسنة ١٩٣٦` | **«قانون العمل»** | A 1936 bayan claims the bare name of the current Labour Law. |
| `تعديل قانون اصول المحاكمات الجزائية` | **«قانون السمسرة»** | Fabricated subject matter. |
| `قانون تعديل قانون شركات التامين رقم ٤٣ لسنة ١٩٤١` | **«قانون الخدمة المدنية»** | Wrong domain entirely. |

Worse, the damage is not confined to a low-priority leg. `strongest_seed_alias_len`
(`law_registry.py:321-334`) folds card alias length into the routing-confidence
signal, and a long alias promotes the query to `NAMED_INSTRUMENT` — the shape
that by design puts routed chunks **ahead of hybrid**. `AGENTS.md` §6 states the
opposite is required for topical asks: *"topical asks (`low`, e.g. عقوبة السرقة)
keep hybrid first so routes don't crowd out BM25."* **A single bogus long card
alias can flip a topical question into named-instrument mode and push a 1936
bayan ahead of the BM25 hits that currently score 9/9.**

Secondary waste: **parse failures double-bill.** 11 failures in 954 logged calls;
each burns the primary model *and* the fallback (`google/gemini-2.5-flash-lite`
→ `deepseek/deepseek-v4-flash`), sometimes producing **no card at all**.

---

## 6. Risk that the overnight run wastes money without improving `eval_recall`

**This risk was ~100%, not speculative.** Four independent reasons:

1. **No measurement is possible.** The public store is sample-scale, so
   `eval_recall.py` runs the 7-case sample suite against 34 chunks. Cards cannot
   be shown to help or hurt in this repo today.
2. **No A/B switch exists.** There is no `--no-cards` flag anywhere. Even with a
   full store, you could not produce a with/without comparison without a code
   change — you would have to move the cache file by hand.
3. **The metric is already saturated.** The recorded result is **9/9 (100%)**. A
   metric at ceiling has **zero headroom to demonstrate gain** and can only move
   **down**. Spending $8 to influence a metric that cannot improve is
   definitionally poor value.
4. **The gold set does not exercise card routing.** The cases are topical
   (`عقوبة السرقة`, `الإجازة السنوية`) or exact-article (`المادة 741 القانون
   المدني`) — shapes served by BM25, article index, and the 5 existing seed
   rules. The one alias-dependent case (`private_education`) is already covered
   by hand-written seed rules #1–2. **No gold case would change if all 37,990
   cards existed.**

Add the ordering problem (73% of spend on pre-1940 laws) and the conclusion is
that the overnight run would have spent ~$8 and ~17 hours to produce an artifact
that is unmeasurable, unexercised by the eval, and carrying a quantified
regression risk to the one number that is currently perfect.

---

## 7. Recommended spend order

| # | Action | Cap | Verdict |
|---|---|---|---|
| 0 | Stop the card build; unregister the task **and** the watchdog loop | **–$8 saved** | **DONE** (stopped at 2,278 cards) |
| 1 | Make the full store measurable — repoint/copy the paid Masadir `lancedb` instead of re-ingesting | **$0** | **DO FIRST** |
| 2 | Add `--no-cards`; record real baselines: 12-case suite, hybrid vs `--vector-only`, cards vs no-cards | **<$0.01** | **GATE** |
| 3 | Hand-write 30–50 `SEED_ALIAS_RULES` for the actual head of the distribution | **$0** | **BEST VALUE** |
| 4 | Fix `embed_articles.py` (cap text, bisect on 400), then embed | **$1.00** | **GO-CONDITIONAL** on step 2 showing a definitional/precision gap |
| 5 | Cards for `--priority` only (~6,266 laws; AGENTS §4), **after** adding a تعديل/بيان penalty to card aliases | **$1.40** | **GO-CONDITIONAL** on step 2 showing routing misses seeds can't fix |
| 6 | Cards for the remaining ~31,700 long-tail laws | ~$7 | **NO-GO** — revisit only with a precision metric and evidence of demand |

Total conditional exposure: **~$2.40**, versus the ~$8–15 originally planned.
~$0.50 has already been spent on the 2,278 existing cards; treat that as sunk and
**keep the file** (it is harmless while unused, and useful as a test corpus for
the alias-penalty fix).

**Before re-registering any scheduled task or watchdog:** add `--priority` to the
stage-A command in `scripts/overnight_p1.py:231`, fix the stage-C HTTP 400, give
the trigger a **single run with no repetition**, and put a hard iteration cap in
the batch loop. As configured it was an unbounded spend loop, not an overnight
job.

### Concrete next 3 actions with $ caps

**Action 1 — make the corpus measurable. Cap: $0.**
Add an `IRAQI_RAG_DB_DIR` env override to `common.py:24`, point it at
`C:\iraqi-law-rag\lancedb` (or copy `laws.lance` + `law_routes.lance` across —
668 MB + 162 MB, free), and copy the 37,990-row `law_registry.jsonl` over the
32-row sample. Confirm `eval_recall.py` selects the **12-case** suite. Do not
re-ingest; those vectors are already paid for.

**Action 2 — establish the baseline you never recorded. Cap: $0.01.**
Add `--no-cards` to `ask.py` / `eval_recall.py`. Then record four numbers in
`AGENTS.md` §6: 12-case hybrid, 12-case `--vector-only`, 12-case with the 2,278
cards, 12-case without. Each run is ~12 question embeddings and **no answer
calls**. If cards do not move recall — or move it **down**, which the false-friend
data predicts — the $8 decision is settled permanently by evidence instead of
estimate.

**Action 3 — buy the $0 win first, then the $1 one. Cap: $1.00.**
Expand `SEED_ALIAS_RULES` from 5 to 30–50 hand-written rules (free, scored,
reviewable, authoritative over cards) and re-run Action 2. Only then fix
`embed_articles.py` (cap text ~6,000 chars; let HTTP 400 reach the bisect path)
and spend up to **$1.00** on article vectors — and only if Action 2 exposed a
definitional/precision gap that article-level semantics would actually close.

### Before any further card spend, three preconditions

1. Add a **تعديل / بيان / قرار penalty** to `laws_matching_card_aliases` so card
   aliases are *scored* like seeds instead of raw-appended.
2. Reject any card alias that claims a **base-code name** while its title carries
   an amendment marker (`scripts/analyze_card_risk.py` already detects these).
3. Always pass **`--priority`**. Never card in `lawBookID` order again.

---

## Appendix — evidence

Reproducible analyses written for this review:

- `scripts/analyze_card_value.py` — alias redundancy vs title; decade distribution
- `scripts/analyze_card_risk.py` — amendment-marker false-friend rate

Key measurements: 2,278 cards built (~6.0% of 37,990) · mean **$0.00022/card**
over 954 logged calls · observed **36 cards/min ≈ $0.48/hour** · remaining
~35,700 cards ≈ **16.5 h / ~$7.9** · aliases **47.2% redundant** with the title ·
**38.9%** of carded titles carry an amendment marker · **~1.0%** of cards hijack a
base-code name · article index **188,796 rows at $0** · article embed crashed at
**~1,088 / 161,455** with HTTP 400.

---

## Budget note 2026-07-31 night

Hard cap tonight: **~$10 OpenRouter credits remaining** (top-up morning).

- **No** full ~38k law-card corpus. **No** `overnight_p1` / watchdog / scheduled re-run.
- Prefer **$0** baselines (DB_DIR override, `--no-cards` A/B, seed expansion) first.
- After measurement, at most **one** targeted experiment ≤ **~$2** (priority cards
  ~$0.50 / 200, or article-embed fix + `--limit 2000` ~$0.20). Leave **~$8+**
  headroom for morning.

---

## Follow-up baselines (2026-07-31, cheap Auto)

Actions 1–3 from §7 done without further card spend:

1. `IRAQI_RAG_DB_DIR` + README docs — eval pointed at Masadir `lancedb` (99,377 chunks).
2. `--no-cards` / `IRAQI_RAG_NO_CARDS` wired through `common.use_law_cards` →
   `laws_matching_card_aliases` / ask / eval.
3. `SEED_ALIAS_RULES` expanded **5 → 46**.

Recorded recall@6 (12-case suite) — see [`docs/BASELINES_2026-07-31.md`](BASELINES_2026-07-31.md):

| Mode | Cards | Score |
|------|-------|------:|
| hybrid | on | 11/12 |
| hybrid | off | 11/12 |
| vector-only | on | 11/12 |
| vector-only | off | 12/12 |

**Cards did not improve hybrid recall.** Vector-only was better without cards on
this run. Continues to support **NO-GO** on finishing the 38k card build until a
precision metric and a scored card-alias penalty exist.
