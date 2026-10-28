#!/usr/bin/env python3
"""
The evaluation harness. Every number in this project comes from here, so that
all four approaches are measured identically.

WHY THERE IS NO ACCURACY METRIC IN THIS FILE
--------------------------------------------
2.93% of blocks are anomalous. A model that predicts "normal" for every single
input scores 97.07% accuracy while detecting nothing at all. Accuracy on
imbalanced data is not a weak metric, it is an actively misleading one.

It is left out deliberately. If you ever want it, you have to add it yourself
and explain why -- that friction is the point.

WHAT IS REPORTED
----------------
precision  of the blocks you flagged, what fraction were really anomalous
           (low precision = burning on-call attention on false alarms)
recall     of the real anomalies, what fraction you caught
           (low recall = missed outages)
F1         harmonic mean of the two, on the ANOMALY class only
PR-AUC     threshold-free quality. The baseline is the positive rate (0.0293),
           NOT 0.5 -- a random classifier scores 0.0293 here, so 0.4 is real.

TP/FP/FN are shown as raw counts because "precision 0.85" hides whether that
means 17 false alarms or 17,000.

Usage as a module:
    from evaluate import evaluate, report
    report("TF-IDF + LR", y_true, y_pred, y_score)
"""

import sys


def evaluate(y_true, y_pred, y_score=None):
    """y_true / y_pred: iterables of 0/1. y_score: optional float confidences.

    Returns a dict. Deliberately contains no accuracy key.
    """
    y_true = list(y_true)
    y_pred = list(y_pred)
    if len(y_true) != len(y_pred):
        raise ValueError(f"length mismatch: {len(y_true)} vs {len(y_pred)}")

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    out = {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "n": len(y_true),
        "positives": tp + fn,
        "positive_rate": (tp + fn) / len(y_true) if y_true else 0.0,
        "pr_auc": None,
    }

    if y_score is not None:
        try:
            from sklearn.metrics import average_precision_score
            out["pr_auc"] = float(average_precision_score(y_true, list(y_score)))
        except ImportError:
            print("  (PR-AUC skipped: pip install scikit-learn)", file=sys.stderr)
    return out


def report(name, y_true, y_pred, y_score=None, notes=None):
    """Print one labeled result block and return the metrics dict."""
    m = evaluate(y_true, y_pred, y_score)
    print()
    print(f"  {name}")
    print("  " + "-" * 62)
    print(f"    precision {m['precision']:.4f}   recall {m['recall']:.4f}   "
          f"F1 {m['f1']:.4f}")
    if m["pr_auc"] is not None:
        print(f"    PR-AUC    {m['pr_auc']:.4f}   (random baseline = "
              f"{m['positive_rate']:.4f})")
    else:
        print("    PR-AUC    n/a  (rule-based: binary output, no confidence score)")
    print(f"    caught {m['tp']:,} of {m['positives']:,} anomalies, "
          f"{m['fp']:,} false alarms, {m['fn']:,} missed")
    if notes:
        print(f"    note: {notes}")
    return m


def table(results):
    """results: list of (name, metrics dict). Prints the comparison table."""
    print()
    print("=" * 78)
    print("COMPARISON -- all rows measured on the same held-out split")
    print("=" * 78)
    print(f"  {'approach':<34} {'prec':>7} {'recall':>7} {'F1':>7} {'PR-AUC':>8} {'FP':>9}")
    print("  " + "-" * 74)
    for name, m in results:
        pa = f"{m['pr_auc']:.4f}" if m["pr_auc"] is not None else "n/a"
        print(f"  {name:<34} {m['precision']:>7.4f} {m['recall']:>7.4f} "
              f"{m['f1']:>7.4f} {pa:>8} {m['fp']:>9,}")
    print("=" * 78)
    if results:
        best = max(results, key=lambda r: r[1]["f1"])
        print(f"  Best F1: {best[0]} ({best[1]['f1']:.4f})")
        print()
        print("  This is the bar your neural network has to clear in week 9.")
        print("  If it does not, that is a publishable finding -- report it honestly")
        print("  rather than grinding. An honest negative result reads as competence.")
    print()


if __name__ == "__main__":
    print(__doc__)
    print("This module is imported by baselines.py; it is not run directly.")
