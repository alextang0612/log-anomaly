#!/usr/bin/env python3
"""
Is this dataset actually hard, or does something trivial solve it?

Run this on ANY dataset before building a model for it. Week 2 learned the
expensive way that every integrity check can pass while the task is a lookup
table -- block counts, label joins and anomaly rates were all exactly correct
on HDFS while a Python dict scored F1 0.92.

FOUR DIAGNOSTICS
----------------
1. SEQUENCE DIVERSITY
   How many distinct behaviours exist? HDFS: 17,458 distinct across 402,549
   training blocks. If the number is small relative to the dataset, every
   future example repeats a past one.

2. VERBATIM OVERLAP
   What share of evaluation sequences appeared character-for-character in
   training? HDFS: 99.2%. Above ~90% the task is recall, not generalisation.

3. LOOKUP BASELINE
   Memorise training sequences with their majority label; look up each
   evaluation sequence. No model, no features. Whatever this scores is the
   floor that any learned model must clearly beat to have earned anything.

4. NEW-EVENT-TYPE BASELINE
   Flag any sequence containing a template never seen in training. On BGL
   grouped by sequence identifier this scores F1 0.988 (Landauer et al., FSE
   2024) because normal and anomalous event types are nearly disjoint -- a
   different trivial solution from HDFS's, equally fatal to an accuracy
   comparison.

VERDICT
-------
The script prints a judgement, but the judgement is a prompt to think, not an
answer. Read the numbers.

Usage:
    python scripts/saturation_check.py                              # HDFS
    python scripts/saturation_check.py --splits data/bgl_splits \\
        --templates data/bgl_processed/templates.csv                # BGL
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

csv.field_size_limit(100_000_000)


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="data/splits")
    ap.add_argument("--templates", default="data/processed/templates.csv")
    ap.add_argument("--eval", default="val", choices=["val", "test"])
    args = ap.parse_args()

    tr_path = os.path.join(args.splits, "train.csv")
    ev_path = os.path.join(args.splits, f"{args.eval}.csv")
    for p in (tr_path, ev_path):
        if not os.path.exists(p):
            print(f"ERROR: {p} not found -- run make_split.py first.", file=sys.stderr)
            return 1

    tr, ev = load(tr_path), load(ev_path)
    ytr = [1 if r["label"] == "Anomaly" else 0 for r in tr]
    yev = [1 if r["label"] == "Anomaly" else 0 for r in ev]

    print("=" * 74)
    print(f"SATURATION CHECK   train {len(tr):,}  {args.eval} {len(ev):,}")
    print(f"  anomaly rate: train {100*sum(ytr)/max(len(tr),1):.2f}%  "
          f"{args.eval} {100*sum(yev)/max(len(ev),1):.2f}%")
    print("=" * 74)

    # ---- 1. diversity --------------------------------------------------
    tr_seqs = [r["sequence"] for r in tr]
    ev_seqs = [r["sequence"] for r in ev]
    tr_set = set(tr_seqs)
    uniq_pct = 100 * len(tr_set) / max(len(tr_seqs), 1)

    print("\n1. SEQUENCE DIVERSITY")
    print(f"   distinct training sequences : {len(tr_set):,} / {len(tr_seqs):,} "
          f"({uniq_pct:.1f}% unique)")
    print(f"   HDFS reference              : 4.7% unique  (highly repetitive)")
    print(f"   BGL reference               : 39.8% unique (diverse)")

    # ---- 2. overlap ----------------------------------------------------
    seen = sum(1 for s in ev_seqs if s in tr_set)
    overlap = 100 * seen / max(len(ev_seqs), 1)
    print("\n2. VERBATIM OVERLAP")
    print(f"   {args.eval} sequences seen in training: {seen:,} / {len(ev_seqs):,} "
          f"({overlap:.1f}%)")

    # ---- 3. lookup -----------------------------------------------------
    by_seq = defaultdict(list)
    for r in tr:
        by_seq[r["sequence"]].append(r["label"])
    lut = {s: ("Anomaly" if l.count("Anomaly") * 2 > len(l) else "Normal")
           for s, l in by_seq.items()}

    tp = fp = fn = unseen = unseen_anom = 0
    for r, y in zip(ev, yev):
        g = lut.get(r["sequence"])
        if g is None:
            unseen += 1
            unseen_anom += y
            g = "Normal"
        p = 1 if g == "Anomaly" else 0
        tp += 1 if (y and p) else 0
        fp += 1 if (not y and p) else 0
        fn += 1 if (y and not p) else 0
    lp, lr, lf = prf(tp, fp, fn)

    print("\n3. LOOKUP BASELINE  (pure memorisation, no model)")
    print(f"   precision {lp:.4f}   recall {lr:.4f}   F1 {lf:.4f}")
    print(f"   unseen sequences: {unseen:,}, of which anomalous {unseen_anom:,}"
          + (f" ({100*unseen_anom/unseen:.1f}%)" if unseen else ""))
    if unseen:
        base = 100 * sum(yev) / max(len(ev), 1)
        lift = (100 * unseen_anom / unseen) / max(base, 1e-9)
        print(f"   novelty lift: {lift:.1f}x the base rate "
              f"-- an unfamiliar sequence is {lift:.1f}x more likely to be anomalous")

    # ---- 4. new event type ---------------------------------------------
    tr_tmpl = set()
    for s in tr_seqs:
        tr_tmpl.update(s.split())

    tp = fp = fn = 0
    for r, y in zip(ev, yev):
        p = 1 if (set(r["sequence"].split()) - tr_tmpl) else 0
        tp += 1 if (y and p) else 0
        fp += 1 if (not y and p) else 0
        fn += 1 if (y and not p) else 0
    np_, nr, nf = prf(tp, fp, fn)

    print("\n4. NEW-EVENT-TYPE BASELINE  (flag any unseen template)")
    print(f"   precision {np_:.4f}   recall {nr:.4f}   F1 {nf:.4f}")
    print(f"   templates in training: {len(tr_tmpl)}")
    print(f"   BGL reference (sequence-id grouping): F1 0.988")

    # ---- verdict -------------------------------------------------------
    best = max(lf, nf)
    print("\n" + "=" * 74)
    print("VERDICT")
    print("=" * 74)
    print(f"   best trivial baseline: F1 {best:.4f}"
          + ("  (lookup)" if lf >= nf else "  (new-event-type)"))

    if best > 0.95:
        print("""
   SATURATED. A trivial method already scores above 0.95, so there is no
   room for a model to demonstrate anything. Do NOT spend weeks training
   here hoping to win -- the headroom is smaller than the noise.

   Options: change the grouping (time windows instead of identifiers, or
   shorter windows), move to a harder dataset, or keep this one purely as
   a cost-comparison surface where accuracy is deliberately held constant.""")
    elif best > 0.85:
        print("""
   MOSTLY SOLVED. A trivial method is close to the ceiling. A model can
   still show something, but the margin will be small and you must report
   the trivial baseline alongside it or the result is misleading.""")
    else:
        print("""
   GENUINELY HARD. Trivial methods leave real headroom, so model and
   design choices can actually be measured here. This is where ablations
   mean something.

   Keep the tripwire: if your model lands far above the published numbers
   for this dataset and configuration, suspect leakage before celebrating.""")

    if overlap > 90:
        print(f"\n   Note: {overlap:.1f}% verbatim overlap means the task is mostly")
        print("   recall of known patterns rather than generalisation, regardless")
        print("   of what the F1 numbers say.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
