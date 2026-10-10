"""Action Plan page: investigation suggestions and downloads."""

from __future__ import annotations

import streamlit as st

from ..analytics import Report
from ..config import Settings
from ..providers import ProviderError
from ..report import (
    ACTION_SOURCE_LABELS,
    action_plan_markdown,
    corrections_csv,
    current_actions,
    evidence_csv,
    findings_csv,
    generate_action_plan,
    report_json,
)
from ..schemas import CATEGORY_LABELS, Filters
from ..session import MODE_MANUAL, AnalysisState
from .common import make_provider, safe_md


def _generate(state: AnalysisState, report: Report, filters: Filters, settings: Settings) -> None:
    provider = None
    if state.mode != MODE_MANUAL:
        try:
            provider = make_provider(state, settings)
        except ProviderError as err:
            st.session_state["actions_error"] = err.message + " Showing reviewed templates instead."
    with st.spinner("Preparing investigation suggestions…"):
        generate_action_plan(state, report, filters, provider)


def render(state: AnalysisState, report: Report | None, filters: Filters, settings: Settings) -> None:
    if report is None:
        st.info("Load data and click **Analyze** first.")
        return
    err = st.session_state.pop("actions_error", None)
    if err:
        st.warning(err)
    plan = current_actions(state, filters)
    st.caption(
        "Suggestions are investigations to review, not guaranteed savings or proven causes. "
        "All figures come from the verified calculations; the model or template supplies only the wording. "
        "ShopDetective never contacts customers or suppliers, processes refunds or changes inventory."
    )
    if filters.categories or filters.review_statuses:
        st.info(
            "The plan covers the top findings in the current product and date scope. The finding category and "
            "review-status filters narrow only the Findings list and findings export rows, not this plan."
        )
    source_hint = {
        "Live AI": "the configured Gemini model (validated against each finding's evidence)",
        "Sample demonstration": "synthetic reference text shipped with the sample (no model called)",
        "Manual review": "reviewed category templates (no model called)",
    }[state.mode]
    if st.button("Generate investigation suggestions", type="primary", key="gen_actions"):
        _generate(state, report, filters, settings)
        st.rerun()
    st.caption(f"Source in the current mode: {source_hint}.")
    if plan is None:
        if state.actions is not None:
            st.warning(
                f"Earlier suggestions were for report version {state.actions.report_version} or a different "
                "filter scope. They are outdated and hidden. Generate again for the current report."
            )
        else:
            st.info(
                "No suggestions generated for this report version yet. "
                "Downloads use reviewed templates until you generate them."
            )
    else:
        st.markdown(
            f"**Report version {plan.report_version}** · generated {plan.generated_at_utc} UTC · {plan.source_summary}"
        )
        if not plan.items:
            st.write("No product–issue findings in this scope.")
        for i, a in enumerate(plan.items, start=1):
            with st.container(border=True):
                st.markdown(f"#### {i}. {safe_md(a.product_name)} — {CATEGORY_LABELS[a.category]}")
                st.markdown(f"**Verified facts:** {safe_md(a.fact_summary)}")
                st.markdown(f"**Suggested investigation:** {safe_md(a.recommendation)}")
                st.caption(
                    f"Evidence: {', '.join(a.evidence_ids) or 'none'} · Source: {ACTION_SOURCE_LABELS[a.source]}"
                    + (f" · {a.note}" if a.note else "")
                )
        if plan.notes:
            with st.expander(f"Validation notes ({len(plan.notes)})"):
                for n in plan.notes:
                    st.write(n)

    st.subheader("Downloads")
    st.caption(
        f"Built from report version {state.report_version} and the current filters. CSV text fields that begin "
        "with = + - @ are prefixed with an apostrophe so spreadsheets do not run them as formulas."
    )
    v = state.report_version
    c = st.columns(5)
    c[0].download_button(
        "Findings CSV",
        findings_csv(state, report, filters),
        f"shopdetective_findings_v{v}.csv",
        "text/csv",
        key="dl_findings",
    )
    c[1].download_button(
        "Evidence CSV",
        evidence_csv(state, report, filters),
        f"shopdetective_evidence_v{v}.csv",
        "text/csv",
        key="dl_evidence",
    )
    c[2].download_button(
        "Corrections CSV",
        corrections_csv(state, filters),
        f"shopdetective_corrections_v{v}.csv",
        "text/csv",
        key="dl_corrections",
    )
    c[3].download_button(
        "Report JSON",
        report_json(state, report, filters),
        f"shopdetective_report_v{v}.json",
        "application/json",
        key="dl_json",
    )
    c[4].download_button(
        "Action plan (Markdown)",
        action_plan_markdown(state, report, filters),
        f"shopdetective_action_plan_v{v}.md",
        "text/markdown",
        key="dl_md",
    )
