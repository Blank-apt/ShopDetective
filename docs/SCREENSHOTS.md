# Screenshots

## Included (actual captures of the built app)

Captured on 2026-10-09 from the app running locally (`streamlit run app.py --server.headless true`, no API key, Sample demonstration mode), using headless Google Chrome driven by Playwright at 1440×900. They are real screenshots, not mockups. The walkthrough raised no Streamlit exceptions or page errors. None of them shows a live Gemini run.

| File | Shows |
|---|---|
| `assets/screenshots/01_start.png` | Start page with **Load example store** and the upload area |
| `assets/screenshots/02_upload_analyze.png` | Example store loaded and analysed, with status counts |
| `assets/screenshots/03_findings.png` | Metrics with denominators, reconciliation, ranked findings and charts |
| `assets/screenshots/04_evidence_review.png` | Evidence for *Classic Hoodie — Sizing*, purchase line HD-0003: evidence card with original message, exact quote check, label source and review status |
| `assets/screenshots/05_action_plan.png` | Action plan for report version 2, with verified facts and *synthetic reference* source labels |
| `assets/screenshots/06_review_queue.png` | Review-case example: the review queue with evidence cards |
| `assets/screenshots/07_correction_applied.png` | Success message after the JN-0044 / M0037 correction: report version 2 |
| `assets/screenshots/08_findings_after_correction.png` | Findings after the correction: jeans sizing INR 6,000.00, issue-associated INR 26,175.00, unassigned INR 10,300.00, total INR 36,475.00, balanced |

## Capturing your own (for Devpost)

1. Run the app and use a browser window of about 1440×900.
2. Follow `docs/DEMO_SCRIPT.md` and capture: findings with chart, an evidence card, the review queue, the success message after a correction (report version 2), audit history and the action plan.
3. If you show Live AI, make sure the sidebar shows **Live AI** and the evidence card shows *Label source: Live Gemini*. No key, `.env` file or terminal containing secrets may be visible.
4. Keep the "synthetic" notices visible. Don't crop out the source labels.
