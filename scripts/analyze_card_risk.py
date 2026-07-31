#!/usr/bin/env python3
"""
Throwaway analysis: measure the FALSE-FRIEND rate in LLM card aliases.

AGENTS.md bug #1: Iraqi legal naming reuses phrases like «قانون تعديل قانون
العقوبات» across eras, so title-substring matching pulls 1920s-40s amendment
acts for the modern code. SEED_ALIAS_RULES penalise تعديل/بيان/قرار titles by
-30/-14 for exactly this reason.

Card aliases are appended AFTER seeds with no such scoring. So: how often does
a card on an amendment/bayan/qarar title emit an alias that DROPS the amendment
marker and claims the bare name of a base code?
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

CARDS = Path(r"C:\iraqi-legislation-rag\cache\law_cards.jsonl")

_AR_DIAC = re.compile(r"[\u064B-\u0652\u0670\u0640]")

# Amendment / secondary-instrument markers (mirrors seed penalties).
MARKERS = ("تعديل", "بيان", "قرار", "تصديق", "الغاء", "ذيل", "تفسير")

# Bare names of head-of-distribution base codes users actually ask about.
BASE_CODES = (
    "قانون العقوبات", "قانون الاحوال الشخصية", "قانون العمل",
    "القانون المدني", "قانون المرافعات المدنية",
    "قانون اصول المحاكمات الجزائية", "قانون الشركات",
    "قانون المواريث", "قانون التجارة", "قانون الخدمة المدنية",
)


def norm(s: str) -> str:
    s = _AR_DIAC.sub("", s or "")
    s = (s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
           .replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و")
           .replace("ئ", "ي"))
    for i, d in enumerate("٠١٢٣٤٥٦٧٨٩"):
        s = s.replace(d, str(i))
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def main() -> None:
    if not CARDS.exists():
        sys.exit(f"missing {CARDS}")

    n = 0
    n_marker_titles = 0
    stripped_marker = 0          # alias drops the amendment marker entirely
    claims_base = 0              # ...and claims a bare base-code name
    examples: list[str] = []
    base_hits = Counter()

    nbases = [norm(b) for b in BASE_CODES]

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
            if 90000 <= lid <= 90099:
                continue
            n += 1
            title = c.get("title") or ""
            ntitle = norm(title)
            title_markers = [m for m in MARKERS if norm(m) in ntitle]
            if not title_markers:
                continue
            n_marker_titles += 1
            for a in (c.get("colloquial_aliases") or []):
                na = norm(a)
                if not na:
                    continue
                if any(norm(m) in na for m in title_markers):
                    continue        # marker preserved — honest alias
                stripped_marker += 1
                for b, nb in zip(BASE_CODES, nbases):
                    if nb in na or na in nb:
                        claims_base += 1
                        base_hits[b] += 1
                        if len(examples) < 20:
                            examples.append(f"  {lid}  [{title[:58]}]\n"
                                            f"        -> «{a}»  (claims: {b})")
                        break

    print(f"cards analysed:                              {n}")
    print(f"titles carrying an amendment/bayan marker:   {n_marker_titles} "
          f"({100*n_marker_titles/max(n,1):.1f}%)")
    print(f"aliases that STRIP the marker:               {stripped_marker}")
    print(f"  ...and claim a bare BASE CODE name:        {claims_base}")
    if n_marker_titles:
        print(f"  base-code false-friend rate per marker title: "
              f"{100*claims_base/n_marker_titles:.1f}%")
    print(f"\nwhich base codes get hijacked:")
    for b, cnt in base_hits.most_common():
        print(f"  {cnt:4d}  {b}")
    print(f"\nexamples (these route real user questions to the wrong law):")
    for e in examples:
        print(e)


if __name__ == "__main__":
    main()
