#!/usr/bin/env python3
"""
Chronological train/val/test split -- the most important correctness decision
in the whole project.

Logs are time-ordered. A RANDOM split lets the model train on events that
happened AFTER the ones it is tested on, which is studying tomorrow's newspaper
to predict today. Scores come out beautiful and mean nothing. This is a
documented flaw in published work on log anomaly detection, so getting it right
is a differentiator, not just hygiene.

Three splits, not two:
  train  earliest   fit the model
  val    middle     tune the decision threshold, pick between models
  test   latest     touch ONCE, at the very end

Why val exists: in week 7 you tune the classification threshold. If you tune it
on test, test is no longer held out and every number after that is optimistic.

Boundary handling: the split point is advanced until the timestamp actually
changes, so no single moment in time is split across two sets. Then it asserts
strict separation -- the script fails loudly rather than producing a quietly
leaky split.

Usage:
    python scripts/make_split.py
    python scripts/make_split.py --train 0.6 --val 0.2
"""

import argparse
import csv
import os
import sys

csv.field_size_limit(10_000_000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default="data/processed/sessions.csv")
    ap.add_argument("--out", default="data/splits")
    ap.add_argument("--train", type=float, default=0.70)
    ap.add_argument("--val", type=float, default=0.15)
    args = ap.parse_args()

    if args.train + args.val >= 1.0:
        print("ERROR: train + val must leave room for test.", file=sys.stderr)
        return 1
    if not os.path.exists(args.sessions):
        print(f"ERROR: {args.sessions} not found -- run parse_logs.py first.", file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)

    with open(args.sessions, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = [r for r in reader if r["label"] in ("Normal", "Anomaly")]

    if "first_ts" not in fields:
        print("ERROR: sessions.csv has no first_ts column.", file=sys.stderr)
        return 1

    # "081109 203518" sorts correctly as a string (fixed-width, zero-padded).
    rows.sort(key=lambda r: r["first_ts"])
    n = len(rows)

    def snap(idx):
        """Advance idx until the timestamp changes, so one instant is never
        split across two sets."""
        if idx <= 0 or idx >= n:
            return idx
        ts = rows[idx - 1]["first_ts"]
        while idx < n and rows[idx]["first_ts"] == ts:
            idx += 1
        return idx

    i_train = snap(int(n * args.train))
    i_val = snap(int(n * (args.train + args.val)))

    splits = {
        "train": rows[:i_train],
        "val": rows[i_train:i_val],
        "test": rows[i_val:],
    }

    # ---- the check that makes this trustworthy -------------------------
    order = ["train", "val", "test"]
    for a, b in zip(order, order[1:]):
        if not splits[a] or not splits[b]:
            print(f"ERROR: split '{a}' or '{b}' is empty -- adjust ratios.", file=sys.stderr)
            return 1
        last = max(r["first_ts"] for r in splits[a])
        first = min(r["first_ts"] for r in splits[b])
        if last >= first:
            print(f"ERROR: {a} ends at {last}, {b} starts at {first} -- OVERLAP.",
                  file=sys.stderr)
            return 1

    for name, rs in splits.items():
        path = os.path.join(args.out, f"{name}.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rs)

    # ---- report --------------------------------------------------------
    print("=" * 72)
    print("CHRONOLOGICAL SPLIT")
    print("=" * 72)
    print(f"  {'split':<6} {'sessions':>10} {'anomalies':>10} {'rate':>8}   time range")
    for name in order:
        rs = splits[name]
        na = sum(1 for r in rs if r["label"] == "Anomaly")
        lo = min(r["first_ts"] for r in rs)
        hi = max(r["first_ts"] for r in rs)
        print(f"  {name:<6} {len(rs):>10,} {na:>10,} {100*na/len(rs):>7.2f}%   {lo} -> {hi}")

    print()
    print("  VERIFIED: no timestamp appears in more than one split.")
    print(f"  Wrote {args.out}/train.csv, val.csv, test.csv")

    rates = [100 * sum(1 for r in splits[k] if r["label"] == "Anomaly") / len(splits[k])
             for k in order]
    if max(rates) - min(rates) > 1.5:
        print()
        print("  !! Anomaly rate differs noticeably across splits "
              f"({min(rates):.2f}% to {max(rates):.2f}%).")
        print("     This is REAL and expected -- failures cluster in time. Do not")
        print("     'fix' it by shuffling. It does mean test is a different")
        print("     distribution than train, which is exactly the hard case a")
        print("     production detector faces. Note it in the writeup.")

    print()
    print("  RULE FOR THE REST OF THE PROJECT:")
    print("    train -> fitting.  val -> every decision you make.")
    print("    test  -> run once, at the end, and report whatever it says.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
