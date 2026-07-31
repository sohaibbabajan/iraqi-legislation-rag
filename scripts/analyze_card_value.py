#!/usr/bin/env python3
"""
Throwaway analysis: how much NEW routing signal do LLM law cards add over the
already-paid title vectors + deterministic phrase matching?

Measures, over cache/law_cards.jsonl:
  - alias count distribution
  - fraction of aliases that are substrings of the law title (redundant with
    title vectors / title LIKE, which cost $0 more)
  - fraction that are token-subsets of the title (near-redundant)
  - the genuinely novel aliases (contain tokens absent from the title)
  - year distribution of the laws carded so far (is the spend hitting the
    head of the query distribution, or 1920s-30s long tail?)
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

CARDS = Path(r"C:\iraqi-legislation-rag\cache\law_cards.jsonl")

_AR_DIAC = re.compile(r"[\u064B-\u0652\u0670\u0640]")


def norm(s: str) -> str:
    s = _AR_DIAC.sub("", s or "")
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
           .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و")
           .replace("ئ", "ي"))
    for i, d in enumerate("٠١٢٣٤٥٦٧٨٩"):
        s = s.replace(d, str(i))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


_STOP = {"قانون", "نظام", "تعليمات", "بيان", "رقم", "لسنه", "لسنة", "المعدل",
         "تعديل", "ال", "في", "و", "من", "على", "العراقي", "العراق"}


def toks(s: str) -> set[str]:
    return {t for t in norm(s).split() if t and t not in _STOP and not t.isdigit()}


def main() -> None:
    if not CARDS.exists():
        sys.exit(f"missing {CARDS}")

    n_cards = 0
    n_alias = 0
    substr_redundant = 0
    token_subset = 0
    novel: list[tuple[str, str]] = []
    alias_counts = Counter()
    years = Counter()
    sample_novel: list[tuple[str, str]] = []

    with CARDS.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            lid = int(c.get("law_book_id") or 0)
            if 90000 <= lid <= 90099:      # synthetic sample fixtures
                continue
            n_cards += 1
            title = c.get("title") or ""
            ntitle = norm(title)
            ttoks = toks(title)
            aliases = c.get("colloquial_aliases") or []
            alias_counts[len(aliases)] += 1
            m = re.search(r"(1[89]\d\d|20[0-2]\d)", norm(title))
            if m:
                years[int(m.group(1)) // 10 * 10] += 1
            for a in aliases:
                n_alias += 1
                na = norm(a)
                if not na:
                    continue
                if na in ntitle:
                    substr_redundant += 1
                elif toks(a) <= ttoks:
                    token_subset += 1
                else:
                    novel.append((title, a))
                    if len(sample_novel) < 25:
                        sample_novel.append((title[:55], a))

    print(f"cards analysed (excl. sample fixtures): {n_cards}")
    print(f"total colloquial_aliases:              {n_alias}")
    if n_alias:
        print(f"  substring of title (redundant):      {substr_redundant} "
              f"({100*substr_redundant/n_alias:.1f}%)")
        print(f"  token-subset of title (near-redundant): {token_subset} "
              f"({100*token_subset/n_alias:.1f}%)")
        print(f"  genuinely novel tokens:              {len(novel)} "
              f"({100*len(novel)/n_alias:.1f}%)")
    print(f"\naliases per card: {dict(sorted(alias_counts.items()))}")
    print(f"\nlaw decade distribution of cards built so far:")
    for dec in sorted(years):
        print(f"  {dec}s: {years[dec]}")
    print(f"\nsample 'novel' aliases (judge quality yourself):")
    for t, a in sample_novel:
        print(f"  [{t}] -> {a}")


if __name__ == "__main__":
    main()
