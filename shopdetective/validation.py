"""Evidence gate: validates provider responses before they enter the dataset.

Checks: JSON shape, one result per requested message, known IDs, allowed
category/detail values and *exact* source-quote support. The only text
normalization is line endings (CRLF/CR -> LF) on both quote and message.
No fuzzy matching is used, so a quotation that is not literally in the message
is rejected. (This proves the text exists, not that the category is right.)
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import ValidationError

from .schemas import (
    COMPLAINT_CATEGORIES,
    Category,
    ClassificationResult,
    Prediction,
    PredictionSource,
)


def normalize_line_endings(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def quote_is_supported(quote: str | None, source_text: str) -> bool:
    """True when ``quote`` is a non-blank exact substring of ``source_text``."""
    if quote is None or quote.strip() == "":
        return False
    return normalize_line_endings(quote) in normalize_line_endings(source_text)


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BatchValidation:
    accepted: dict[str, Prediction] = field(default_factory=dict)
    retry_ids: set[str] = field(default_factory=set)
    issue_counts: Counter = field(default_factory=Counter)

    @property
    def schema_failed(self) -> bool:
        return self.issue_counts.get("invalid_json", 0) > 0 or self.issue_counts.get("invalid_shape", 0) > 0


def build_prediction(
    result: ClassificationResult,
    source_text: str,
    source: PredictionSource,
    model_id: str,
    prompt_version: str,
    schema_version: str,
) -> Prediction:
    notes: list[str] = []
    needs_review = result.needs_review
    review_reason = result.review_reason
    quote = result.evidence_quote
    if quote is not None and quote.strip() == "":
        quote = None
    quote_valid = quote_is_supported(quote, source_text)

    if result.category in COMPLAINT_CATEGORIES:
        if quote is None:
            notes.append("missing_quote")
            needs_review = True
            review_reason = review_reason or "No supporting quotation was returned."
        elif not quote_valid:
            notes.append("unsupported_quote")
            needs_review = True
            review_reason = "Quoted text is not an exact substring of the message."
    elif quote is not None and not quote_valid:
        notes.append("unsupported_quote")
        needs_review = True
        review_reason = "Quoted text is not an exact substring of the message."

    if result.category == Category.UNCLEAR and not needs_review:
        needs_review = True
        review_reason = review_reason or "Classified as unclear."
    if needs_review and not review_reason:
        review_reason = "Flagged for review by the classifier."

    return Prediction(
        message_id=result.message_id,
        category=result.category,
        detail=result.detail,
        evidence_quote=quote,
        needs_review=needs_review,
        review_reason=review_reason,
        source=source,
        model_id=model_id,
        prompt_version=prompt_version,
        schema_version=schema_version,
        quote_valid=quote_valid,
        created_at_utc=utc_now(),
        validation_notes=notes,
    )


def validate_classification_response(
    raw: str | dict,
    requested: dict[str, str],
    source: PredictionSource,
    model_id: str,
    prompt_version: str,
    schema_version: str,
) -> BatchValidation:
    """Validate one batch response against the requested ``{message_id: text}``.

    Valid items are accepted individually, so one malformed item does not
    discard the rest. Requested IDs that are missing, duplicated or malformed
    are returned in ``retry_ids``. Unknown IDs are ignored and counted.
    Items whose ``message_id`` is not a string (list, dict, null, number) are
    rejected before any counting, so they cannot raise or block valid items.
    """
    out = BatchValidation()
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        out.issue_counts["invalid_json"] += 1
        out.retry_ids = set(requested)
        return out
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        out.issue_counts["invalid_shape"] += 1
        out.retry_ids = set(requested)
        return out

    items = payload["results"]
    # Only string IDs are hashable and meaningful; count nothing else.
    id_counts = Counter(
        item["message_id"] for item in items if isinstance(item, dict) and isinstance(item.get("message_id"), str)
    )
    for item in items:
        if not isinstance(item, dict):
            out.issue_counts["malformed_item"] += 1
            continue
        mid = item.get("message_id")
        if not isinstance(mid, str):
            out.issue_counts["malformed_id"] += 1
            continue
        if mid not in requested:
            out.issue_counts["unknown_id"] += 1
            continue
        if id_counts[mid] > 1:
            out.issue_counts["duplicate_id"] += 1
            out.retry_ids.add(mid)
            continue
        try:
            result = ClassificationResult.model_validate(item)
        except ValidationError:
            out.issue_counts["invalid_item"] += 1
            out.retry_ids.add(mid)
            continue
        out.accepted[mid] = build_prediction(result, requested[mid], source, model_id, prompt_version, schema_version)

    for mid in requested:
        if mid not in out.accepted and mid not in out.retry_ids:
            out.issue_counts["missing_id"] += 1
            out.retry_ids.add(mid)
    return out
