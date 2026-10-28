#!/usr/bin/env python3
"""
The three baselines your model has to beat. All scored by the same harness on
the same chronological split.

  1. LENGTH ONLY      one integer threshold, fit on train
  2. RULES            template-presence rules, the kind an ops team writes
  3. TF-IDF + LR      bag-of-templates with logistic regression

Read this before you look at the numbers:

TF-IDF + LR IS EXPECTED TO BE STRONG. That is not a disappointment, it is the
point. It is the real bar -- not the frontier model. If your transformer cannot
beat a 1990s technique on this data, you need to know that in week 9, not
week 15.

The LENGTH baseline exists because the week-1 EDA found something the roadmap
did not anticipate: no normal block has fewer than 13 events, while a quarter of
anomalies have 4 or fewer. Length alone is a strong signal here. If you skip
this baseline you will not know how much of any later F1 is just length wearing
a costume.

Threshold / rule selection happens on TRAIN and VAL only. Test is reported once.

Usage:
    python scripts/baselines.py                 # evaluate on val (default)
    python scripts/baselines.py --eval test     # once, at the end
    python scripts/baselines.py --ngram 2       # does ORDER beat PRESENCE?
"""

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluate import report, table  # noqa: E402

csv.field_size_limit(10_000_000)


def load(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    y = [1 if r["label"] == "Anomaly" else 0 for r in rows]
    seqs = [r["sequence"] for r in rows]
    lens = [int(r["length"]) for r in rows]
    return rows, y, seqs, lens


# ---------------------------------------------------------------- baseline 1
def fit_length_rule(y, lens):
    """Pick the length threshold that maximises F1 ON TRAIN. Flags short blocks."""
    best = (0.0, 0)
    for thr in range(1, max(lens) + 1):
        pred = [1 if L < thr else 0 for L in lens]
        tp = sum(1 for t, p in zip(y, pred) if t == 1 and p == 1)
        fp = sum(1 for t, p in zip(y, pred) if t == 0 and p == 1)
        fn = sum(1 for t, p in zip(y, pred) if t == 1 and p == 0)
        if tp == 0:
            continue
        pr, rc = tp / (tp + fp), tp / (tp + fn)
        f1 = 2 * pr * rc / (pr + rc)
        if f1 > best[0]:
            best = (f1, thr)
    return best[1]


# ---------------------------------------------------------------- baseline 2
# Template IDs chosen from the week-1 EDA, not from intuition. Each appears in
# <0.5% of normal blocks. See docs/week1-findings.md section 3.2.
#
# Deliberately EXCLUDED: template 27 ("Got exception while serving"). It looks
# like an error and fires 356,207 times, but appears in 22.6% of normal blocks
# vs 18.9% of anomalous ones. A naive keyword rule on "exception" would include
# it and wreck precision. That contrast is worth reporting.
RULE_TEMPLATES = {
    25,  # Unexpected error trying to delete block -- 30.4% anom / 0.04% norm
    14,  # writeBlock received exception        -- 19.6% anom / 0.00% norm
    26,  # addStoredBlock ... does not belong to any file -- 7.4% / 0.00%
    19,  # Redundant addStoredBlock request     --  5.8% anom / 0.00% norm
    11,  # ask to replicate (replication chain) -- 20.3% anom / 0.32% norm
}


def rules_predict(seqs, templates):
    out = []
    for s in seqs:
        ids = {int(x) for x in s.split()} if s else set()
        out.append(1 if ids & templates else 0)
    return out


def naive_keyword_predict(seqs, tmpl_text):
    """The rule an ops team writes on day one: flag anything with 'exception'
    or 'error' in it. Included to show why evidence beats intuition."""
    bad = {t for t, txt in tmpl_text.items()
           if "exception" in txt.lower() or "error" in txt.lower()}
    return rules_predict(seqs, bad), bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="data/splits")
    ap.add_argument("--templates", default="data/processed/templates.csv")
    ap.add_argument("--eval", default="val", choices=["val", "test"],
                    help="which split to score on. Use test ONCE, at the end.")
    ap.add_argument("--ngram", type=int, default=1,
                    help="1 = bag of templates (presence). 2 = include adjacent "
                         "pairs, a cheap first probe at whether ORDER helps.")
    args = ap.parse_args()

    tr_path = os.path.join(args.splits, "train.csv")
    ev_path = os.path.join(args.splits, f"{args.eval}.csv")
    for p in (tr_path, ev_path):
        if not os.path.exists(p):
            print(f"ERROR: {p} not found -- run make_split.py first.", file=sys.stderr)
            return 1

    tmpl_text = {}
    if os.path.exists(args.templates):
        with open(args.templates, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tmpl_text[int(r["template_id"])] = r["template"]

    _, ytr, str_, ltr = load(tr_path)
    _, yev, sev, lev = load(ev_path)

    print("=" * 78)
    print(f"BASELINES   fit on train ({len(ytr):,})   scored on "
          f"{args.eval} ({len(yev):,})")
    if args.eval == "test":
        print("  *** scoring on TEST -- this should happen once, at the end ***")
    print("=" * 78)

    results = []

    # 1. length only
    thr = fit_length_rule(ytr, ltr)
    results.append(("Length only (len < %d)" % thr,
                    report(f"1. LENGTH ONLY  (flag length < {thr}, fit on train)",
                           yev, [1 if L < thr else 0 for L in lev],
                           notes="no model, no features, one integer")))

    # 2a. the naive version, for contrast
    naive_pred, bad = naive_keyword_predict(sev, tmpl_text)
    if tmpl_text:
        results.append(("Naive keyword (error|exception)",
                        report("2a. NAIVE KEYWORD RULE  (any template containing "
                               "'error'/'exception')",
                               yev, naive_pred,
                               notes=f"{len(bad)} templates matched; this is the "
                                     "intuitive rule, and it includes the false "
                                     "friend (template 27)")))

    # 2b. the evidence-based version
    results.append(("Rules (EDA-derived templates)",
                    report("2b. EVIDENCE-BASED RULES  (templates "
                           f"{sorted(RULE_TEMPLATES)})",
                           yev, rules_predict(sev, RULE_TEMPLATES),
                           notes="same idea, but chosen from measured class rates")))

    # 3. TF-IDF + logistic regression
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        print("\n  SKIPPED TF-IDF + LR: uv pip install scikit-learn", file=sys.stderr)
        table(results)
        return 0

    vec = TfidfVectorizer(analyzer="word", token_pattern=r"\S+",
                          ngram_range=(1, args.ngram))
    Xtr = vec.fit_transform(str_)
    Xev = vec.transform(sev)

    clf = LogisticRegression(max_iter=2000, class_weight="balanced")
    clf.fit(Xtr, ytr)
    score = clf.predict_proba(Xev)[:, 1]

    label = "TF-IDF + LR" + (f" ({args.ngram}-gram)" if args.ngram > 1 else " (bag)")
    results.append((label,
                    report(f"3. {label.upper()}  "
                           f"[{Xtr.shape[1]} features, class_weight=balanced]",
                           yev, clf.predict(Xev), score,
                           notes="this is the real bar")))

    table(results)

    print("  GATE 1 checklist:")
    print("    [x] chronological split, verified non-overlapping")
    print("    [x] harness reports precision / recall / F1 / PR-AUC, never accuracy")
    print("    [x] baselines recorded on an untouched split")
    print()
    print("  Record the best F1 above. That number, not the frontier model's,")
    print("  is what week 9 has to beat.")
    if args.ngram == 1:
        print()
        print("  Next: rerun with --ngram 2. If bigrams barely help, ORDER adds")
        print("  little over PRESENCE on this data -- which sharpens exactly what")
        print("  your transformer has to justify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
