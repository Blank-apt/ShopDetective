"""Typed contracts shared by providers, validation, analytics and exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Category(StrEnum):
    SIZING = "sizing"
    DELIVERY = "delivery"
    WRONG_ITEM = "wrong_item"
    OTHER = "other"
    NO_COMPLAINT = "no_complaint"
    UNCLEAR = "unclear"


COMPLAINT_CATEGORIES: tuple[Category, ...] = (
    Category.SIZING,
    Category.DELIVERY,
    Category.WRONG_ITEM,
    Category.OTHER,
)

CATEGORY_LABELS: dict[str, str] = {
    "sizing": "Sizing / fit",
    "delivery": "Delivery",
    "wrong_item": "Wrong item",
    "other": "Other complaint",
    "no_complaint": "No complaint",
    "unclear": "Unclear",
}


class Detail(StrEnum):
    TOO_SMALL = "too_small"
    TOO_LARGE = "too_large"
    LATE_DELIVERY = "late_delivery"
    MISSING_DELIVERY = "missing_delivery"
    DAMAGED = "damaged"
    WRONG_PRODUCT = "wrong_product"
    WRONG_COLOR = "wrong_color"
    WRONG_SIZE_SENT = "wrong_size_sent"
    QUALITY = "quality"
    MIXED = "mixed"
    OTHER = "other"
    NONE = "none"


# The documented category -> allowed detail enum.
ALLOWED_DETAILS: dict[Category, frozenset[Detail]] = {
    Category.SIZING: frozenset({Detail.TOO_SMALL, Detail.TOO_LARGE, Detail.OTHER}),
    Category.DELIVERY: frozenset({Detail.LATE_DELIVERY, Detail.MISSING_DELIVERY, Detail.DAMAGED, Detail.OTHER}),
    Category.WRONG_ITEM: frozenset({Detail.WRONG_PRODUCT, Detail.WRONG_COLOR, Detail.WRONG_SIZE_SENT, Detail.OTHER}),
    Category.OTHER: frozenset({Detail.QUALITY, Detail.DAMAGED, Detail.OTHER}),
    Category.NO_COMPLAINT: frozenset({Detail.NONE}),
    Category.UNCLEAR: frozenset({Detail.MIXED, Detail.OTHER}),
}


class ClassificationResult(BaseModel):
    """One model (or reference) observation about one customer message."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=200)
    category: Category
    detail: Detail
    evidence_quote: str | None = Field(
        default=None,
        max_length=2000,
        description="Exact substring of the message supporting the category, or null.",
    )
    needs_review: bool
    review_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _detail_matches_category(self) -> ClassificationResult:
        if self.detail not in ALLOWED_DETAILS[self.category]:
            raise ValueError(f"detail '{self.detail.value}' is not allowed for category '{self.category.value}'")
        return self


class ClassificationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ClassificationResult]


class ActionType(StrEnum):
    MEASURE_SIZE_CHART = "measure_against_size_chart"
    REVIEW_FIT_GUIDANCE = "review_fit_guidance"
    CHECK_DISPATCH_RECORDS = "check_dispatch_records"
    REVIEW_COURIER_PERFORMANCE = "review_courier_performance"
    AUDIT_PICKING_PACKING = "audit_picking_packing"
    VERIFY_LISTING = "verify_listing_details"
    INSPECT_QUALITY = "inspect_product_quality"
    OTHER_INVESTIGATION = "other_investigation"


class ActionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1, max_length=200)
    product_id: str = Field(min_length=1, max_length=200)
    action_type: ActionType
    recommendation: str = Field(min_length=10, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=10)


class ActionPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ActionSuggestion]


class PredictionSource(StrEnum):
    LIVE_GEMINI = "live_gemini"
    CACHED_LIVE = "cached_live_result"
    SYNTHETIC_REFERENCE = "synthetic_reference"
    MANUAL_ONLY = "manual_only_review"


SOURCE_LABELS: dict[str, str] = {
    "live_gemini": "Live Gemini",
    "cached_live_result": "Cached live result (this session)",
    "synthetic_reference": "Synthetic reference label (no model called)",
    "manual_only_review": "Manual review (no model)",
    "manual_correction": "Manual correction",
}


class MessageStatus(StrEnum):
    PENDING = "pending"  # not yet classified (or awaiting manual label)
    CLASSIFIED = "classified"  # validated prediction stored
    FAILED = "failed"  # retries exhausted or provider error; retryable
    NOT_SENT = "not_sent"  # empty/oversized text: needs owner review


@dataclass
class Prediction:
    message_id: str
    category: Category
    detail: Detail
    evidence_quote: str | None
    needs_review: bool
    review_reason: str | None
    source: PredictionSource
    model_id: str
    prompt_version: str
    schema_version: str
    quote_valid: bool
    created_at_utc: str
    validation_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> Prediction:
        return cls(
            message_id=d["message_id"],
            category=Category(d["category"]),
            detail=Detail(d["detail"]),
            evidence_quote=d["evidence_quote"],
            needs_review=bool(d["needs_review"]),
            review_reason=d["review_reason"],
            source=PredictionSource(d["source"]),
            model_id=d["model_id"],
            prompt_version=d["prompt_version"],
            schema_version=d["schema_version"],
            quote_valid=bool(d["quote_valid"]),
            created_at_utc=d["created_at_utc"],
            validation_notes=list(d.get("validation_notes", [])),
        )

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "category": self.category.value,
            "detail": self.detail.value,
            "evidence_quote": self.evidence_quote,
            "needs_review": self.needs_review,
            "review_reason": self.review_reason,
            "source": self.source.value,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "quote_valid": self.quote_valid,
            "created_at_utc": self.created_at_utc,
            "validation_notes": list(self.validation_notes),
        }


@dataclass(frozen=True)
class ValidationIssue:
    level: str  # "error" | "warning"
    code: str
    message: str
    file: str
    row: int | None = None  # CSV line number (header is line 1)
    field: str | None = None


@dataclass(frozen=True)
class Filters:
    product_ids: tuple[str, ...] | None = None  # None = all products
    date_from: date | None = None
    date_to: date | None = None
    categories: tuple[str, ...] | None = None  # findings display filter only
    review_statuses: tuple[str, ...] | None = None  # findings display filter only

    def describe(self) -> dict:
        return {
            "product_ids": list(self.product_ids) if self.product_ids else "all",
            "purchase_date_from": self.date_from.isoformat() if self.date_from else None,
            "purchase_date_to": self.date_to.isoformat() if self.date_to else None,
            "finding_categories": list(self.categories) if self.categories else "all",
            "finding_review_statuses": (list(self.review_statuses) if self.review_statuses else "all"),
        }

    def financial_scope(self) -> dict:
        """The filters that decide which purchase lines (and money) are in scope."""
        d = self.describe()
        return {k: d[k] for k in ("product_ids", "purchase_date_from", "purchase_date_to")}


FILTER_SEMANTICS = (
    "product_ids and purchase dates set the financial scope for every total, chart, finding, evidence row and "
    "action suggestion. finding_categories and finding_review_statuses only narrow the displayed findings list "
    "and the findings rows in exports; charts, totals, evidence and the action plan are not narrowed by them."
)
