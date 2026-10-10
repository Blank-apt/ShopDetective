# Devpost submission draft

> Fill in the bracketed items. Do not add results, links or quotes that don't exist yet.

**Title:** ShopDetective

**Tagline:** Trace refunds to the real customer complaints behind them, then decide what to investigate first.

**Track:** AI + Business

**Team:** [teammate 1 name], [teammate 2 name]

**Links:** Demo video: [public 2–4 min video URL] · Repository: [public GitHub URL] · Live app: [Streamlit URL, if you deploy one]

## Inspiration

Small online clothing stores export refunds from one system and customer messages from another. "Barely got it over my head", "fits like a kids' size" and "Way too small" are all the same problem, but to a spreadsheet they're unrelated text. Owners end up guessing which issue to fix first.

## Problem

Which recurring product problems are associated with the most recorded refunds, what evidence supports them, and what should I investigate first?

## Target users

Owners and operations leads of small online apparel shops who have CSV exports of item-level sales and refunds plus customer feedback, but no analyst.

## What it does

- Uploads two CSVs (purchase lines and feedback), with column mapping and strict validation.
- AI (Gemini, structured JSON) labels each message: sizing, delivery, wrong item, other, no complaint or unclear, with an exact supporting quote.
- Python checks every quote is a real substring, joins messages to exact purchase lines, keeps one issue per purchase line, and calculates refund associations in integer paise/cents.
- Ranks product–issue findings by associated recorded refunds, shows unassigned refunds separately, and proves reconciliation for every filter.
- Every finding opens to the original rows and quotes. Mixed, conflicting, unclear or unlinked cases go to a review queue, where the owner's corrections are audited, recalculated and versioned.
- Produces an action plan of investigations (measure against the size chart, check dispatch records, audit picking) with evidence IDs, plus CSV/JSON/Markdown exports.

## How we built it

Python 3.12, Streamlit, pandas, Pydantic, the Google GenAI SDK (`generate_content` with a JSON schema), Plotly, pytest and Ruff. The AI layer sits behind a provider interface (live Gemini / synthetic reference / manual), so business calculations never depend on the model. Responses go through an evidence gate (schema, IDs, allowed enums, exact quotes). The calls use bounded batches and retries with jittered backoff, plus a per-session cache keyed by message digest, model, prompt and schema version. Action suggestions are validated against a whitelist of each finding's evidence IDs, and suggestions containing digits or listed causal/savings phrases are rejected. That is a safeguard, not a semantic guarantee. All figures come from Python templates.

## Challenges

- Keeping AI useful without letting it touch the numbers: we separated "what was said" (AI) from "what it is worth" (Python).
- Avoiding double counting when one purchase has several messages, and handling messages that disagree.
- Being honest in no-key mode: sample labels are digest-locked to the shipped synthetic data and labelled as such, so they can't pretend to classify an upload.

## Accomplishments

- An exact, test-verified synthetic fixture (hoodie: INR 9,600 sizing-associated of INR 16,000 refunds; coverage 15/20) and reconciliation checked across many filter scopes.
- An end-to-end correction workflow with audit history, report versioning and stale-action invalidation, covered by Streamlit AppTest.
- On a 62-message synthetic evaluation set (labels still provisional, drafted by our coding assistant), Gemini 3.5 Flash-Lite reached raw category accuracy 0.984 (keyword baseline 0.613). Counting only the decisions the app accepts without a human, it auto-accepted 56 of 62 (55 correct) and routed 6 to review. Every complaint quote was an exact substring. This is synthetic data, not real-store accuracy.

## What we learned

[Team to write in your own words.]

## What's next

- Human-verify the evaluation labels, re-run the evaluation (raw and accepted-decision metrics), and test the default Flash model.
- Pilot with a real store's anonymised exports (none has been done yet).
- Refund-date input for time-based cohorts; delivery-charge refunds as separate lines.

## Architecture in one paragraph

One Streamlit application. Gemini interprets the complaint text. Pydantic and Python check its evidence, pandas and Python join purchase records and compute the amounts, and owner corrections update a versioned report. Suggestions are investigations, and money is reported as associated recorded refunds. State lives in the browser session (no accounts or database by design), so reports are downloadable.

## Synthetic vs. real

All data shown is synthetic. ShopDetective has not been validated with a real business, and refund associations are leads to investigate, not proven causes or predicted savings.

## Built with

python · streamlit · pandas · pydantic · google-genai · gemini · plotly · pytest · ruff
