"""Shared UI helpers: sidebar, filters, provider factory and safe text."""

from __future__ import annotations

import re
from datetime import date

import streamlit as st

from ..analytics import ITEM_STATUS_LABELS
from ..config import Settings, get_secret
from ..providers import GeminiProvider, ManualReviewProvider, ProviderError, SampleReferenceProvider
from ..schemas import CATEGORY_LABELS, COMPLAINT_CATEGORIES, Filters
from ..session import MODE_LIVE, MODE_MANUAL, MODE_SAMPLE, MODES, AnalysisState

NAV_PAGES = ("Upload & Analyze", "Findings", "Evidence & Review", "Action Plan")
REVIEW_STATUS_OPTIONS = ("model/reference labels", "partly owner reviewed", "all owner reviewed")

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-.!|$~])")


def safe_md(text: str) -> str:
    """Escape untrusted text for st.markdown (no formatting, links, HTML or LaTeX)."""
    return _MD_SPECIAL.sub(r"\\\1", str(text)).replace("\n", "  \n")


def go_to(page: str, **extra) -> None:
    """Button callback: switch the main navigation (and optional keys)."""
    st.session_state["nav"] = page
    for k, v in extra.items():
        st.session_state[k] = v


def gemini_key_present() -> bool:
    return bool(get_secret("GEMINI_API_KEY"))


LIVE_SETUP_HELP = """**Live AI needs a Gemini API key.** It is read on the server only.

1. Create a key in Google AI Studio (https://aistudio.google.com/apikey).
2. Local: copy `.env.example` to `.env` and set `GEMINI_API_KEY=...`, then restart the app.
   Streamlit Cloud: add `GEMINI_API_KEY` under *App settings → Secrets*.
3. Optional: set `GEMINI_MODEL` (default shown in the sidebar).

Meanwhile you can use **Sample demonstration** (shipped sample only) or **Manual review**."""


def make_provider(state: AnalysisState, settings: Settings):
    """Return the provider for the current mode or raise ProviderError."""
    if state.mode == MODE_LIVE:
        key = get_secret("GEMINI_API_KEY")
        if not key:
            raise ProviderError("missing_key", "GEMINI_API_KEY is not configured.", False)
        return GeminiProvider(key, settings.provider.model, settings.provider.timeout_seconds)
    if state.mode == MODE_SAMPLE:
        ds = state.dataset
        if ds is None or not ds.is_synthetic_sample:
            raise ProviderError(
                "not_sample",
                "Sample demonstration labels exist only for the shipped example files. "
                "Choose Live AI or Manual review for your own data.",
                False,
            )
        return SampleReferenceProvider(ds.sample_name, ds.feedback_digest)
    return ManualReviewProvider()


def render_sidebar(state: AnalysisState, settings: Settings) -> Filters:
    sb = st.sidebar
    sb.markdown("### Analysis mode")
    mode = sb.radio(
        "Mode",
        MODES,
        index=MODES.index(state.mode),
        key="mode_choice",
        label_visibility="collapsed",
        help="Live AI calls Gemini. Sample demonstration uses stored synthetic reference labels "
        "for the shipped example only. Manual review uses no model.",
    )
    state.mode = mode
    if mode == MODE_LIVE:
        if gemini_key_present():
            sb.caption(f"Gemini key: configured · model `{settings.provider.model}`")
        else:
            sb.warning("Gemini key not configured. See Upload & Analyze for setup.")
    elif mode == MODE_SAMPLE:
        sb.caption("Synthetic reference labels · no model is called.")
    else:
        sb.caption("You assign every label · no model is called.")
    if state.prediction_mode and state.prediction_mode != mode:
        sb.info(f"Current results came from **{state.prediction_mode}**. Click Analyze to re-run in {mode}.")

    filters = Filters()
    ds = state.dataset
    if ds is not None:
        sb.markdown("### Filters")
        pids = sorted(ds.display_names)
        chosen = sb.multiselect(
            "Products",
            pids,
            default=[],
            format_func=lambda p: f"{ds.display_names[p]} ({p})",
            key="f_products",
            placeholder="All products",
        )
        lo, hi = ds.date_bounds
        picked = sb.date_input(
            "Purchase dates",
            value=(lo, hi),
            min_value=lo,
            max_value=hi,
            key="f_dates",
            help="Selects purchase cohorts by purchase date. Refund dates are not in the input.",
        )
        d_from, d_to = lo, hi
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            d_from, d_to = picked
        elif isinstance(picked, date):
            d_from = d_to = picked
        cats = sb.multiselect(
            "Finding categories",
            [c.value for c in COMPLAINT_CATEGORIES],
            default=[],
            format_func=lambda c: CATEGORY_LABELS[c],
            key="f_categories",
            placeholder="All categories",
        )
        statuses = sb.multiselect(
            "Finding review status",
            REVIEW_STATUS_OPTIONS,
            default=[],
            key="f_review",
            placeholder="Any status",
        )
        sb.caption(
            "Products and purchase dates set the scope for all totals, charts, evidence and actions. "
            "Category and review-status filters only narrow the findings list and the findings rows in exports."
        )
        filters = Filters(
            product_ids=tuple(chosen) or None,
            date_from=d_from if d_from != lo else None,
            date_to=d_to if d_to != hi else None,
            categories=tuple(cats) or None,
            review_statuses=tuple(statuses) or None,
        )

        sb.markdown("### Status")
        label = {"demo": "Example store (synthetic)", "review_cases": "Review-case example (synthetic)"}.get(
            ds.sample_name or "", "Uploaded files"
        )
        sb.caption(f"Data: {label} · {ds.currency}")
        sb.caption(f"{len(ds.orders):,} purchase lines · {len(ds.feedback):,} messages")
        if state.analyzed:
            c = state.status_counts()
            sb.caption(
                f"Report version {state.report_version} · classified {c['classified']} · "
                f"pending {c['pending']} · failed {c['failed']} · not sent {c['not_sent']}"
            )
        else:
            sb.caption("Not analysed yet.")
    with sb.expander("About ShopDetective"):
        st.markdown(
            "ShopDetective links customer complaints to the exact purchase lines and recorded "
            "refunds they belong to. AI classifies the wording; Python checks the quotes, "
            "joins the records and calculates every amount.\n\n"
            "Refund associations are leads to investigate, not proof of cause or savings. "
            "Session data is temporary; download your reports.\n\n"
            f"Item statuses: {', '.join(ITEM_STATUS_LABELS.values())}."
        )
    return filters


__all__ = [
    "LIVE_SETUP_HELP",
    "MODE_LIVE",
    "MODE_MANUAL",
    "MODE_SAMPLE",
    "NAV_PAGES",
    "go_to",
    "make_provider",
    "render_sidebar",
    "safe_md",
]
