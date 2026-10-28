# Week 2 — Split, baselines, and why HDFS cannot settle an accuracy argument

**Status:** Gate 1 passed. Headline result is not the F1 score; it is what the F1 score turned out to mean.

---

## 1. The chronological split

70 / 15 / 20 by time, boundaries snapped so no single timestamp spans two sets,
non-overlap asserted in code rather than assumed.

| split | sessions | anomalies | rate | time range |
|---|---|---|---|---|
| train | 402,549 | 13,116 | 3.26% | 081109 203518 → 081111 060046 |
| val | 86,254 | 2,606 | 3.02% | 081111 060047 → 081111 083310 |
| test | 86,258 | 1,116 | **1.29%** | 081111 083311 → 081111 110413 |

**The test split has 2.5× fewer anomalies, proportionally, than train.** A random
split would have produced three tidy 2.93% slices and concealed this entirely.
This is the first concrete payoff of splitting by time.

Consequences, recorded now so they are not rationalised later:

- Test precision will be structurally lower than val precision. Fewer true
  positives against the same false-alarm rate. This is the data being harder,
  not the model being worse.
- The PR-AUC random baseline differs per split: 0.0326 train, 0.0302 val,
  0.0129 test. A PR-AUC must always be read against its split's positive rate.
- **Test gets reported as-is.** No tuning until it looks better.

A second observation: train spans ~33 hours, val and test together span ~5 hours
while holding 30% of all blocks. Throughput rose sharply near the end of the
capture window while the anomaly rate fell. Whether heavy load correlates with
fewer failures, or the failing workload simply finished earlier, cannot be
determined from this data. Recorded as an observation, not a conclusion.

---

## 2. Baselines (scored on val)

| Approach | Precision | Recall | F1 | PR-AUC | False alarms |
|---|---|---|---|---|---|
| Length only (`len < 13`) | 1.0000 | 0.2763 | 0.4330 | — | 0 |
| Naive keyword (`error`/`exception`) | 1.0000 | 0.6688 | 0.8016 | — | 0 |
| Evidence-based rules | 0.9055 | 0.8454 | 0.8744 | — | 230 |
| **Lookup table (memorisation)** | **1.0000** | **0.8507** | **0.9193** | — | **0** |
| **TF-IDF + LR (bag of templates)** | **0.9935** | **0.9973** | **0.9954** | **0.9997** | **17** |

Accuracy is not reported. At a 2.93% positive rate, always predicting "normal"
scores 97.07% while detecting nothing. The harness has no accuracy function.

---

## 3. The actual finding: HDFS is near-saturated

F1 of 0.9954 tripped the project's own tripwire — *F1 above 0.99 is a bug report,
not a triumph*. Investigating it produced the most useful result of the phase.

### 3.1 The data is highly repetitive

```
distinct sequences in train:                    17,458   (from 402,549 blocks)
val sequences seen verbatim in train:   85,580 / 86,254  (99.2%)
```

Across 575,061 blocks there are only ~17k distinct behaviours. Almost every
future block repeats one already observed, with its label attached.

### 3.2 A dictionary scores 0.92

A lookup table — memorise every training sequence with its majority label,
look up each validation sequence — reaches **F1 0.9193 with perfect precision.**
No model, no features, no training.

Perfect precision is the interesting part: a sequence that was anomalous in
training is *always* anomalous in validation. The sequence→label mapping is an
effectively deterministic function, stable across the time boundary.

### 3.3 All remaining error is novelty

```
unseen sequences in val:          674
  of which anomalous:             389
lookup's missed anomalies:        389
```

Exact match. **Lookup makes zero errors on anything it has seen before.** Its
entire failure mode is blocks with a structurally novel sequence.

And novelty is itself a powerful signal: **57.7% of novel sequences are
anomalous, against a 2.93% base rate — a ~20× lift.**

### 3.4 What TF-IDF adds

Memorisation 0.9193 → bag-of-templates 0.9954. That gap is real generalisation:
the model learns *template 14 present ⇒ anomaly* and applies it to sequences it
has never seen, where lookup can only shrug.

So the task decomposes:

| Portion of blocks | Task | Solved by |
|---|---|---|
| 99.2% | exact recall of a known pattern | a dictionary |
| 0.8% | judging unfamiliar behaviour | generalisation over marker templates |

This retroactively justifies DeepLog's design. DeepLog is not a classifier — it
predicts the next event and flags surprise. It is a novelty detector, which is
exactly what the residual 0.8% requires.

It also promotes the week-5 question — *how are unseen templates handled at test
time* — from a footnote to the central design decision. Handling novelty **is**
the task. The rest is a lookup.

### 3.5 Prior work

This is **not** an original finding. Landauer et al., *A Critical Review of Common
Log Data Sets Used for Evaluation of Sequence-Based Anomaly Detection Techniques*
(FSE 2024), analyses duplicate sequences across common log benchmarks, and the
authors publish analysis scripts. It was independently reproduced here, which is
worth something as verification, but the claim in the writeup must be "consistent
with published critiques," not "novel."

---

## 4. What this means for the project

**Accuracy comparisons on HDFS cannot distinguish architectures.** Everything
lands near 0.99; the headroom above TF-IDF is under 0.005, which is noise. The
original Gate 3 — *beat TF-IDF + LR* — is unachievable in any meaningful sense
and has been rewritten.

**A saturated benchmark is a poor accuracy test but an excellent cost test.**
Comparing cost honestly requires accuracy held constant, or the comparison is
always contestable ("cheaper, but it misses more"). HDFS controls that variable
by construction. The cost study becomes a clean single-variable experiment.

**The original thesis is unaffected and in fact strengthened.** The pitch was
never "more accurate" — it was *how much accuracy do I give up, and how much
cheaper and faster is it*, with the moat being cost at volume. If a logistic
regression reaches 0.995, paying a frontier model per log line is indefensible.

**An honest consequence to state plainly:** the neural model will not be the
cheapest row in the HDFS table. Logistic regression over 31 features is a few
CPU multiplications. The cost argument is specifically against the frontier
model, and on HDFS the neural model costs more than TF-IDF while buying nothing.
That is the sentence that makes a second dataset necessary rather than optional.

---

## 5. Open items

| Item | Status |
|---|---|
| Naive keyword rule produced **0 false alarms**, contradicting the EDA — template 27 contains "exception" and appears in 22.6% of normal blocks, so it should have fired constantly. Hypothesis: that event is concentrated in the training time window and barely occurs in val, which would be a further distribution-shift finding and a caution that val does not exercise every failure mode. | **Unresolved — check before writing up** |
| Does BGL exhibit the same saturation? | Must be checked before committing weeks to it. Landauer et al. likely answers this directly. |
| Do the four replication templates (6, 7, 11, 12) merit merging? | Perfectly collinear; deferred to week 9 |

---

## 6. What didn't work

- **Accepting a good number.** F1 0.9954 looked like success. Everything that
  made it meaningless was invisible in the metrics and took three short
  diagnostics to surface.
- **An early claim of novelty.** The lookup baseline was initially described as
  a possible original contribution. A literature check found it documented. The
  lesson is to check before claiming, not after.
- **Assuming val exercises everything.** The naive keyword rule scoring perfect
  precision is almost certainly an artefact of which events happen to occur in
  the validation window — a reminder that one held-out slice is one sample of
  conditions, not all of them.

---

## 7. Reproducing

```bash
python scripts/make_split.py
python scripts/baselines.py            # val
python scripts/baselines.py --ngram 2  # does order beat presence?
```

## References

- Landauer et al., *A Critical Review of Common Log Data Sets Used for Evaluation
  of Sequence-Based Anomaly Detection Techniques*, FSE 2024.
  https://dl.acm.org/doi/10.1145/3660768
- Le & Zhang, *Log-based Anomaly Detection with Deep Learning: How Far Are We?*
  https://arxiv.org/pdf/2202.04301
