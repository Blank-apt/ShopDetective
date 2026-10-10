"""Deterministic business analytics on integer minor units.

1. ``resolve_items`` turns message-level observations into at most ONE
   accepted primary issue per purchase line (owner corrections win).
2. ``compute_report`` aggregates unique purchase lines in the filtered scope:
   totals, rates with explicit denominators, ranked product-issue findings,
   unassigned refunds and a reconciliation check.

Nothing here calls a model or reads model prose. Amounts come only from the
validated orders table, so duplicate messages can never multiply a refund.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .schemas import COMPLAINT_CATEGORIES, Category, Filters, MessageStatus
from .session import AnalysisState

COMPLAINT_VALUES = tuple(c.value for c in COMPLAINT_CATEGORIES)

# Item-level statuses
ACCEPTED = "accepted"  # consistent, validated model/reference observations
REVIEWED = "reviewed"  # owner correction
NO_COMPLAINT = "no_complaint"
NO_FEEDBACK = "no_feedback"
NEEDS_REVIEW = "needs_review"
PENDING = "pending"

ITEM_STATUS_LABELS = {
    ACCEPTED: "Accepted label",
    REVIEWED: "Owner reviewed",
    NO_COMPLAINT: "No complaint",
    NO_FEEDBACK: "No feedback",
    NEEDS_REVIEW: "Needs review",
    PENDING: "Not yet classified",
}


@dataclass
class ItemAssignment:
    order_line_id: str
    status: str
    category: str | None  # accepted complaint category, else None
    detail: str | None
    message_ids: list[str]  # all messages linked to the line
    supporting_message_ids: list[str]  # evidence for the accepted category
    reason: str | None = None  # why it is unresolved, if it is

    @property
    def has_feedback(self) -> bool:
        return bool(self.message_ids)


def resolve_items(state: AnalysisState) -> dict[str, ItemAssignment]:
    assert state.dataset is not None
    fb = state.effective_feedback()
    linked = fb[fb["order_line_id"].isin(set(state.dataset.orders["order_line_id"]))]
    by_item: dict[str, list[str]] = {}
    for rec in linked.itertuples(index=False):
        by_item.setdefault(rec.order_line_id, []).append(rec.message_id)
    overrides = state.item_overrides()

    out: dict[str, ItemAssignment] = {}
    for olid in state.dataset.orders["order_line_id"]:
        mids = sorted(by_item.get(olid, []))
        corr = overrides.get(olid)
        if corr is not None:
            cat = corr.after.get("category")
            out[olid] = ItemAssignment(
                olid,
                REVIEWED,
                cat if cat in COMPLAINT_VALUES else None,
                corr.after.get("detail") if cat in COMPLAINT_VALUES else None,
                mids,
                list(corr.supporting_message_ids),
            )
            continue
        if not mids:
            out[olid] = ItemAssignment(olid, NO_FEEDBACK, None, None, [], [])
            continue
        out[olid] = _resolve_from_predictions(olid, mids, state)
    return out


def _resolve_from_predictions(olid: str, mids: list[str], state: AnalysisState) -> ItemAssignment:
    preds = []
    for mid in mids:
        status = state.message_status.get(mid)
        if status == MessageStatus.NOT_SENT:
            return ItemAssignment(
                olid, NEEDS_REVIEW, None, None, mids, [], "A linked message is empty or too long to send."
            )
        pred = state.predictions.get(mid)
        if pred is None or status != MessageStatus.CLASSIFIED:
            reason = "Classification failed; retry or review." if status == MessageStatus.FAILED else None
            return ItemAssignment(olid, PENDING if reason is None else NEEDS_REVIEW, None, None, mids, [], reason)
        preds.append(pred)

    flagged = [p for p in preds if p.needs_review]
    if flagged:
        return ItemAssignment(
            olid, NEEDS_REVIEW, None, None, mids, [], flagged[0].review_reason or "Flagged for review."
        )
    complaint = [p for p in preds if p.category.value in COMPLAINT_VALUES]
    categories = {p.category.value for p in complaint}
    positive = any(p.category == Category.NO_COMPLAINT for p in preds)
    if len(categories) > 1:
        return ItemAssignment(
            olid,
            NEEDS_REVIEW,
            None,
            None,
            mids,
            [],
            "Messages for this purchase give different complaint categories (" + ", ".join(sorted(categories)) + ").",
        )
    if categories and positive:
        return ItemAssignment(
            olid, NEEDS_REVIEW, None, None, mids, [], "Positive and complaint messages conflict for this purchase."
        )
    if not categories:
        return ItemAssignment(olid, NO_COMPLAINT, None, None, mids, [])
    cat = categories.pop()
    details = Counter(p.detail.value for p in complaint)
    detail = details.most_common(1)[0][0] if len(details) == 1 else "mixed_details"
    return ItemAssignment(olid, ACCEPTED, cat, detail, mids, [p.message_id for p in complaint])


# ------------------------------------------------------------------ report


@dataclass
class Finding:
    finding_id: str
    rank: int
    product_id: str
    product_name: str
    category: str
    associated_refund_minor: int
    refunded_lines: int  # distinct refunded purchase lines with this issue
    affected_lines: int  # distinct lines with this issue (incl. zero refund)
    zero_refund_lines: int
    supporting_messages: int
    covered_refunded_lines_product: int  # denominator for share
    product_refund_minor: int
    reviewed_lines: int
    detail_counts: dict[str, int]
    order_line_ids: list[str]
    message_ids: list[str]

    @property
    def review_status(self) -> str:
        if self.reviewed_lines == 0:
            return "model/reference labels"
        if self.reviewed_lines == self.affected_lines:
            return "all owner reviewed"
        return "partly owner reviewed"

    @property
    def share_of_covered(self) -> tuple[int, int]:
        return self.refunded_lines, self.covered_refunded_lines_product


@dataclass
class ProductSummary:
    product_id: str
    product_name: str
    lines: int
    sales_minor: int
    refund_minor: int
    refunded_lines: int
    covered_refunded_lines: int
    unassigned_refund_minor: int
    issue_refund_minor: dict[str, int]


@dataclass
class Report:
    currency: str
    scope_lines: int
    sales_minor: int
    refund_minor: int
    refunded_lines: int
    covered_refunded_lines: int
    attributed_refund_minor: int
    unassigned_refund_minor: int
    unassigned_breakdown: dict[str, dict[str, int]]  # status -> {lines, refund_minor}
    findings: list[Finding]  # ranked, after display filters
    all_findings: list[Finding]  # ranked, before display filters
    products: list[ProductSummary]
    items: dict[str, ItemAssignment]
    scope_order_ids: list[str]
    review_counts: dict[str, int] = field(default_factory=dict)

    @property
    def reconciles(self) -> bool:
        return self.attributed_refund_minor + self.unassigned_refund_minor == self.refund_minor


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def format_ratio(numerator: int, denominator: int) -> str:
    r = ratio(numerator, denominator)
    return "n/a (no lines)" if r is None else f"{numerator}/{denominator} = {r:.0%}"


def scope_orders(orders: pd.DataFrame, filters: Filters) -> pd.DataFrame:
    scoped = orders
    if filters.product_ids:
        scoped = scoped[scoped["product_id"].isin(filters.product_ids)]
    if filters.date_from is not None:
        scoped = scoped[scoped["purchase_date"] >= filters.date_from]
    if filters.date_to is not None:
        scoped = scoped[scoped["purchase_date"] <= filters.date_to]
    return scoped


def compute_report(state: AnalysisState, filters: Filters | None = None) -> Report:
    assert state.dataset is not None
    filters = filters or Filters()
    ds = state.dataset
    items = resolve_items(state)
    scoped = scope_orders(ds.orders, filters)

    sales = int(scoped["sale_minor"].sum())
    refunds = int(scoped["refund_minor"].sum())
    refunded_mask = scoped["refund_minor"] > 0

    group: dict[tuple[str, str], dict] = {}
    unassigned: dict[str, dict[str, int]] = {}
    products: dict[str, ProductSummary] = {}
    covered_total = 0
    attributed = 0
    unassigned_total = 0

    for rec in scoped.itertuples(index=False):
        olid, pid = rec.order_line_id, rec.product_id
        item = items[olid]
        refund = int(rec.refund_minor)
        ps = products.get(pid)
        if ps is None:
            ps = products[pid] = ProductSummary(pid, ds.display_names.get(pid, rec.product_name), 0, 0, 0, 0, 0, 0, {})
        ps.lines += 1
        ps.sales_minor += int(rec.sale_minor)
        ps.refund_minor += refund
        if refund > 0:
            ps.refunded_lines += 1
            if item.has_feedback:
                ps.covered_refunded_lines += 1
                covered_total += 1
        if item.category is not None:
            g = group.setdefault(
                (pid, item.category),
                {
                    "refund": 0,
                    "refunded": 0,
                    "affected": 0,
                    "zero": 0,
                    "reviewed": 0,
                    "details": Counter(),
                    "olids": [],
                    "mids": set(),
                },
            )
            g["refund"] += refund
            g["affected"] += 1
            g["refunded"] += 1 if refund > 0 else 0
            g["zero"] += 1 if refund == 0 else 0
            g["reviewed"] += 1 if item.status == REVIEWED else 0
            g["details"][item.detail or "unspecified"] += 1
            g["olids"].append(olid)
            g["mids"].update(item.supporting_message_ids)
            attributed += refund
            ps.issue_refund_minor[item.category] = ps.issue_refund_minor.get(item.category, 0) + refund
        elif refund > 0:
            bucket = unassigned.setdefault(item.status, {"lines": 0, "refund_minor": 0})
            bucket["lines"] += 1
            bucket["refund_minor"] += refund
            unassigned_total += refund
            ps.unassigned_refund_minor += refund

    findings: list[Finding] = []
    for (pid, cat), g in group.items():
        ps = products[pid]
        findings.append(
            Finding(
                finding_id=f"{pid}:{cat}",
                rank=0,
                product_id=pid,
                product_name=ps.product_name,
                category=cat,
                associated_refund_minor=g["refund"],
                refunded_lines=g["refunded"],
                affected_lines=g["affected"],
                zero_refund_lines=g["zero"],
                supporting_messages=len(g["mids"]),
                covered_refunded_lines_product=ps.covered_refunded_lines,
                product_refund_minor=ps.refund_minor,
                reviewed_lines=g["reviewed"],
                detail_counts=dict(g["details"]),
                order_line_ids=sorted(g["olids"]),
                message_ids=sorted(g["mids"]),
            )
        )
    findings.sort(key=lambda f: (-f.associated_refund_minor, -f.refunded_lines, f.product_id, f.category))
    for i, f in enumerate(findings, start=1):
        f.rank = i

    shown = findings
    if filters.categories:
        shown = [f for f in shown if f.category in filters.categories]
    if filters.review_statuses:
        shown = [f for f in shown if f.review_status in filters.review_statuses]

    scope_ids = list(scoped["order_line_id"])
    review_counts = Counter(items[o].status for o in scope_ids)
    report = Report(
        currency=ds.currency,
        scope_lines=len(scoped),
        sales_minor=sales,
        refund_minor=refunds,
        refunded_lines=int(refunded_mask.sum()),
        covered_refunded_lines=covered_total,
        attributed_refund_minor=attributed,
        unassigned_refund_minor=unassigned_total,
        unassigned_breakdown=unassigned,
        findings=shown,
        all_findings=findings,
        products=sorted(products.values(), key=lambda p: (-p.refund_minor, p.product_id)),
        items=items,
        scope_order_ids=scope_ids,
        review_counts=dict(review_counts),
    )
    if not report.reconciles:  # pragma: no cover - guarded by tests
        raise AssertionError("Refund reconciliation failed: attributed + unassigned != total")
    return report


def product_labels(products: dict[str, str]) -> dict[str, str]:
    """product_id -> display label; IDs are appended when display names collide.

    Analytics never merges product IDs, so charts and lists must not either.
    """
    seen = Counter(name.strip().casefold() for name in products.values())
    return {pid: f"{name} ({pid})" if seen[name.strip().casefold()] > 1 else name for pid, name in products.items()}


def product_metrics(report: Report, product_id: str) -> ProductSummary | None:
    return next((p for p in report.products if p.product_id == product_id), None)


def date_or_none(value) -> date | None:
    return value if isinstance(value, date) else None
