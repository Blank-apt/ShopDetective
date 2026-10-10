# Evaluation

## What is measured

`scripts/evaluate.py` compares a **keyword baseline** with the **live Gemini classifier** on a separate synthetic labelled set: `data/evaluation/labelled_feedback.csv`, 62 examples.

- The set covers sizing, delivery and wrong-item complaints, other complaints, neutral/positive feedback, negation ("Delivery wasn't slow"), paraphrases ("Had to send it back for a bigger one"), mixed/unclear messages and one instruction-like message.
- The examples are **not** used in prompts or in the sample-demo lookup. A test checks that none of them appears in `prompts.py` or the demo feedback.
- Allowed `label_status` values are `provisional_synthetic` and `human_verified`; the script and tests reject anything else. **All 62 rows are currently `provisional_synthetic`**: the coding assistant drafted them and no person has reviewed them. To review: edit the CSV, fix `expected_category`/`expected_detail` where you disagree, add `reviewer_notes`, and change `label_status` to `human_verified` only for rows a person actually checked. Then re-run. Reports show the count for each status.

## Two metric families

They answer different questions, so they are reported separately and must not be swapped.

### Raw category metrics (`raw_category`)

"Is the category the classifier *returned* correct?" Every row is scored on the returned category, **even when the app would send that prediction to human review** (flagged `needs_review`, or a complaint whose quote failed the exact-substring check). A response that failed validation after the bounded retries counts as a miss. Reported: accuracy, macro F1 over categories with support, and per-category precision/recall/F1/support for all six labels.

This measures labelling quality. It is **not** the accuracy of decisions the app accepts automatically.

### Automatically accepted decisions (`accepted_decisions`)

"What does the app accept without a human, and how often is that right?" A prediction is **auto-accepted** only if all of these hold:

1. a valid prediction was returned (no validation failure);
2. it is not flagged `needs_review` and is not `unclear`;
3. for complaint categories, its quote is an exact substring of the message (`quote_valid` is not false).

Everything else is **routed to human review**. Reviewed, quote-rejected and failed rows stay in every denominator. Reported:

- **Auto-acceptance coverage** = auto-accepted / all examples, and the **review-or-failure rate** = 1 − coverage, split into flagged/unclear, quote rejected and no valid prediction.
- **Accepted precision** = correct auto-accepted / auto-accepted (*n/a* if nothing was accepted).
- **Correct auto-accepted / all examples**, the share handled correctly without a human.
- Per-category precision/recall/F1 for the five auto-acceptable labels. Recall uses **every** expected row of that category as the denominator, so reviewed or failed rows count as misses.
- **Expected `unclear` rows.** The app never auto-accepts `unclear`, so the correct automatic outcome for these rows is routing them to review. They count toward coverage and the review rate. An auto-accepted decision on one of them counts as an incorrect automatic decision. `expected_unclear_routed_to_review` reports how many were routed correctly. `unclear` has no auto-accepted per-category row.

Example (a regression test): expected `sizing`, predicted `sizing`, `needs_review=True`, quote invalid. This gives **raw** accuracy and sizing recall of 1.0, but **0** auto-accepted decisions, coverage 0.0, review rate 1.0 and sizing auto-accepted recall 0.0.

### Other figures

- **Classifier review/abstention flags**: share of rows flagged `needs_review` or `unclear` by the classifier/evidence gate.
- **Evidence-quote validity**: share of complaint predictions whose quote is an exact substring. For the keyword baseline this is 1.0 *by construction* (it quotes the matched keyword), so it is not a meaningful comparison for the baseline. Quote validity shows that the text exists in the message; it does **not** show that the category is semantically right.

Review flags are not calibrated confidence, and no confidence percentage is reported.

## How to run

```bash
python scripts/evaluate.py                 # baseline, plus live model if GEMINI_API_KEY is set
python scripts/evaluate.py --baseline-only
python scripts/evaluate.py --rescore reports/evaluation/latest.json   # recompute from stored predictions, no model call
```

Outputs: `reports/evaluation/latest.json` (metrics, metric definitions, label-status counts and **per-example predictions**) and `reports/evaluation/latest.md`. Without a key, the live section is recorded as `not_run` with no metrics.

## Actual results recorded in this build

### Current run: 2026-10-09T17:37:37Z

`GEMINI_MODEL=gemini-3.5-flash-lite`, prompt `classify-v1`, schema `1.0`, 0 provider errors. Labels: 62 `provisional_synthetic`, 0 `human_verified`. The predictions are stored in `reports/evaluation/latest.json`.

| System | n | Raw accuracy | Raw macro F1 | Auto-accepted (coverage) | Routed to review / failed | Accepted precision | Correct auto-accepted / all | Expected `unclear` routed to review |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Keyword baseline | 62 | 0.613 | 0.625 | 41/62 (0.661) | 21/62 | 0.780 | 0.516 | 6/7 |
| Live Gemini 3.5 Flash-Lite | 62 | 0.984 | 0.981 | 56/62 (0.903) | 6/62 (all flagged/unclear; 0 quote rejected, 0 failed) | 0.982 | 0.887 | 6/7 |

The live model's single incorrect automatic decision was E062 (expected `unclear`, auto-accepted as `no_complaint`). The six reviewed rows were all expected-`unclear` examples routed to review as intended. Per-category tables for both families are in `reports/evaluation/latest.md`.

### Historical run: 2026-10-06

`reports/evaluation/historical/2026-10-06_gemini-3.5-flash-lite/` keeps the original report unchanged: `gemini-3.5-flash-lite`, prompt `classify-v1`, 62 `provisional_synthetic` labels, **raw category metrics only** (accuracy 0.984, macro F1 0.981, flag rate 0.097, quote validity 1.000). That run did not store per-example predictions, so accepted-decision metrics **cannot be recomputed** for it and are not reported. Its README explains this.

### How to read these numbers

- 62 short synthetic messages, with labels drafted by the coding assistant and still `provisional_synthetic`. Clear synthetic wording is easier than real customer text.
- This is **not** evidence of accuracy on real store data, and it is not a user study.
- Each live result is one run. LLM output can vary between runs.
- Why Flash-Lite: on 2026-10-06 the default `gemini-3.5-flash` returned HTTP 503 "high demand" for this key, while `gemini-3.5-flash-lite` responded. The default model has not been evaluated.

## Aggregation checks (separate from classifier quality)

The pytest suite verifies business behaviour with fixed labels:

- Exact hoodie fixture totals, denominators and reconciliation.
- The scripted JN-0044 / M0037 correction: jeans sizing INR 4,500 → INR 6,000, report version 1 → 2, stale actions hidden.
- Duplicate messages that do not multiply refunds.
- Partial refunds, zero-refund complaints, conflicting categories and explicit owner resolution.
- Monetary bounds: oversized amounts and totals are rejected with validation errors before int64 storage.
- Reconciliation for 12 product/date filter scopes.

These checks use synthetic reference labels and fake providers. They show the calculations are correct for given labels; **they say nothing about model accuracy**.
