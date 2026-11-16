# Week 3 — Prompt development, and three ways to fool yourself

## In plain terms

Week 3 was about giving the frontier model a fair shot before measuring it. A
rigged baseline would destroy the project's credibility, so the prompt was
iterated until it was genuinely good — on training data only, never on the data
it would later be scored against.

Three separate mistakes were made and caught along the way, and all three had
the same shape: **the numbers looked fine while something was wrong.**

---

## 1. Setup

- 100 training sessions, **stratified 50/50** by class.
- Stratified because at a 2.93% natural rate, a random 100 sessions contains
  about three anomalies — not enough failure cases to learn anything from.
- Training split, because looking at training data freely is allowed; iterating
  against validation data would quietly contaminate the split everything is
  later reported on.
- Cost per iteration: about $0.13.

**A metric caveat that matters throughout.** On a stratified sample, precision
is meaningless — it depends on the class ratio, and the ratio was changed
deliberately. Recall is ratio-invariant and stays valid. Only recall was read
during development.

---

## 2. Mistake one — an output cap that dropped the hard cases

The first run set `max_tokens=8`, on the reasoning that the answer is a single
word. 10 of 100 responses came back **completely empty** and were silently
excluded.

Reported recall on the remaining 90: **0.9318**.

The cap was raised to 64. Still 10 empty, identical metrics. Raised to 1024.
All 100 parsed.

Honest recall on all 100: **0.8800**.

**The dropped sessions were not random.** Inspection showed all ten hit the
token ceiling exactly — the model was spending its budget on reasoning before
emitting any text. The sessions it reasoned longest about were the ambiguous
ones: four of the ten were the *shortest* inputs in the batch, which from the
week-1 EDA means truncated lifecycles, the genuinely hard calls.

So the first two runs measured the easy 90% of the sample and reported it as the
whole. Every metric looked clean and plausible throughout.

---

## 3. Mistake two — tuning and scoring on the same sample

With all 100 parsing, six were wrong. Reading them showed a clear pattern: five
of the six contained one of two events the prompt never mentioned.

| Event | In normal blocks | In anomalous blocks | In the prompt? |
|---|---|---|---|
| `Redundant addStoredBlock request received` | 0.00% | 5.8% | no |
| `addStoredBlock ... does not belong to any file` | 0.00% | 7.4% | no |

Both are perfect discriminators from the week-1 EDA. A third signal was visible
too — `addStoredBlock` events occurring *after* the block had been deleted, a
lifecycle-ordering violation the prompt's description did not cover.

The single false positive was also instructive: a normal block of length 13
flagged as anomalous, where the prompt said *"Healthy blocks produce roughly 19
events. Fewer than 13 is unusual"* — vaguer than the data supports. The EDA found
**no** normal block below 13, making `< 13` definitive and `>= 13` uninformative.

The prompt was rewritten with those signals. Re-scored on the same 100 sessions:
**F1 1.0000.**

**That number is meaningless.** The prompt was built by reading the mistakes on
those exact sessions — of course it got them all right. It is the same
circularity a chronological split exists to prevent, applied to prompts instead
of model weights.

**The fix:** a fresh 100 training sessions, different random seed, never seen
during tuning. Score there: **F1 1.0000** again. That one counts — the rules
generalise rather than memorise.

(With 50 positives, a recall estimate carries roughly ±7 points of uncertainty,
so "1.0000" means "consistent with anything above about 0.93." The week-4 run on
5,000 sessions is what pinned it down, at 0.9412.)

---

## 4. Mistake three — a comparison that was unfair in the other direction

The revised prompt now listed marker templates, the length rule, truncation,
re-replication and a lifecycle check. The rules baseline it was being compared
against had **five marker templates and nothing else**.

That comparison measured which side was told more, not which approach was
better. A second rules baseline (`rules_v2`) was built with exactly the signal
set the prompt had:

| | Signals | F1 |
|---|---|---|
| Rules v1 | 5 marker templates | 0.8744 |
| Rules v2 | markers + length + truncation + lifecycle order | 0.9432 |

v1 was kept in the results table, because the gap between them shows what the
extra signals bought — and it is that fair comparison that produced week 4's
central finding.

---

## 5. A process change: prompts became files

Editing a Python source file to tweak a prompt is the wrong setup when prompt
iteration *is* the work. The system prompt was moved to `prompts/system.txt`,
with the runner printing which file it loaded and how long it is — so an edit
that silently failed to take effect can't be mistaken for a prompt that didn't
help.

Prompt versions are kept alongside the results each produced:

| File | Score |
|---|---|
| `prompts/system.txt` | current expert prompt — val F1 0.9412 |
| `prompts/system_naive.txt` | bare instruction, for the ablation — val F1 0.8696 |

That history is the methodology section writing itself.

---

## 6. One judgement call worth flagging

The prompt explicitly tells the model that `Got exception while serving` is a
false friend — it appears in 22.6% of normal blocks against 18.9% of anomalous
ones, despite looking like an error and occurring 356,207 times.

That is domain knowledge handed over, derived from the training data. It is
*fairer* to include it — a human SRE with access to the same logs would know it —
but it is a choice, and it is the reason the week-4 naive-prompt ablation exists:
to measure how much of the model's performance comes from knowledge supplied
rather than reasoning performed.

---

## 7. What didn't work

- **A tight output cap** silently removed 10% of responses, and specifically the
  hardest 10%. Recall read 0.9318 when it was 0.8800.
- **Tuning a prompt and scoring it on the same sample** produced a perfect
  1.0000 that meant nothing. Caught before it reached a results table, but only
  just.
- **An unfair comparison, in the project's own favour.** The prompt was given
  more information than the baseline it was measured against. Fixed by levelling
  up the baseline rather than weakening the prompt.

All three share a shape: the output looked reasonable while the measurement was
wrong.

---

## Continues in

**[week4-findings.md](week4-findings.md)** — the measurement run on 5,000
validation sessions, the naive-prompt ablation, and the finding that the
frontier model's performance is the prompt rather than the model.
