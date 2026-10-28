#!/usr/bin/env python3
"""
Week 1 EDA: what actually distinguishes an anomalous block from a normal one?

You cannot answer that from the parse output -- the parse only proved the
plumbing works. This compares the two classes directly and hands you the
evidence to write that sentence in your own words.

Three questions it answers:

  1. Are anomalous blocks a different LENGTH than normal ones?
     If yes, a model could cheat by counting events instead of reading them.
     That is worth knowing before you believe any result.

  2. Which templates appear disproportionately in anomalous blocks?
     Presence rate per class, plus the gap between them.

  3. Which templates are MISSING from anomalous blocks?
     Absence is signal too -- a block that never gets served, or never
     completes replication, is broken by omission rather than by error.

Usage:
    python scripts/explore_sessions.py
    python scripts/explore_sessions.py --sessions data/processed_th085/sessions.csv
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

csv.field_size_limit(10_000_000)


def pctile(sorted_vals, p):
    if not sorted_vals:
        return 0
    return sorted_vals[min(int(len(sorted_vals) * p), len(sorted_vals) - 1)]


def bar(frac, width=28):
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default="data/processed/sessions.csv")
    ap.add_argument("--templates", default="data/processed/templates.csv")
    ap.add_argument("--width", type=int, default=64,
                    help="how much of each template string to print")
    args = ap.parse_args()

    for p in (args.sessions, args.templates):
        if not os.path.exists(p):
            print(f"ERROR: {p} not found -- run parse_logs.py first.", file=sys.stderr)
            return 1

    # template_id -> template text
    tmpl = {}
    with open(args.templates, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            tmpl[int(r["template_id"])] = r["template"]

    n = {"Normal": 0, "Anomaly": 0}
    lengths = {"Normal": [], "Anomaly": []}
    # how many blocks OF THIS CLASS contain template t at least once
    present = {"Normal": defaultdict(int), "Anomaly": defaultdict(int)}
    # total occurrences, for events that repeat within a block
    occurs = {"Normal": defaultdict(int), "Anomaly": defaultdict(int)}

    with open(args.sessions, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lab = r["label"]
            if lab not in n:
                continue
            seq = [int(x) for x in r["sequence"].split()] if r["sequence"] else []
            n[lab] += 1
            lengths[lab].append(len(seq))
            seen = set()
            for t in seq:
                occurs[lab][t] += 1
                seen.add(t)
            for t in seen:
                present[lab][t] += 1

    total = n["Normal"] + n["Anomaly"]
    if total == 0:
        print("No labeled sessions found.", file=sys.stderr)
        return 1

    for k in lengths:
        lengths[k].sort()

    # ---------------------------------------------------------------
    print("=" * 78)
    print("1. CLASS BALANCE AND SESSION LENGTH")
    print("=" * 78)
    print(f"  Normal   {n['Normal']:>9,}  ({100*n['Normal']/total:5.2f}%)")
    print(f"  Anomaly  {n['Anomaly']:>9,}  ({100*n['Anomaly']/total:5.2f}%)")
    print()
    print(f"  {'':9} {'min':>6} {'p25':>6} {'p50':>6} {'p75':>6} {'p90':>6} {'p99':>6} {'max':>6}")
    for k in ("Normal", "Anomaly"):
        L = lengths[k]
        print(f"  {k:<9} {L[0]:>6} {pctile(L,.25):>6} {pctile(L,.50):>6} "
              f"{pctile(L,.75):>6} {pctile(L,.90):>6} {pctile(L,.99):>6} {L[-1]:>6}")

    nm, am = pctile(lengths["Normal"], .50), pctile(lengths["Anomaly"], .50)
    print()
    if nm and abs(am - nm) / nm > 0.25:
        print(f"  !! Median length differs a lot ({nm} vs {am}).")
        print("     A model could score well by counting events alone. Check that any")
        print("     good result is not just length in disguise -- a length-only baseline")
        print("     is worth adding to your week-2 harness to find out.")
    else:
        print(f"  Median length is similar ({nm} vs {am}) -- so length alone is a weak")
        print("  signal, and whatever separates the classes is in WHICH events occur")
        print("  and in what order. That is good news: it means the sequence matters.")

    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("2. TEMPLATES BY DISCRIMINATIVE POWER")
    print("   'in N%' = share of blocks of that class containing the template >=1 time")
    print("=" * 78)
    print(f"  {'id':>4} {'norm%':>7} {'anom%':>7} {'gap':>8}  template")
    print("  " + "-" * 74)

    rows = []
    for t in sorted(set(present["Normal"]) | set(present["Anomaly"])):
        pn = present["Normal"][t] / max(n["Normal"], 1)
        pa = present["Anomaly"][t] / max(n["Anomaly"], 1)
        rows.append((pa - pn, t, pn, pa))
    rows.sort(key=lambda r: -r[0])

    for gap, t, pn, pa in rows:
        flag = ""
        if pa > 0.5 and pn < 0.01:
            flag = "  <== strong POSITIVE indicator"
        elif pn > 0.5 and pa < 0.01:
            flag = "  <== strong ABSENCE indicator"
        print(f"  {t:>4} {100*pn:>6.2f}% {100*pa:>6.2f}% {100*gap:>+7.2f}%  "
              f"{tmpl.get(t,'?')[:args.width]}{flag}")

    # ---------------------------------------------------------------
    print()
    print("=" * 78)
    print("3. WHAT THIS MEANS")
    print("=" * 78)

    pos = [(g, t, pn, pa) for g, t, pn, pa in rows if g > 0.10]
    neg = [(g, t, pn, pa) for g, t, pn, pa in rows if g < -0.10]

    if pos:
        print("\n  Present in anomalous blocks far more often than normal ones:")
        for g, t, pn, pa in pos[:8]:
            print(f"    [{t:>3}] {100*pa:5.1f}% of anomalies vs {100*pn:5.1f}% of normals")
            print(f"          {tmpl.get(t,'?')[:70]}")
    if neg:
        print("\n  MISSING from anomalous blocks (broken by omission):")
        for g, t, pn, pa in neg[-8:]:
            print(f"    [{t:>3}] {100*pa:5.1f}% of anomalies vs {100*pn:5.1f}% of normals")
            print(f"          {tmpl.get(t,'?')[:70]}")
    if not pos and not neg:
        print("\n  No template separates the classes strongly on its own.")
        print("  That means the signal is in COMBINATIONS and ORDER, not single events --")
        print("  which is the case that justifies a sequence model over bag-of-templates.")

    print()
    print("  " + "-" * 74)
    print("  Week 1 is done when you can finish this sentence without looking:")
    print("     'An anomalous block is one that ...'")
    print("  Write it in the README now. You will reuse it in week 3 to write the")
    print("  frontier-model prompt, and in week 9 to explain what the model learned.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
