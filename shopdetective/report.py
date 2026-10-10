"""Fact bundles, validated action suggestions and exports.

The model (or the synthetic reference) supplies only qualitative
investigation text. Every number shown or exported is rendered from Python
facts with deterministic templates.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .analytics import Finding, Report, format_ratio
from .config import (
    ACTION_PROMPT_VERSION,
    APP_VERSION,
    PROMPT_VERSION,
    REPORT_FORMAT_VERSION,
    SCHEMA_VERSION,
)
from .data_loader import format_minor, minor_to_decimal_str
from .providers import Provider, ProviderError
from .schemas import CATEGORY_LABELS, FILTER_SEMANTICS, SOURCE_LABELS, ActionPlanResponse, ActionType, Filters
from .session import ActionPlan, AnalysisState
from .validation import utc_now

MAX_BUNDLE_QUOTES = 8
MAX_ACTION_FINDINGS = 8

ACTION_SOURCE_LIVE = "live_gemini"
ACTION_SOURCE_SYNTHETIC = "synthetic_reference"
ACTION_SOURCE_TEMPLATE = "deterministic_template"
ACTION_SOURCE_LABELS = {
    ACTION_SOURCE_LIVE: "Live Gemini suggestion (validated)",
    ACTION_SOURCE_SYNTHETIC: "Synthetic reference text (sample demo, no model called)",
    ACTION_SOURCE_TEMPLATE: "Reviewed category template",
}

# Reviewed category-specific investigation templates (fallbacks).
TEMPLATES: dict[str, tuple[ActionType, str]] = {
    "sizing": (
        ActionType.MEASURE_SIZE_CHART,
        "Measure several in-stock units of each size against the published size chart, and check "
        "whether the product page explains the fit clearly.",
    ),
    "delivery": (
        ActionType.CHECK_DISPATCH_RECORDS,
        "Check dispatch dates and courier tracking for the cited purchases to see where delays, "
        "losses or transit damage occurred.",
    ),
    "wrong_item": (
        ActionType.AUDIT_PICKING_PACKING,
        "Review picking and packing records for the cited purchases and confirm that SKU labels, "
        "bin locations and listing colour names match.",
    ),
    "other": (
        ActionType.INSPECT_QUALITY,
        "Inspect current stock and the supplier batch for the quality problems customers describe.",
    ),
}

# Reject prose that states causation or promises outcomes.
_CAUSAL = re.compile(
    r"\b(caused|causes|causing|proves?|guarantee[sd]?|will (save|reduce|eliminate|increase)|"
    r"definitely|certainly)\b",
    re.IGNORECASE,
)


@dataclass
class ActionItem:
    finding_id: str
    product_id: str
    product_name: str
    category: str
    action_type: str
    recommendation: str
    evidence_ids: list[str]
    source: str
    fact_summary: str
    note: str | None = None


def fact_summary(f: Finding, currency: str) -> str:
    covered = f.covered_refunded_lines_product
    share = format_ratio(f.refunded_lines, covered)
    text = (
        f"{f.refunded_lines} refunded purchase line(s) of {f.product_name} have an accepted "
        f"{CATEGORY_LABELS[f.category].lower()} issue, associated with "
        f"{format_minor(f.associated_refund_minor, currency)} of recorded refunds "
        f"(product total {format_minor(f.product_refund_minor, currency)}). "
        f"Share of feedback-covered refunded lines: {share}. "
        f"Supporting messages: {f.supporting_messages}."
    )
    if f.zero_refund_lines:
        text += f" A further {f.zero_refund_lines} non-refunded purchase line(s) report the same issue."
    return text


def scope_key(filters: Filters) -> str:
    """Actions depend on the financial scope only; display-only list filters do not invalidate them."""
    return json.dumps(filters.financial_scope(), sort_keys=True)


def build_fact_bundles(state: AnalysisState, report: Report, limit: int = MAX_ACTION_FINDINGS) -> list[dict]:
    """Verified facts per finding; quotes are only validated excerpts."""
    bundles = []
    for f in report.all_findings[:limit]:
        quotes = []
        for olid in f.order_line_ids:
            item = report.items[olid]
            corr = state.item_overrides().get(olid)
            for mid in item.supporting_message_ids:
                q = None
                if corr is not None:
                    q = corr.supporting_quotes.get(mid)
                else:
                    p = state.predictions.get(mid)
                    if p is not None and p.quote_valid:
                        q = p.evidence_quote
                if q and len(quotes) < MAX_BUNDLE_QUOTES:
                    quotes.append({"message_id": mid, "quote": q[:300]})
        bundles.append(
            {
                "finding_id": f.finding_id,
                "product_id": f.product_id,
                "product_name": f.product_name,
                "issue": f.category,
                "issue_details": f.detail_counts,
                "refunded_purchase_lines": f.refunded_lines,
                "purchase_lines_with_issue": f.affected_lines,
                "supporting_messages": f.supporting_messages,
                "allowed_evidence_ids": f.message_ids,
                "quotes": quotes,
                "allowed_action_types": [a.value for a in ActionType],
            }
        )
    return bundles


def template_action(f: Finding, currency: str, note: str | None = None) -> ActionItem:
    action_type, text = TEMPLATES[f.category]
    return ActionItem(
        f.finding_id,
        f.product_id,
        f.product_name,
        f.category,
        action_type.value,
        text,
        f.message_ids[:5],
        ACTION_SOURCE_TEMPLATE,
        fact_summary(f, currency),
        note,
    )


def validate_actions(raw: str, bundles: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """Return accepted actions by finding_id plus rejection notes."""
    notes: list[str] = []
    try:
        parsed = ActionPlanResponse.model_validate_json(raw)
    except (ValidationError, ValueError):
        return {}, ["Action response did not match the schema."]
    by_id = {b["finding_id"]: b for b in bundles}
    accepted: dict[str, dict] = {}
    for a in parsed.actions:
        b = by_id.get(a.finding_id)
        if b is None:
            notes.append(f"Ignored action for unknown finding '{a.finding_id}'.")
            continue
        if a.product_id != b["product_id"]:
            notes.append(f"{a.finding_id}: product ID mismatch.")
            continue
        allowed = set(b["allowed_evidence_ids"])
        if not a.evidence_ids or any(e not in allowed for e in a.evidence_ids):
            notes.append(f"{a.finding_id}: cited evidence outside this finding.")
            continue
        if any(ch.isdigit() for ch in a.recommendation):
            notes.append(f"{a.finding_id}: recommendation contained numbers (figures come from Python).")
            continue
        if _CAUSAL.search(a.recommendation):
            notes.append(f"{a.finding_id}: recommendation made a definitive causal or savings claim.")
            continue
        if a.finding_id in accepted:
            notes.append(f"{a.finding_id}: duplicate action ignored.")
            continue
        accepted[a.finding_id] = {
            "action_type": a.action_type.value,
            "recommendation": a.recommendation.strip(),
            "evidence_ids": list(dict.fromkeys(a.evidence_ids)),
        }
    return accepted, notes


def generate_action_plan(
    state: AnalysisState,
    report: Report,
    filters: Filters,
    provider: Provider | None,
) -> ActionPlan:
    """Build the action plan for the current report version and scope.

    ``provider`` is the live or synthetic provider, or None for templates only.
    Any rejected or missing suggestion falls back to a labelled template.
    """
    currency = report.currency
    findings = report.all_findings[:MAX_ACTION_FINDINGS]
    bundles = build_fact_bundles(state, report)
    accepted: dict[str, dict] = {}
    notes: list[str] = []
    source = ACTION_SOURCE_TEMPLATE
    if provider is not None and bundles:
        source = ACTION_SOURCE_LIVE if provider.source.value == "live_gemini" else ACTION_SOURCE_SYNTHETIC
        try:
            raw = provider.suggest_actions(bundles)
            accepted, notes = validate_actions(raw, bundles)
        except ProviderError as err:
            notes.append(f"Suggestion provider unavailable: {err.message}")

    items: list[ActionItem] = []
    for f in findings:
        a = accepted.get(f.finding_id)
        if a is None:
            reason = None
            if provider is not None:
                reason = "No valid suggestion was returned for this finding; showing the reviewed template."
            items.append(template_action(f, currency, reason))
        else:
            items.append(
                ActionItem(
                    f.finding_id,
                    f.product_id,
                    f.product_name,
                    f.category,
                    a["action_type"],
                    a["recommendation"],
                    a["evidence_ids"],
                    source,
                    fact_summary(f, currency),
                )
            )
    summary = ACTION_SOURCE_LABELS[source] if accepted else ACTION_SOURCE_LABELS[ACTION_SOURCE_TEMPLATE]
    if provider is not None and source == ACTION_SOURCE_LIVE:
        summary += f" · model {provider.model_id} · prompt {ACTION_PROMPT_VERSION}"
    plan = ActionPlan(
        report_version=state.report_version,
        scope_key=scope_key(filters),
        generated_at_utc=utc_now(),
        source_summary=summary,
        items=items,
        notes=notes,
    )
    state.actions = plan
    return plan


def current_actions(state: AnalysisState, filters: Filters) -> ActionPlan | None:
    """Return stored actions only if they match this report version and scope."""
    plan = state.actions
    if plan is None:
        return None
    if plan.report_version != state.report_version or plan.scope_key != scope_key(filters):
        return None
    return plan


def default_template_plan(state: AnalysisState, report: Report, filters: Filters) -> ActionPlan:
    """Template-only plan used for exports when no suggestions were generated."""
    return ActionPlan(
        report_version=state.report_version,
        scope_key=scope_key(filters),
        generated_at_utc=utc_now(),
        source_summary=ACTION_SOURCE_LABELS[ACTION_SOURCE_TEMPLATE],
        items=[template_action(f, report.currency) for f in report.all_findings[:MAX_ACTION_FINDINGS]],
    )


# ----------------------------------------------------------------- exports

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value) -> str:
    """Neutralise spreadsheet formulas in free text by prefixing an apostrophe."""
    text = "" if value is None else str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def metadata(state: AnalysisState, filters: Filters) -> dict:
    assert state.dataset is not None
    model_ids = sorted({p.model_id for p in state.predictions.values()}) or ["none"]
    return {
        "app": "ShopDetective",
        "app_version": APP_VERSION,
        "report_format_version": REPORT_FORMAT_VERSION,
        "report_version": state.report_version,
        "generated_at_utc": utc_now(),
        "currency": state.dataset.currency,
        "analysis_mode": state.prediction_mode or state.mode,
        "dataset_digest": state.dataset.digest,
        "dataset_is_synthetic_sample": state.dataset.is_synthetic_sample,
        "filter_scope": filters.describe(),
        "filter_semantics": FILTER_SEMANTICS,
        "model_ids": model_ids,
        "classification_prompt_version": PROMPT_VERSION,
        "action_prompt_version": ACTION_PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
    }


def _csv_bytes(rows: list[dict], columns: list[str], meta: dict) -> bytes:
    meta_cols = {
        "report_version": meta["report_version"],
        "generated_at_utc": meta["generated_at_utc"],
        "currency": meta["currency"],
        "analysis_mode": meta["analysis_mode"],
        "dataset_digest": meta["dataset_digest"],
        "filter_scope": json.dumps(meta["filter_scope"], sort_keys=True),
        "filter_semantics": meta["filter_semantics"],
        "model_ids": ";".join(meta["model_ids"]),
        "prompt_version": meta["classification_prompt_version"],
        "schema_version": meta["schema_version"],
    }
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns + list(meta_cols), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {**{k: csv_safe(row.get(k)) for k in columns}, **{k: csv_safe(v) for k, v in meta_cols.items()}}
        )
    return buf.getvalue().encode("utf-8-sig")


def findings_rows(report: Report) -> list[dict]:
    rows = []
    for f in report.findings:
        rows.append(
            {
                "rank": f.rank,
                "finding_id": f.finding_id,
                "product_id": f.product_id,
                "product_name": f.product_name,
                "issue": f.category,
                "associated_recorded_refunds": minor_to_decimal_str(f.associated_refund_minor),
                "refunded_purchase_lines": f.refunded_lines,
                "purchase_lines_with_issue": f.affected_lines,
                "zero_refund_lines_with_issue": f.zero_refund_lines,
                "supporting_messages": f.supporting_messages,
                "feedback_covered_refunded_lines_product": f.covered_refunded_lines_product,
                "share_of_covered_refunded_lines": format_ratio(f.refunded_lines, f.covered_refunded_lines_product),
                "product_recorded_refunds": minor_to_decimal_str(f.product_refund_minor),
                "review_status": f.review_status,
                "order_line_ids": ";".join(f.order_line_ids),
                "message_ids": ";".join(f.message_ids),
            }
        )
    return rows


def findings_csv(state: AnalysisState, report: Report, filters: Filters) -> bytes:
    rows = findings_rows(report)
    cols = (
        list(rows[0])
        if rows
        else [
            "rank",
            "finding_id",
            "product_id",
            "product_name",
            "issue",
            "associated_recorded_refunds",
        ]
    )
    return _csv_bytes(rows, cols, metadata(state, filters))


def evidence_rows(state: AnalysisState, report: Report) -> list[dict]:
    assert state.dataset is not None
    fb = state.effective_feedback()
    orders = state.dataset.orders.set_index("order_line_id")
    scope = set(report.scope_order_ids)
    rows = []
    for rec in fb.itertuples(index=False):
        in_scope = rec.order_line_id in scope
        if rec.link_status != "unknown" and not in_scope:
            continue
        pred = state.predictions.get(rec.message_id)
        item = report.items.get(rec.order_line_id) if in_scope else None
        order = orders.loc[rec.order_line_id] if rec.order_line_id in orders.index else None
        rows.append(
            {
                "message_id": rec.message_id,
                "order_line_id": rec.order_line_id,
                "original_order_line_id": rec.original_order_line_id,
                "link_status": rec.link_status,
                "message": rec.message,
                "message_source_row": rec.source_row,
                "product_id": "" if order is None else order["product_id"],
                "purchase_date": "" if order is None else order["purchase_date"].isoformat(),
                "sale_amount": "" if order is None else minor_to_decimal_str(int(order["sale_minor"])),
                "refund_amount": "" if order is None else minor_to_decimal_str(int(order["refund_minor"])),
                "order_source_row": "" if order is None else int(order["source_row"]),
                "message_status": str(state.message_status.get(rec.message_id, "")),
                "predicted_category": pred.category.value if pred else "",
                "predicted_detail": pred.detail.value if pred else "",
                "evidence_quote": pred.evidence_quote if pred and pred.evidence_quote else "",
                "quote_valid": pred.quote_valid if pred else "",
                "needs_review": pred.needs_review if pred else "",
                "review_reason": pred.review_reason if pred and pred.review_reason else "",
                "prediction_source": SOURCE_LABELS.get(pred.source.value, pred.source.value) if pred else "",
                "model_id": pred.model_id if pred else "",
                "item_status": item.status if item else "outside_scope_or_unlinked",
                "item_accepted_issue": (item.category or "") if item else "",
            }
        )
    return rows


def evidence_csv(state: AnalysisState, report: Report, filters: Filters) -> bytes:
    rows = evidence_rows(state, report)
    cols = list(rows[0]) if rows else ["message_id", "order_line_id", "message"]
    return _csv_bytes(rows, cols, metadata(state, filters))


def corrections_csv(state: AnalysisState, filters: Filters) -> bytes:
    rows = [c.to_row() for c in state.corrections]
    cols = (
        list(rows[0])
        if rows
        else [
            "correction_id",
            "timestamp_utc",
            "reviewer",
            "kind",
            "order_line_id",
            "message_id",
            "before",
            "after",
            "supporting_message_ids",
            "reason",
            "report_version_before",
            "report_version_after",
            "dataset_digest",
        ]
    )
    return _csv_bytes(rows, cols, metadata(state, filters))


def _plan_for_export(state: AnalysisState, report: Report, filters: Filters) -> ActionPlan:
    return current_actions(state, filters) or default_template_plan(state, report, filters)


def report_json(state: AnalysisState, report: Report, filters: Filters) -> bytes:
    plan = _plan_for_export(state, report, filters)
    payload = {
        "metadata": metadata(state, filters),
        "language_note": (
            "Issue-associated refunds are recorded refunds on purchase lines whose accepted "
            "feedback issue matches; this is an association, not proof of cause. Refunded "
            "purchase lines are not necessarily returned units or unique customers."
        ),
        "overview": {
            "purchase_lines": report.scope_lines,
            "recorded_sales_minor": report.sales_minor,
            "recorded_refunds_minor": report.refund_minor,
            "refunded_purchase_lines": report.refunded_lines,
            "refunded_purchase_line_rate": {"numerator": report.refunded_lines, "denominator": report.scope_lines},
            "feedback_coverage": {
                "numerator": report.covered_refunded_lines,
                "denominator": report.refunded_lines,
                "definition": "refunded purchase lines with at least one linked message / refunded purchase lines",
            },
            "issue_associated_refunds_minor": report.attributed_refund_minor,
            "unassigned_refunds_minor": report.unassigned_refund_minor,
            "unassigned_breakdown": report.unassigned_breakdown,
            "reconciles": report.reconciles,
        },
        "findings": [
            {
                "rank": f.rank,
                "finding_id": f.finding_id,
                "product_id": f.product_id,
                "product_name": f.product_name,
                "issue": f.category,
                "associated_refund_minor": f.associated_refund_minor,
                "refunded_purchase_lines": f.refunded_lines,
                "purchase_lines_with_issue": f.affected_lines,
                "zero_refund_lines_with_issue": f.zero_refund_lines,
                "supporting_message_count": f.supporting_messages,
                "feedback_covered_refunded_lines_product": f.covered_refunded_lines_product,
                "detail_counts": f.detail_counts,
                "review_status": f.review_status,
                "order_line_ids": f.order_line_ids,
                "message_ids": f.message_ids,
            }
            for f in report.findings
        ],
        "action_plan": {
            "report_version": plan.report_version,
            "source": plan.source_summary,
            "items": [
                {
                    "finding_id": a.finding_id,
                    "product_id": a.product_id,
                    "action_type": a.action_type,
                    "recommendation": a.recommendation,
                    "evidence_ids": a.evidence_ids,
                    "source": a.source,
                    "verified_facts": a.fact_summary,
                }
                for a in plan.items
            ],
        },
        "corrections": [c.to_row() for c in state.corrections],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str).encode("utf-8")


def _md_escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]<>#|])", r"\\\1", text)


def action_plan_markdown(state: AnalysisState, report: Report, filters: Filters) -> bytes:
    meta = metadata(state, filters)
    plan = _plan_for_export(state, report, filters)
    cur = report.currency
    lines = [
        "# ShopDetective action plan",
        "",
        f"- Report version: {meta['report_version']} · generated {meta['generated_at_utc']} (UTC)",
        f"- Analysis mode: {meta['analysis_mode']} · currency {cur}",
        f"- Dataset digest: `{meta['dataset_digest'][:16]}…`"
        + (" (synthetic sample data)" if meta["dataset_is_synthetic_sample"] else ""),
        f"- Scope: `{json.dumps(meta['filter_scope'], sort_keys=True)}`",
        f"- Filter meaning: {meta['filter_semantics']}",
        f"- Models: {', '.join(meta['model_ids'])} · prompt {meta['classification_prompt_version']}"
        f" · schema {meta['schema_version']}",
        f"- Suggestions source: {plan.source_summary}",
        "",
        "## Overview (recorded data)",
        "",
        f"- Recorded sales: {format_minor(report.sales_minor, cur)}",
        f"- Recorded refunds: {format_minor(report.refund_minor, cur)}",
        f"- Refunded purchase-line rate: {format_ratio(report.refunded_lines, report.scope_lines)}",
        f"- Feedback coverage of refunded lines: {format_ratio(report.covered_refunded_lines, report.refunded_lines)}",
        f"- Refunds associated with an accepted issue: {format_minor(report.attributed_refund_minor, cur)}",
        f"- Refunds without an accepted issue (unassigned): {format_minor(report.unassigned_refund_minor, cur)}",
        "",
        "Associations are investigation leads, not proof of cause or estimated savings.",
        "",
        "## Investigations to review, in priority order",
        "",
    ]
    if not plan.items:
        lines.append("No product–issue findings in this scope.")
    for i, a in enumerate(plan.items, start=1):
        lines += [
            f"### {i}. {_md_escape(a.product_name)} — {CATEGORY_LABELS[a.category]}",
            "",
            f"**Verified facts:** {_md_escape(a.fact_summary)}",
            "",
            f"**Suggested investigation:** {_md_escape(a.recommendation)}",
            "",
            f"- Evidence IDs: {', '.join(a.evidence_ids) or 'none'}",
            f"- Source: {ACTION_SOURCE_LABELS[a.source]}" + (f" — {a.note}" if a.note else ""),
            "",
        ]
    lines += [
        "---",
        "This plan was generated by ShopDetective. It does not contact customers or suppliers, "
        "process refunds or change inventory.",
    ]
    return "\n".join(lines).encode("utf-8")
