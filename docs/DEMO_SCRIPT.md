# Demo / video script (about 3 minutes 30 seconds)

The exact amounts below come from **Sample demonstration** on the shipped synthetic data, and are checked by the test suite and a browser walkthrough. A Live AI clip is recorded separately, because a mode toggle alone does not show that a model ran.

## Prepare the recording

1. Launch from the project root. Windows: `.venv\Scripts\python.exe -m streamlit run app.py`. Linux/macOS: `.venv/bin/python -m streamlit run app.py`.
2. Use a browser window of 1440 × 900 or larger, with the sidebar visible. Close unrelated tabs and windows. Never show `.env`, secrets or a terminal containing a key.
3. Click **Load example store**, keep **Sample demonstration**, click **Analyze** and check the hoodie figures. Click **Load example store** again before recording to remove any earlier corrections.
4. **Live AI clip (separate).** Configure a Gemini key locally (`.env`, see `docs/SETUP_WINDOWS.md`). Prior verified live runs used `gemini-3.5-flash-lite` (set `GEMINI_MODEL=gemini-3.5-flash-lite`); use a model that actually works with your key. The Gemini key is separate from any Claude subscription.
5. **Load example store** resets the mode to Sample demonstration. For the live clip, click **Load example store** first, then choose **Live AI** in the sidebar, then click **Analyze**. Keep the actual results and their live provenance on screen: the *Classified N message(s)* message, and **Label source: Live Gemini** on an evidence card in Evidence & Review.
6. You may trim waiting time in editing, but keep the click and the result visible. Do not paste sample labels into a live screen, and do not quote the fixed sample amounts for live output that differs.

## Screen actions and narration

| Time | Show / do | Say |
|---|---|---|
| 0:00–0:18 | App title and Upload & Analyze | "Small online stores have refund records in one file and customer complaints in another. Owners can see the refund amount, but deciding which recurring product problem to investigate still means reading messages manually. ShopDetective connects that evidence." |
| 0:18–0:38 | **Live AI clip**: Analyze, then an evidence card showing *Label source: Live Gemini* | "In Live AI mode, Gemini reads the feedback and returns a complaint category, a detail, and a supporting quote. Our code validates the response and checks that the quote actually exists in the original message. Cases that need review stay visible." |
| 0:38–0:55 | Back to a fresh Sample demonstration: **Load example store**, then **Analyze** | "For a reproducible walkthrough, I'm now using our synthetic example: three hundred purchase lines, five products, and seventy-nine messages. This mode uses stored reference labels and clearly says that no model is called." |
| 0:55–1:15 | **See findings →**. Point at the top row, Classic Hoodie / Sizing | "The highest-priority finding is Classic Hoodie sizing. Twelve refunded purchases have an accepted sizing issue, associated with nine thousand six hundred rupees of recorded refunds. Repeated messages about the same purchase don't multiply the money." |
| 1:15–1:32 | Sidebar **Products** → *Classic Hoodie (P001)*. Point at Recorded refunds, Feedback coverage and Unassigned refunds | "The hoodie has sixteen thousand rupees in total refunds. Fifteen of twenty refunded purchases have feedback, and four thousand rupees remain unassigned. We show that gap instead of pretending every refund has an explanation." |
| 1:32–1:52 | **View evidence →** for the hoodie sizing finding. In **Purchase line in this finding**, choose `HD-0003`. Point at the evidence card | "Every finding can be traced back to the original purchase row and customer message. Here is the original message, the exact supporting quote with its check, the label source and the review status. Gemini interprets the wording; Python joins the records and calculates the money." |
| 1:52–2:20 | Clear the product filter. **Review and correct** → **Purchase line** `JN-0044`. Choose *Sizing / fit* and `too_small`; supporting message `M0037`; quote and reason as below; tick **I confirm this correction**; **Apply correction** | "This jeans message mentions both a fit problem and late delivery, so it waits for an owner decision. For this demonstration, I choose fit as the primary issue, select the supporting message, and record my reason. The original prediction remains in the audit history." |
| 2:20–2:40 | **Findings**: point at Slim Fit Jeans / Sizing, *Report version 2* and Reconciliation | "The jeans sizing finding changes from four thousand five hundred to six thousand rupees. The report becomes version two, and unassigned refunds decrease by the same amount. The total recorded refunds stay unchanged. Any earlier action plan has to be regenerated." |
| 2:40–3:00 | **Action Plan** → **Generate investigation suggestions**. Show the hoodie item and its source label | "The action plan suggests concrete investigations, such as measuring stock against the published size chart or checking dispatch records. Each suggestion is connected to evidence. These are investigation leads, not proof that an issue caused a refund or a promise of savings." |
| 3:00–3:15 | Downloads: Findings CSV, Evidence CSV, Report JSON, Action plan (Markdown) | "The owner can download the findings, supporting evidence, correction history, and action plan. Reports carry their version and scope so the numbers can be checked later." |
| 3:15–3:30 | Findings overview | "We built this with Streamlit, Python, pandas, Pydantic, and Gemini. ShopDetective combines complaint understanding, checked calculations, and human review to help a store decide what to investigate first." |

If Live AI is unavailable, leave out that clip and say explicitly that the recording shows synthetic reference mode. Do not imply that the radio button proves the model ran.

## Exact correction to rehearse

Clear the product filter first; this is a jeans purchase, not a hoodie.

| Control | Value |
|---|---|
| Purchase line | `JN-0044`: listed as *Mentions both a fit problem and late delivery* |
| Original message | `M0037`: `Too tight at the waist and they also arrived a week late.` |
| Accepted primary issue | Sizing / fit (`sizing`) |
| Detail | `too_small` |
| Supporting messages | `M0037` (pre-selected, since it is the only message) |
| Exact supporting quote from M0037 | `Too tight at the waist` |
| Reason for this decision | `For this synthetic demonstration, choose fit as the primary issue.` |
| Confirmation | Tick **I confirm this correction**, then click **Apply correction** |

Choosing the primary issue here demonstrates an owner decision. Do not claim that a real customer was contacted or that an extra email arrived.

Expected before → after (verified by `tests/test_review_and_report.py::test_scripted_demo_correction_jn0044_exact_numbers` and a browser walkthrough): jeans sizing INR 4,500 → INR 6,000; store issue-associated refunds INR 24,675 → INR 26,175; store unassigned refunds INR 11,800 → INR 10,300. Total recorded refunds stay at INR 36,475. Report version 1 → 2.

## Verified sample numbers (before any correction)

| Scope | Value |
|---|---|
| Example store | 300 purchase lines, five products, 79 messages |
| Store recorded refunds | INR 36,475.00 on 43 refunded purchase lines; coverage 33/43 |
| Store issue-associated / unassigned | INR 24,675.00 / INR 11,800.00 |
| Classic Hoodie refunds | INR 16,000.00; refunded-line rate 20/100; coverage 15/20 |
| Hoodie sizing | INR 9,600.00 on 12 refunded lines (12/15 = 80%); 15 lines with the issue, 18 supporting messages |
| Hoodie delivery / unassigned | INR 2,400.00 on 3 lines / INR 4,000.00 |

## Technical explanation for questions

"This is a single Streamlit application. We validate two CSVs and keep purchase IDs exact. Gemini classifies the feedback using a structured schema. Pydantic and our evidence checks validate the result, then we resolve one primary issue per purchased line. Python computes refunds and rankings in minor currency units. The owner can override an assignment with an audit record, which updates the report version and invalidates old suggestions. Data is held in the current session; exports preserve the report for later use."

Why AI helps: "Customers describe the same issue differently, including paraphrases and negation. The model interprets that language. It never supplies the financial totals."

About the evaluation: "The included evaluation uses sixty-two synthetic examples with provisional labels. It is an early test, not evidence of real-store accuracy. We separate raw category quality from whether a decision can be accepted automatically. In our latest run, the model's raw category accuracy was 0.98. The app accepted 56 of 62 decisions automatically, 55 of them correctly, and sent the other six to review." (Figures: `docs/EVALUATION.md`, run 2026-10-09.)

## Claims to keep accurate

- Say "associated recorded refunds", not lost profit or money guaranteed to be saved.
- The fixed INR 9,600 hoodie figure belongs to Sample demonstration. Live output may differ (one earlier live run put a hoodie sizing line into review: INR 8,800 on 11 lines).
- Quote existence is checked; that alone does not prove the category is semantically correct.
- Suggestions are screened for unseen evidence IDs, digits and a list of causal/savings phrases. That is a safeguard, not a complete semantic guarantee.
- All bundled business data is synthetic. No real-store pilot or user study has been done.
- State is held in session memory, not a persistent database. A restart can lose it, so download reports.
- An owner correction updates an audited assignment; it does not train the model.
- The category and review-status filters narrow only the findings list and findings export rows. Totals, charts, evidence and the action plan follow the product and date scope.
- Fill in real team names and public repository/video links before submitting.
