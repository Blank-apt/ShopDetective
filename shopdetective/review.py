"""Review queue and owner corrections.

Corrections are separate audit records. They never overwrite uploaded data
or model predictions; they supersede the accepted item-level assignment (or
add an explicit item link) and create a new report version.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .analytics import NEEDS_REVIEW, PENDING, resolve_items
from .config import REVIEWER_ID
from .schemas import COMPLAINT_CATEGORIES, MessageStatus
from .session import AnalysisState, Correction
from .validation import quote_is_supported, utc_now

ASSIGNABLE_CATEGORIES = tuple(c.value for c in COMPLAINT_CATEGORIES) + ("no_complaint",)
ASSIGNABLE_DETAILS: dict[str, tuple[str, ...]] = {
    "sizing": ("too_small", "too_large", "other"),
    "delivery": ("late_delivery", "missing_delivery", "damaged", "other"),
    "wrong_item": ("wrong_product", "wrong_color", "wrong_size_sent", "other"),
    "other": ("quality", "damaged", "other"),
    "no_complaint": ("none",),
}
MAX_REASON_CHARS = 300


class CorrectionError(ValueError):
    pass


@dataclass
class QueueItem:
    kind: str  # "item" | "unknown_link" | "not_sent_unlinked"
    key: str  # order_line_id for items, message_id for link issues
    reason: str
    message_ids: list[str]


def review_queue(state: AnalysisState) -> list[QueueItem]:
    """Items needing an owner decision, most actionable first."""
    if state.dataset is None:
        return []
    items = resolve_items(state)
    queue: list[QueueItem] = []
    for olid, item in items.items():
        if item.status == NEEDS_REVIEW:
            queue.append(QueueItem("item", olid, item.reason or "Needs review", item.message_ids))
        elif item.status == PENDING and state.prediction_mode is not None:
            queue.append(QueueItem("item", olid, "Not yet classified", item.message_ids))
    fb = state.effective_feedback()
    for rec in fb[fb["link_status"] == "unknown"].itertuples(index=False):
        queue.append(
            QueueItem(
                "unknown_link",
                rec.message_id,
                f"References unknown order_line_id '{rec.order_line_id}'",
                [rec.message_id],
            )
        )
    queue.sort(key=lambda q: (q.kind != "item", q.key))
    return queue


def _new_correction(state: AnalysisState, **kw) -> Correction:
    assert state.dataset is not None
    return Correction(
        correction_id=f"C-{uuid.uuid4().hex[:8]}",
        timestamp_utc=utc_now(),
        reviewer=REVIEWER_ID,
        dataset_digest=state.dataset.digest,
        report_version_before=state.report_version,
        report_version_after=state.report_version + 1,
        **kw,
    )


def _check_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise CorrectionError("Add a short reason for the correction.")
    if len(reason) > MAX_REASON_CHARS:
        raise CorrectionError(f"Keep the reason under {MAX_REASON_CHARS} characters.")
    return reason


def apply_item_correction(
    state: AnalysisState,
    order_line_id: str,
    category: str,
    detail: str,
    supporting_message_ids: list[str],
    reason: str,
    quotes: dict[str, str] | None = None,
) -> Correction:
    """Set the accepted primary issue for one purchase line."""
    if state.dataset is None:
        raise CorrectionError("Load data first.")
    reason = _check_reason(reason)
    if order_line_id not in set(state.dataset.orders["order_line_id"]):
        raise CorrectionError(f"Purchase line '{order_line_id}' does not exist.")
    if category not in ASSIGNABLE_CATEGORIES:
        raise CorrectionError(f"Category '{category}' cannot be assigned.")
    if detail not in ASSIGNABLE_DETAILS[category]:
        raise CorrectionError(f"Detail '{detail}' is not valid for '{category}'.")

    fb = state.effective_feedback()
    linked = fb[fb["order_line_id"] == order_line_id].set_index("message_id")
    supporting = list(dict.fromkeys(supporting_message_ids))
    unknown = [m for m in supporting if m not in linked.index]
    if unknown:
        raise CorrectionError("Supporting messages must be linked to this purchase line: " + ", ".join(unknown))
    if category != "no_complaint" and not supporting:
        raise CorrectionError("Select at least one supporting customer message for a complaint category.")

    checked_quotes: dict[str, str] = {}
    for mid in supporting:
        text = linked.loc[mid, "message"]
        quote = (quotes or {}).get(mid, "").strip()
        if not quote:
            pred = state.predictions.get(mid)
            if pred is not None and pred.quote_valid and pred.evidence_quote:
                quote = pred.evidence_quote
        if quote:
            if not quote_is_supported(quote, text):
                raise CorrectionError(f"The quote for {mid} is not an exact excerpt of that message.")
            checked_quotes[mid] = quote
        elif category != "no_complaint":
            if not str(text).strip():
                raise CorrectionError(f"Message {mid} is empty and cannot support a complaint.")
            checked_quotes[mid] = str(text)[:300]

    item = resolve_items(state)[order_line_id]
    before = {
        "status": item.status,
        "category": item.category or "none",
        "detail": item.detail or "none",
        "model_labels": ",".join(
            f"{m}:{state.predictions[m].category.value}" for m in item.message_ids if m in state.predictions
        )
        or "none",
    }
    corr = _new_correction(
        state,
        kind="item_assignment",
        order_line_id=order_line_id,
        message_id=None,
        before=before,
        after={"category": category, "detail": detail},
        supporting_message_ids=supporting,
        supporting_quotes=checked_quotes,
        reason=reason,
    )
    state.corrections.append(corr)
    state.bump_version()
    return corr


def apply_link_correction(state: AnalysisState, message_id: str, order_line_id: str, reason: str) -> Correction:
    """Explicitly link a message with an unknown reference to an existing line.

    Only an exact, existing order_line_id chosen by the owner is accepted;
    nothing is inferred from product wording.
    """
    if state.dataset is None:
        raise CorrectionError("Load data first.")
    reason = _check_reason(reason)
    fb = state.effective_feedback().set_index("message_id")
    if message_id not in fb.index:
        raise CorrectionError(f"Message '{message_id}' does not exist.")
    if fb.loc[message_id, "link_status"] != "unknown":
        raise CorrectionError(f"Message '{message_id}' is already linked to a purchase line.")
    order_line_id = order_line_id.strip()
    if order_line_id not in set(state.dataset.orders["order_line_id"]):
        raise CorrectionError(f"'{order_line_id}' is not an existing order_line_id.")
    corr = _new_correction(
        state,
        kind="item_link",
        order_line_id=order_line_id,
        message_id=message_id,
        before={"order_line_id": fb.loc[message_id, "original_order_line_id"], "link_status": "unknown"},
        after={"order_line_id": order_line_id, "link_status": "corrected"},
        supporting_message_ids=[message_id],
        supporting_quotes={},
        reason=reason,
    )
    state.corrections.append(corr)
    if state.message_status.get(message_id) == MessageStatus.FAILED:
        state.message_status[message_id] = MessageStatus.PENDING
    state.bump_version()
    return corr
