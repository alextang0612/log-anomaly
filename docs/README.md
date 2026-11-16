# Project log — what happened each week

A plain-language index. Each entry says what was done, what was found, and what
was decided. Follow the link for the detailed version with numbers and
methodology.

Planning documents: [`roadmap-revised.md`](roadmap-revised.md) is the current
plan; [`project-handbook.md`](project-handbook.md) is the original one, kept for
reference and because the deviations from it are part of the story.

---

## Weeks 1–2 — Getting the data and building the measuring stick

**[week1-findings.md](week1-findings.md)** · parsing, templates, exploratory analysis

Took 11.2 million raw HDFS log lines and turned them into 575,061 labelled
sequences a model can actually consume. Log lines are nearly all unique because
they contain IDs and addresses, so they were parsed into *templates* — the ~31
underlying print statements that produced them.

**What was found:** the parser's default setting silently merged a failure event
(`PacketResponder ... Interrupted`, 49 occurrences) into a success event
(`... terminating`, 1.7 million occurrences). Every summary statistic was
correct while this was true. Only reading the templates by hand caught it.

**Decided:** tuned the parser threshold deliberately rather than accepting the
default, and grouped Java exception subtypes together so a rare exception class
at test time doesn't become an unknown token.

---

**[week2-findings.md](week2-findings.md)** · split, baselines, and a surprise

Built the evaluation harness and the "dumb" baselines any model must beat.
Split the data chronologically — train on earlier logs, test on later — because
a random split lets a model learn from events that happened *after* the ones it
is tested on.

**What was found:** a 1990s statistical method (TF-IDF + logistic regression)
scored F1 0.9954. That tripped the project's own rule that anything above 0.99
is a bug report, not a triumph. Investigating showed why: across 575,061 blocks
there are only ~17,000 distinct behaviours, and **99.2% of test sequences appear
word-for-word in the training data**. A plain Python dictionary scores 0.9193.

**Decided:** HDFS cannot settle an accuracy argument — every method clusters near
0.99 and there is nothing to distinguish. It was repurposed as a *cost*
comparison, where holding accuracy constant is a feature rather than a problem,
and a second dataset was added to answer the accuracy question properly.

---

## Weeks 3–4 — What does a frontier model actually buy you?

**[week3-findings.md](week3-findings.md)** · giving the model a fair shot

Before measuring a frontier model you have to prompt it properly, or the
comparison is rigged and worthless. This week iterated the prompt on training
data until it was genuinely good.

**What was found:** three separate ways to fool yourself, all caught. An output
limit silently dropped 10% of responses — and specifically the hardest 10%,
since those were the ones the model thought longest about; reported recall was
0.93 when the honest figure was 0.88. Tuning the prompt by reading its mistakes
and then scoring it on those same examples produced a perfect 1.0000 that meant
nothing. And the prompt was being compared against a rules baseline that had been
told *less* than the prompt had — unfair in this project's own favour.

**Decided:** validate every prompt on examples it was never tuned against, move
prompts into version-controlled text files, and level the rules baseline up to
the same information the prompt gets rather than quietly enjoying the advantage.

---

**[week4-findings.md](week4-findings.md)** · the LLM baseline, measured

Ran Claude Sonnet 5.5 and Claude Haiku 5.5 over 5,000 held-out sessions,
recording real token counts rather than estimates. The prompt was developed on
training data only and validated on unseen training sessions before any
validation data was touched.

**What was found, in three numbers:**

| What the model was given | F1 | Cost per million log lines |
|---|---|---|
| A bare instruction | 0.8696 | $75.72 |
| The same plus domain knowledge | 0.9412 | $124.29 |
| **That knowledge written as code instead** | **0.9432** | **~$0** |

The domain knowledge is worth +0.072 F1. The model adds nothing beyond it —
forty lines of conditional logic match it exactly, free, in microseconds instead
of 1.2 seconds. The frontier model places **fourth** in the overall table,
behind both logistic regression and hand-written rules.

Separately, Sonnet and Haiku — an order of magnitude apart in price — produced
**identical predictions on all 5,000 sessions**, including every false alarm and
every miss.

**Decided:** the headline claim is cost and latency at matched accuracy, not
accuracy. 193 hours of serial API time to scan 38 hours of logs from one cluster
is not expensive, it is impossible.

---

## Week 5 — A second dataset, checked before committing to it

**[week5-findings.md](week5-findings.md)** · BGL, and the saturation gate

Added BGL (Blue Gene/L supercomputer logs) to answer the question HDFS could no
longer answer: does model design actually matter? But first checked whether BGL
is *also* too easy — the week-2 lesson applied in advance rather than in
hindsight.

**What was found:** it isn't, provided the data is grouped the right way. Grouped
one way, a trivial detector scores 0.988 and BGL is as useless as HDFS. Grouped
into 30-minute time windows in chronological order, the same trivial detector
scores **0.360**, and pure memorisation catches **one anomaly out of 93** —
every failure is structurally new.

Also: the text-masking rules took three attempts to get right, and the first two
failures were invisible to every summary statistic. Third time in this project.

**Decided:** BGL in 30-minute chronological windows is where the model gets
built and measured. One problem deferred to week 6 — window sizes range from 1
event to 129,607, and a transformer cannot handle that spread without a
deliberate choice about truncation or pooling.

---

## Weeks 6–16 — still to come

| Weeks | Phase |
|---|---|
| 6–9 | Train the model, handle class imbalance, size sweep, ablations |
| 10–12 | Quantisation, profiling, throughput |
| 13–16 | Serve it, deploy it, write it up |

The central open question: **does modelling the order of events beat simply
counting which events occurred?** HDFS says no. BGL is where it gets a real
test.

---

## A recurring theme

Three times now, a silent error has survived every automated check and been
caught only by a human reading the output:

| Week | Error | What the statistics said |
|---|---|---|
| 1 | A failure event merged into a success event | Block counts, label joins and anomaly rate all exactly correct |
| 3 | An output cap silently dropped 10% of model responses — and specifically the hardest 10% | Clean metrics on an unknowingly truncated sample |
| 5 | One event split across two templates by number formatting | 4.7M lines parsed, 0 unparsed, alert rate matching the published figure |

Aggregate statistics cannot detect semantic errors. That is the methodological
spine of this project.
