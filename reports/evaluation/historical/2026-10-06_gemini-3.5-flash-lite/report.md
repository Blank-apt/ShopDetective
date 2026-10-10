# Evaluation results (generated)

- Generated: 2026-10-06T18:08:23Z
- Dataset: `data/evaluation/labelled_feedback.csv` (62 examples)
- Label status: provisional_synthetic
- Synthetic examples only; this is not evidence of performance on real businesses.

## Keyword Baseline

- Analysed sample size: 62
- Accuracy: 0.613
- Macro F1 (categories with support): 0.625
- Abstention / review rate: 0.339
- No valid prediction: 0
- Evidence-quote validity: 1.000 over 36 complaint predictions

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 10 | 0.800 | 0.667 | 0.727 |
| delivery | 11 | 12 | 0.667 | 0.727 | 0.696 |
| wrong_item | 9 | 7 | 0.714 | 0.556 | 0.625 |
| other | 9 | 7 | 0.857 | 0.667 | 0.750 |
| no_complaint | 14 | 5 | 1.000 | 0.357 | 0.526 |
| unclear | 7 | 21 | 0.286 | 0.857 | 0.429 |

## Live Model

Model: `gemini-3.5-flash-lite`, prompt `classify-v1`
- Analysed sample size: 62
- Accuracy: 0.984
- Macro F1 (categories with support): 0.981
- Abstention / review rate: 0.097
- No valid prediction: 0
- Evidence-quote validity: 1.000 over 41 complaint predictions

| Category | Support | Predicted | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|
| sizing | 12 | 12 | 1.000 | 1.000 | 1.000 |
| delivery | 11 | 11 | 1.000 | 1.000 | 1.000 |
| wrong_item | 9 | 9 | 1.000 | 1.000 | 1.000 |
| other | 9 | 9 | 1.000 | 1.000 | 1.000 |
| no_complaint | 14 | 15 | 0.933 | 1.000 | 0.966 |
| unclear | 7 | 6 | 1.000 | 0.857 | 0.923 |

