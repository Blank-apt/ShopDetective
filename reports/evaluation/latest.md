# Evaluation results (generated)

- Generated: 2026-10-09T17:37:37Z
- Dataset: `data/evaluation/labelled_feedback.csv` (62 examples)
- Label status: provisional_synthetic 62, human_verified 0
- Synthetic examples only; this is not evidence of performance on real businesses.

**Raw category metrics** score the category each system returned, even when the app would send that prediction to human review. **Accepted-decision metrics** score only what the app would accept automatically; reviewed, quote-rejected and failed rows stay in the denominator as not accepted.

## Keyword Baseline

- Analysed sample size: 62
- Classifier review/abstention flags: 0.339
- No valid prediction: 0
- Evidence-quote validity: 1.000 over 36 complaint predictions

### Raw category metrics (returned category, before review routing)

- Raw category accuracy: 0.613
- Raw macro F1 (categories with support): 0.625

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 10 | 0.800 | 0.667 | 0.727 |
| delivery | 11 | 12 | 0.667 | 0.727 | 0.696 |
| wrong_item | 9 | 7 | 0.714 | 0.556 | 0.625 |
| other | 9 | 7 | 0.857 | 0.667 | 0.750 |
| no_complaint | 14 | 5 | 1.000 | 0.357 | 0.526 |
| unclear | 7 | 21 | 0.286 | 0.857 | 0.429 |

### Automatically accepted decisions

- Auto-accepted: 41/62 (coverage 0.661)
- Routed to human review or failed: 21/62 (rate 0.339; flagged/unclear 21, quote rejected 0, no valid prediction 0)
- Precision of auto-accepted decisions: 0.780 (9 incorrect)
- Correct auto-accepted decisions / all examples: 0.516
- Macro F1 over auto-acceptable categories: 0.665
- Expected `unclear` routed to review: 6/7

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 10 | 0.800 | 0.667 | 0.727 |
| delivery | 11 | 12 | 0.667 | 0.727 | 0.696 |
| wrong_item | 9 | 7 | 0.714 | 0.556 | 0.625 |
| other | 9 | 7 | 0.857 | 0.667 | 0.750 |
| no_complaint | 14 | 5 | 1.000 | 0.357 | 0.526 |

## Live Model

Model: `gemini-3.5-flash-lite`, prompt `classify-v1`, schema `1.0`, provider errors 0

- Analysed sample size: 62
- Classifier review/abstention flags: 0.097
- No valid prediction: 0
- Evidence-quote validity: 1.000 over 41 complaint predictions

### Raw category metrics (returned category, before review routing)

- Raw category accuracy: 0.984
- Raw macro F1 (categories with support): 0.981

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 12 | 1.000 | 1.000 | 1.000 |
| delivery | 11 | 11 | 1.000 | 1.000 | 1.000 |
| wrong_item | 9 | 9 | 1.000 | 1.000 | 1.000 |
| other | 9 | 9 | 1.000 | 1.000 | 1.000 |
| no_complaint | 14 | 15 | 0.933 | 1.000 | 0.966 |
| unclear | 7 | 6 | 1.000 | 0.857 | 0.923 |

### Automatically accepted decisions

- Auto-accepted: 56/62 (coverage 0.903)
- Routed to human review or failed: 6/62 (rate 0.097; flagged/unclear 6, quote rejected 0, no valid prediction 0)
- Precision of auto-accepted decisions: 0.982 (1 incorrect)
- Correct auto-accepted decisions / all examples: 0.887
- Macro F1 over auto-acceptable categories: 0.993
- Expected `unclear` routed to review: 6/7

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 12 | 1.000 | 1.000 | 1.000 |
| delivery | 11 | 11 | 1.000 | 1.000 | 1.000 |
| wrong_item | 9 | 9 | 1.000 | 1.000 | 1.000 |
| other | 9 | 9 | 1.000 | 1.000 | 1.000 |
| no_complaint | 14 | 15 | 0.933 | 1.000 | 0.966 |

