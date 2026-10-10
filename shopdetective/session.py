"""Per-session workspace. Nothing here is shared between browser sessions.

``AnalysisState`` is a plain object stored in ``st.session_state``; business
modules receive it explicitly, so they are testable without Streamlit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .schemas import MessageStatus, Prediction, ValidationIssue

MODE_SAMPLE = "Sample demonstration"
MODE_LIVE = "Live AI"
MODE_MANUAL = "Manual review"
MODES = (MODE_SAMPLE, MODE_LIVE, MODE_MANUAL)

STATE_KEY = "sd_state"


@dataclass
class Dataset:
    orders: pd.DataFrame  # canonical, validated purchase lines
    feedback: pd.DataFrame  # canonical messages (original link preserved)
    raw_orders: pd.DataFrame  # uploaded source as read (strings)
    raw_feedback: pd.DataFrame
    currency: str
    digest: str
    feedback_digest: str
    sample_name: str | None  # "demo" / "review_cases" for shipped samples
    file_info: dict
    issues: list[ValidationIssue]
    display_names: dict[str, str]  # product_id -> confirmed display name

    @property
    def is_synthetic_sample(self) -> bool:
        return self.sample_name is not None

    @property
    def date_bounds(self) -> tuple[date, date]:
        return self.orders["purchase_date"].min(), self.orders["purchase_date"].max()


@dataclass
class Correction:
    correction_id: str
    timestamp_utc: str
    reviewer: str
    dataset_digest: str
    report_version_before: int
    report_version_after: int
    kind: str  # "item_assignment" | "item_link"
    order_line_id: str
    message_id: str | None
    before: dict
    after: dict
    supporting_message_ids: list[str]
    supporting_quotes: dict[str, str]
    reason: str

    def to_row(self) -> dict:
        return {
            "correction_id": self.correction_id,
            "timestamp_utc": self.timestamp_utc,
            "reviewer": self.reviewer,
            "kind": self.kind,
            "order_line_id": self.order_line_id,
            "message_id": self.message_id or "",
            "before": _fmt(self.before),
            "after": _fmt(self.after),
            "supporting_message_ids": ";".join(self.supporting_message_ids),
            "reason": self.reason,
            "report_version_before": self.report_version_before,
            "report_version_after": self.report_version_after,
            "dataset_digest": self.dataset_digest,
        }


def _fmt(d: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in d.items())


@dataclass
class ActionPlan:
    report_version: int
    scope_key: str
    generated_at_utc: str
    source_summary: str
    items: list  # list[report.ActionItem]
    notes: list[str] = field(default_factory=list)


@dataclass
class AnalysisState:
    dataset: Dataset | None = None
    mode: str = MODE_SAMPLE
    predictions: dict[str, Prediction] = field(default_factory=dict)
    message_status: dict[str, MessageStatus] = field(default_factory=dict)
    prediction_mode: str | None = None  # mode that produced current predictions
    cache: dict[str, dict] = field(default_factory=dict)  # session-only live cache
    corrections: list[Correction] = field(default_factory=list)
    report_version: int = 0
    actions: ActionPlan | None = None
    run_log: list[dict] = field(default_factory=list)
    last_error: str | None = None
    analyzed: bool = False

    # ------------------------------------------------------------ lifecycle

    def load_dataset(self, dataset: Dataset) -> None:
        """Replace the dataset; incompatible analysis state is discarded.

        The live prediction cache is kept: its keys include the message
        digest, model, prompt and schema version, so stale entries never match.
        """
        self.dataset = dataset
        self.predictions = {}
        self.prediction_mode = None
        self.corrections = []
        self.actions = None
        self.run_log = []
        self.last_error = None
        self.analyzed = False
        self.report_version = 0
        self.message_status = {}
        for rec in dataset.feedback.itertuples(index=False):
            self.message_status[rec.message_id] = MessageStatus.NOT_SENT if rec.input_issue else MessageStatus.PENDING

    def clear_predictions(self) -> None:
        self.predictions = {}
        self.prediction_mode = None
        self.actions = None
        self.analyzed = False
        if self.dataset is not None:
            for rec in self.dataset.feedback.itertuples(index=False):
                self.message_status[rec.message_id] = (
                    MessageStatus.NOT_SENT if rec.input_issue else MessageStatus.PENDING
                )

    def bump_version(self) -> None:
        """Any change to accepted facts creates a new report version and
        invalidates action suggestions derived from earlier facts."""
        self.report_version += 1
        self.actions = None

    # -------------------------------------------------------------- derived

    def link_overrides(self) -> dict[str, str]:
        """message_id -> order_line_id from explicit owner link corrections."""
        out: dict[str, str] = {}
        for c in self.corrections:
            if c.kind == "item_link" and c.message_id:
                out[c.message_id] = c.order_line_id
        return out

    def item_overrides(self) -> dict[str, Correction]:
        """order_line_id -> latest item assignment correction (later wins)."""
        out: dict[str, Correction] = {}
        for c in self.corrections:
            if c.kind == "item_assignment":
                out[c.order_line_id] = c
        return out

    def effective_feedback(self) -> pd.DataFrame:
        """Feedback with owner link corrections applied (originals preserved)."""
        assert self.dataset is not None
        fb = self.dataset.feedback.copy()
        fb["original_order_line_id"] = fb["order_line_id"]
        overrides = self.link_overrides()
        if overrides:
            mask = fb["message_id"].isin(overrides)
            fb.loc[mask, "order_line_id"] = fb.loc[mask, "message_id"].map(overrides)
            fb.loc[mask, "link_status"] = "corrected"
        return fb

    def status_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in MessageStatus}
        for s in self.message_status.values():
            counts[s.value] += 1
        counts["needs_review"] = sum(1 for p in self.predictions.values() if p.needs_review)
        return counts


def get_state(session_state) -> AnalysisState:
    """Fetch (or create) this browser session's state from st.session_state."""
    if STATE_KEY not in session_state:
        session_state[STATE_KEY] = AnalysisState()
    return session_state[STATE_KEY]
