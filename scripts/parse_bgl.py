#!/usr/bin/env python3
"""
Stage 1 for BGL: raw messages -> chronological time windows of template IDs.

WHY TIME WINDOWS, AND NOT SEQUENCE-IDENTIFIER GROUPING
------------------------------------------------------
BGL labels individual MESSAGES, not sessions, so sequences have to be
constructed. The choice matters enormously, and Landauer et al. (FSE 2024)
measured it:

    grouping                      new-event detector   DeepLog
    sequence identifier                  F1 0.988      F1 0.989
    30-min time windows                  F1 0.680      F1 0.682
    30-min windows, chronological        F1 0.470        --

Grouped by identifier, BGL is trivially solvable: normal and anomalous event
types are nearly disjoint sets, so "flag any event type I have not seen before"
scores 0.988 and a deep model beats it by 0.001. That configuration cannot
distinguish architectures -- the same dead end HDFS turned out to be.

Time-windowed and chronologically ordered, simple baselines collapse to
0.47-0.68. THAT is the configuration where a sequence model has room to earn
its place, which is the entire reason BGL is in this project.

A TRIPWIRE: most published work reports >0.90 on time-windowed BGL, against
the 0.68 measured above. If your numbers land above 0.90, treat it as a bug
report and check the split, the windowing, and the label assignment -- the
same discipline that caught the HDFS template merge.

LABELS
------
BGL marks each line '-' (normal) or with an alert category (KERNDTLB, APPREAD,
...). A window is ANOMALOUS if it contains at least one alert line. The alert
categories are also recorded, since which failures cluster is worth knowing.

OUTPUT
------
Identical schema to the HDFS pipeline, so make_split.py, baselines.py,
explore_sessions.py and saturation_check.py all work unchanged:

    block_id, label, first_ts, length, sequence

Usage:
    python scripts/parse_bgl.py --limit 200000       # quick check first
    python scripts/parse_bgl.py                      # full run
    python scripts/parse_bgl.py --window-seconds 600 # 10-min windows
"""

import argparse
import csv
import os
import re
import sys
import time
from array import array
from collections import defaultdict

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig
from drain3.masking import MaskingInstruction

# BGL line format (space separated):
#   <label> <unix_ts> <date> <node> <time> <node_repeat> <type> <component> <level> <content>
#   - 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 2005-06-03-15.42.50.363779 \
#     R02-M1-N0-C:J12-U11 RAS KERNEL INFO instruction cache parity error corrected
LINE_RE = re.compile(
    r"^(\S+)\s+(\d{9,11})\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)$"
)


def build_miner(sim_th: float, depth: int) -> TemplateMiner:
    """Drain3 configured for BGL.

    BGL's variable tokens differ from HDFS: node coordinates, hex addresses and
    register dumps dominate. The threshold is NOT inherited from the HDFS run --
    0.85 was tuned for HDFS and has no authority here. Inspect templates and
    retune, exactly as in week 1.
    """
    config = TemplateMinerConfig()
    config.drain_sim_th = sim_th
    config.drain_depth = depth
    config.profiling_enabled = False
    config.parametrize_numeric_tokens = False
    config.masking_instructions = [
        # node coordinates: R02-M1-N0-C:J12-U11
        MaskingInstruction(r"R\d+-M\d+-N\d+(-[\w:.]+)?", "NODE"),
        MaskingInstruction(r"0x[0-9a-fA-F]+", "HEX"),
        # Bare 8+ char runs of [0-9a-f] -- e.g. "iar 003a90fc dear 00c07208"
        # and "12364800 double-hummer alignment exceptions".
        #
        # These mask to <NUM>, the SAME token short decimals get, and that
        # sameness is the point. Two earlier attempts both split one event
        # into two templates:
        #
        #   masking all 8+ hex runs as <HEX>  ->  "1347195 double-hummer"
        #     became <NUM> while "12364800 double-hummer" became <HEX>,
        #     splitting one event by the magnitude of its counter.
        #   requiring a letter for <HEX>      ->  fixed that, but split
        #     address fields instead, where the value sometimes renders with
        #     letters (003a90fc) and sometimes without (12364800).
        #
        # The underlying problem is a variable whose rendering varies, and the
        # only stable answer is to give every rendering the same mask. The
        # hex/decimal distinction is diagnostic detail, not event identity --
        # the same reasoning used to group Java exception subtypes in the HDFS
        # parser.
        MaskingInstruction(r"\b[0-9a-fA-F]{8,}\b", "NUM"),
        MaskingInstruction(r"java\.[\w.$]+(?:Exception|Error)\b.*", "EXCEPTION"),
        MaskingInstruction(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?", "IP"),
        MaskingInstruction(r"/[\w\-./]+", "PATH"),
        MaskingInstruction(r"\b\d+\b", "NUM"),
    ]
    return TemplateMiner(config=config)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="data/raw/BGL.log")
    ap.add_argument("--out", default="data/bgl_processed")
    ap.add_argument("--window-seconds", type=int, default=1800,
                    help="window size. 1800 = 30 min, matching the literature.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sim-th", type=float, default=0.5,
                    help="Drain3 similarity threshold. RETUNE THIS FOR BGL -- "
                         "do not assume the HDFS value transfers.")
    ap.add_argument("--depth", type=int, default=4)
    args = ap.parse_args()

    if not os.path.exists(args.log):
        print(f"ERROR: {args.log} not found.", file=sys.stderr)
        print("Download BGL from https://zenodo.org/records/8196385 "
              "(BGL.zip) and extract into data/raw/", file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)
    miner = build_miner(args.sim_th, args.depth)
    W = args.window_seconds

    windows = defaultdict(lambda: array("i"))   # window_idx -> [template_id,...]
    win_alerts = defaultdict(lambda: defaultdict(int))  # window -> {category: n}
    win_first_ts = {}
    tmpl_count = defaultdict(int)
    tmpl_example = {}
    # first window in which each template appears -- for the new-event baseline
    tmpl_first_window = {}

    n = unparsed = n_alert = 0
    t0 = time.time()

    print(f"Parsing {args.log}  (window = {W}s)")
    with open(args.log, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n += 1
            if args.limit and n > args.limit:
                n -= 1
                break

            m = LINE_RE.match(line)
            if not m:
                unparsed += 1
                continue
            label, ts_s, _date, _node, _tm, _node2, _typ, _comp, _lvl, content = m.groups()
            ts = int(ts_s)
            w = ts // W

            result = miner.add_log_message(content)
            tid = result["cluster_id"]
            tmpl_count[tid] += 1
            if tid not in tmpl_example:
                tmpl_example[tid] = content.strip()
            if tid not in tmpl_first_window:
                tmpl_first_window[tid] = w

            windows[w].append(tid)
            if w not in win_first_ts:
                win_first_ts[w] = ts
            if label != "-":
                win_alerts[w][label] += 1
                n_alert += 1

            if n % 500_000 == 0:
                el = time.time() - t0
                print(f"  {n:>11,} lines | {n/el:>8,.0f} l/s | "
                      f"{len(tmpl_count):>4} templates | {len(windows):>7,} windows")

    el = time.time() - t0
    print(f"\nParsed {n:,} lines in {el/60:.1f} min")
    print(f"  unparsed (format mismatch): {unparsed:,}")
    print(f"  alert lines:                {n_alert:,} ({100*n_alert/max(n,1):.2f}%)")
    print(f"  distinct templates:         {len(tmpl_count)}")
    print(f"  windows:                    {len(windows):,}")

    if unparsed > n * 0.01:
        print(f"\n  !! {100*unparsed/max(n,1):.1f}% of lines did not match the expected")
        print("     BGL format. Check the file -- a silently skipped tenth of the")
        print("     data is exactly the failure mode that bit the HDFS run.")

    # ---- templates.csv -------------------------------------------------
    clusters = {c.cluster_id: c.get_template() for c in miner.drain.clusters}
    tpath = os.path.join(args.out, "templates.csv")
    with open(tpath, "w", newline="", encoding="utf-8") as f:
        w_ = csv.writer(f)
        w_.writerow(["template_id", "count", "first_window", "template", "example_line"])
        for tid, cnt in sorted(tmpl_count.items(), key=lambda kv: -kv[1]):
            w_.writerow([tid, cnt, tmpl_first_window.get(tid, ""),
                         clusters.get(tid, ""), tmpl_example.get(tid, "")])
    print(f"\nWrote {tpath}")

    # ---- sessions.csv (same schema as HDFS) ----------------------------
    spath = os.path.join(args.out, "sessions.csv")
    lengths, n_anom = [], 0
    cat_total = defaultdict(int)
    with open(spath, "w", newline="", encoding="utf-8") as f:
        w_ = csv.writer(f)
        w_.writerow(["block_id", "label", "first_ts", "length", "sequence",
                     "alert_categories"])
        for wi in sorted(windows):
            seq = windows[wi]
            alerts = win_alerts.get(wi, {})
            lab = "Anomaly" if alerts else "Normal"
            if alerts:
                n_anom += 1
                for c in alerts:
                    cat_total[c] += 1
            lengths.append(len(seq))
            # zero-padded so it sorts lexicographically in make_split.py
            w_.writerow([f"w{wi}", lab, f"{win_first_ts[wi]:012d}", len(seq),
                         " ".join(str(t) for t in seq),
                         ";".join(sorted(alerts)) if alerts else ""])
    print(f"Wrote {spath}")

    lengths.sort()
    def pct(p):
        return lengths[min(int(len(lengths) * p), len(lengths) - 1)] if lengths else 0

    total = len(windows)
    print("\n" + "=" * 66)
    print("SANITY CHECK")
    print("=" * 66)
    print(f"  windows:        {total:,}")
    print(f"  anomalous:      {n_anom:,} ({100*n_anom/max(total,1):.2f}%)")
    print(f"  templates:      {len(tmpl_count)}")
    print(f"  window length:  min {lengths[0] if lengths else 0}  p50 {pct(.5)}  "
          f"p90 {pct(.9)}  p99 {pct(.99)}  max {lengths[-1] if lengths else 0}")
    if cat_total:
        top = sorted(cat_total.items(), key=lambda kv: -kv[1])[:6]
        print("  top alert categories: "
              + ", ".join(f"{c}({n})" for c, n in top))
    print("=" * 66)

    if lengths and pct(.9) > 1000:
        print("\n  NOTE: p90 window length is large. A transformer cannot attend")
        print("  over thousands of events cheaply -- you will need truncation, a")
        print("  smaller --window-seconds, or a pooling strategy. Decide this")
        print("  deliberately in week 6 and write down which you chose.")

    print("\nNEXT:")
    print("  1. python scripts/inspect_templates.py "
          f"--templates {args.out}/templates.csv")
    print("     Retune --sim-th if templates look wrong. Do NOT skip this.")
    print(f"  2. python scripts/make_split.py --sessions {spath} "
          f"--out data/bgl_splits")
    print("  3. python scripts/saturation_check.py --splits data/bgl_splits "
          f"--templates {args.out}/templates.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
