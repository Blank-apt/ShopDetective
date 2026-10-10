"""Findings page: verified totals, ranked product–issue findings and charts."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ..analytics import ITEM_STATUS_LABELS, Report, format_ratio, product_labels
from ..data_loader import format_minor
from ..schemas import CATEGORY_LABELS
from ..session import AnalysisState
from .common import go_to

# Restrained, colour-blind-friendly categorical palette; grey = unassigned.
ISSUE_COLORS = {
    "sizing": "#2F6690",
    "delivery": "#D17A22",
    "wrong_item": "#7A5195",
    "other": "#3A7D44",
    "unassigned": "#B8BCC2",
}


def _money(minor: int, cur: str) -> str:
    return format_minor(minor, cur)


def _metrics(report: Report) -> None:
    cur = report.currency
    a = st.columns(4)
    a[0].metric(
        "Recorded sales", _money(report.sales_minor, cur), help="Sum of sale_amount over purchase lines in scope."
    )
    a[1].metric(
        "Recorded refunds", _money(report.refund_minor, cur), help="Sum of refund_amount over purchase lines in scope."
    )
    a[2].metric(
        "Refunded purchase-line rate",
        format_ratio(report.refunded_lines, report.scope_lines),
        help="Purchase lines with a refund / all purchase lines in scope. Not returned units or customers.",
    )
    a[3].metric(
        "Feedback coverage",
        format_ratio(report.covered_refunded_lines, report.refunded_lines),
        help="Refunded purchase lines with at least one linked message / all refunded purchase lines.",
    )
    b = st.columns(4)
    b[0].metric("Refunds with an accepted issue", _money(report.attributed_refund_minor, cur))
    b[1].metric(
        "Unassigned refunds",
        _money(report.unassigned_refund_minor, cur),
        help="Refunds on lines with no feedback, no complaint, or an unresolved label.",
    )
    b[2].metric(
        "Reconciliation",
        "✓ balanced" if report.reconciles else "✗ mismatch",
        help="Refunds with an accepted issue + unassigned refunds = recorded refunds.",
    )
    b[3].metric(
        "Review queue (lines)", report.review_counts.get("needs_review", 0) + report.review_counts.get("pending", 0)
    )


def _labels(state: AnalysisState) -> dict[str, str]:
    return product_labels(state.dataset.display_names)


def _findings_table(report: Report, labels: dict[str, str]) -> pd.DataFrame:
    cur = report.currency
    return pd.DataFrame(
        [
            {
                "Rank": f.rank,
                "Product": labels.get(f.product_id, f.product_name),
                "Issue": CATEGORY_LABELS[f.category],
                f"Associated refunds ({cur})": f.associated_refund_minor / 100,
                "Refunded lines": f.refunded_lines,
                "Lines with issue": f.affected_lines,
                "Supporting messages": f.supporting_messages,
                "Share of feedback-covered refunded lines": format_ratio(
                    f.refunded_lines, f.covered_refunded_lines_product
                ),
                "Review status": f.review_status,
                "finding_id": f.finding_id,
            }
            for f in report.findings
        ]
    )


def _refund_chart(report: Report, labels: dict[str, str]) -> go.Figure:
    products = [p for p in report.products if p.refund_minor > 0] or report.products
    names = [labels.get(p.product_id, p.product_name) for p in products]  # IDs added when names collide
    fig = go.Figure()
    for cat in ("sizing", "delivery", "wrong_item", "other"):
        vals = [p.issue_refund_minor.get(cat, 0) / 100 for p in products]
        if any(vals):
            fig.add_bar(name=CATEGORY_LABELS[cat], x=names, y=vals, marker_color=ISSUE_COLORS[cat])
    fig.add_bar(
        name="Unassigned",
        x=names,
        y=[p.unassigned_refund_minor / 100 for p in products],
        marker_color=ISSUE_COLORS["unassigned"],
    )
    fig.update_layout(
        barmode="stack",
        title="Recorded refunds by product, split by accepted issue",
        yaxis_title=f"Recorded refunds ({report.currency})",
        xaxis_title=None,
        legend_title_text="Accepted issue",
        height=380,
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def _issue_chart(report: Report) -> go.Figure:
    counts = {}
    for f in report.all_findings:
        counts[f.category] = counts.get(f.category, 0) + f.refunded_lines
    unresolved = sum(v["lines"] for v in report.unassigned_breakdown.values())
    labels = [CATEGORY_LABELS[c] for c in counts] + ["No accepted issue"]
    values = list(counts.values()) + [unresolved]
    colors = [ISSUE_COLORS[c] for c in counts] + [ISSUE_COLORS["unassigned"]]
    fig = go.Figure(
        go.Bar(x=values, y=labels, orientation="h", marker_color=colors, text=values, textposition="outside")
    )
    fig.update_layout(
        title=f"Refunded purchase lines by accepted issue (of {report.refunded_lines})",
        xaxis_title="Refunded purchase lines",
        yaxis=dict(autorange="reversed"),
        height=380,
        margin=dict(l=10, r=30, t=50, b=10),
    )
    return fig


def render(state: AnalysisState, report: Report | None) -> None:
    if report is None:
        st.info("Load data and click **Analyze** on the Upload & Analyze page to see findings.")
        st.button("Go to Upload & Analyze", on_click=go_to, args=("Upload & Analyze",), key="f_to_upload")
        return
    cur = report.currency
    if state.dataset.is_synthetic_sample:
        st.caption("Synthetic example data.")
    st.caption(
        f"Report version {state.report_version}. The date filter selects purchase cohorts by purchase date; "
        "refund dates are not part of the input. Amounts are recorded transaction values; an issue association "
        "is a lead to investigate, not proof of cause."
    )
    if report.scope_lines == 0:
        st.warning("No purchase lines match the current filters.")
        return
    _metrics(report)
    labels = _labels(state)

    st.subheader("Ranked product–issue findings")
    st.caption(
        "Ranked by associated recorded refunds, then distinct refunded purchase lines. "
        "Each refunded purchase line is counted once, however many messages it has."
    )
    hidden = len(report.all_findings) - len(report.findings)
    if hidden:
        st.info(
            f"The finding category / review-status filters hide {hidden} of {len(report.all_findings)} findings "
            "from this list and the Findings CSV. Totals, charts, evidence and the action plan still cover "
            "every finding in the product and date scope."
        )
    if not report.findings:
        st.info("No findings with an accepted complaint issue in this scope yet. Check the review queue.")
    else:
        table = _findings_table(report, labels)
        event = st.dataframe(
            table.drop(columns=["finding_id"]),
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="findings_table",
            column_config={f"Associated refunds ({cur})": st.column_config.NumberColumn(format="%.2f")},
        )
        rows = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
        ids = list(table["finding_id"])
        names = {
            f.finding_id: f"#{f.rank} {labels.get(f.product_id, f.product_name)} — {CATEGORY_LABELS[f.category]}"
            for f in report.findings
        }
        c1, c2 = st.columns([3, 1])
        if rows and rows[0] < len(ids):
            pick = ids[rows[0]]
            c1.markdown(f"Selected finding: **{names[pick]}**")
        else:
            pick = c1.selectbox(
                "Open the evidence behind a finding (or select a table row)",
                ids,
                format_func=names.get,
                key="finding_pick",
            )
        c2.button(
            "View evidence →",
            type="primary",
            on_click=go_to,
            args=("Evidence & Review",),
            kwargs={"evidence_finding": pick},
            key="view_evidence",
        )

    left, right = st.columns(2)
    left.plotly_chart(_refund_chart(report, labels), width="stretch", key="chart_refunds")
    right.plotly_chart(_issue_chart(report), width="stretch", key="chart_issues")

    st.subheader("Refunds without an accepted issue")
    st.caption("Shown separately so issue totals are never presented as a complete explanation of refunds.")
    rows = [
        {
            "Reason": ITEM_STATUS_LABELS.get(status, status),
            "Refunded lines": v["lines"],
            f"Recorded refunds ({cur})": v["refund_minor"] / 100,
        }
        for status, v in sorted(report.unassigned_breakdown.items())
    ]
    if rows:
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            width="stretch",
            column_config={f"Recorded refunds ({cur})": st.column_config.NumberColumn(format="%.2f")},
        )
    else:
        st.write("Every refunded line in scope has an accepted issue.")

    with st.expander("Per-product totals"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Product": p.product_name,
                        "product_id": p.product_id,
                        "Purchase lines": p.lines,
                        f"Sales ({cur})": p.sales_minor / 100,
                        f"Refunds ({cur})": p.refund_minor / 100,
                        "Refunded-line rate": format_ratio(p.refunded_lines, p.lines),
                        "Feedback coverage": format_ratio(p.covered_refunded_lines, p.refunded_lines),
                        f"Unassigned ({cur})": p.unassigned_refund_minor / 100,
                    }
                    for p in report.products
                ]
            ),
            hide_index=True,
            width="stretch",
        )
