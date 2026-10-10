"""Classification orchestration: bounded batches, retries and session cache.

API work happens only when the UI calls ``run_classification`` from an
explicit button action. Completed results are kept if a batch fails.
Logs contain counts, codes and versions only (never keys or customer text).
"""

from __future__ import annotations

import logging
import random
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .config import PROMPT_VERSION, SCHEMA_VERSION, ProviderSettings
from .data_loader import message_digest
from .providers import Provider, ProviderError
from .schemas import Category, Detail, MessageStatus, Prediction, PredictionSource
from .session import AnalysisState
from .validation import utc_now, validate_classification_response

log = logging.getLogger("shopdetective.classify")

ProgressFn = Callable[[int, int, dict], None]


@dataclass
class RunSummary:
    requested: int = 0
    classified: int = 0
    from_cache: int = 0
    failed: int = 0
    skipped_corrected: int = 0
    calls: int = 0
    aborted: bool = False
    error_code: str | None = None
    error_message: str | None = None
    issue_counts: Counter = field(default_factory=Counter)

    def as_log(self) -> dict:
        return {
            "requested": self.requested,
            "classified": self.classified,
            "from_cache": self.from_cache,
            "failed": self.failed,
            "skipped_corrected": self.skipped_corrected,
            "calls": self.calls,
            "aborted": self.aborted,
            "error_code": self.error_code,
            "issues": dict(self.issue_counts),
        }


def cache_key(message_id: str, text: str, model_id: str) -> str:
    return f"{message_digest(message_id, text)}|{model_id}|{PROMPT_VERSION}|{SCHEMA_VERSION}"


def _backoff(attempt: int, settings: ProviderSettings, rand: Callable[[], float]) -> float:
    base = min(settings.backoff_max_seconds, settings.backoff_base_seconds * (2 ** (attempt - 1)))
    return base * (0.5 + rand() / 2)  # jitter in [base/2, base]


def select_messages(state: AnalysisState) -> tuple[list[dict[str, str]], int]:
    """Messages to send: not yet classified, not empty/oversized, and not on
    an item the owner has already resolved."""
    assert state.dataset is not None
    fb = state.effective_feedback()
    resolved_items = set(state.item_overrides())
    wanted = {MessageStatus.PENDING, MessageStatus.FAILED}
    out: list[dict[str, str]] = []
    skipped = 0
    for rec in fb.itertuples(index=False):
        status = state.message_status.get(rec.message_id, MessageStatus.PENDING)
        if status not in wanted:
            continue
        if rec.order_line_id in resolved_items and rec.link_status != "unknown":
            skipped += 1
            continue
        out.append({"message_id": rec.message_id, "text": rec.message})
    return out, skipped


def run_classification(
    state: AnalysisState,
    provider: Provider,
    settings: ProviderSettings,
    progress: ProgressFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> RunSummary:
    summary = RunSummary()
    if state.dataset is None:
        return summary
    if not provider.supports_classification:
        raise ProviderError("manual_mode", "This mode does not classify messages.", False)

    messages, summary.skipped_corrected = select_messages(state)
    summary.requested = len(messages)
    total = len(messages)
    texts = {m["message_id"]: m["text"] for m in messages}
    is_live = provider.source == PredictionSource.LIVE_GEMINI
    prompt_version = getattr(provider, "prompt_version", PROMPT_VERSION)

    # 1) Reuse validated live predictions from this session's cache.
    to_send: list[dict[str, str]] = []
    for m in messages:
        key = cache_key(m["message_id"], m["text"], provider.model_id)
        cached = state.cache.get(key) if is_live else None
        if cached is not None:
            pred = replace(Prediction.from_dict(cached), source=PredictionSource.CACHED_LIVE)
            state.predictions[m["message_id"]] = pred
            state.message_status[m["message_id"]] = MessageStatus.CLASSIFIED
            summary.from_cache += 1
        else:
            to_send.append(m)
    done = summary.from_cache
    if progress:
        progress(done, total, state.status_counts())

    # 2) Bounded batches with bounded retries.
    size = max(1, settings.batch_size)
    for start in range(0, len(to_send), size):
        batch = to_send[start : start + size]
        remaining = {m["message_id"] for m in batch}
        attempt = 0
        while remaining and attempt < settings.max_attempts:
            attempt += 1
            request = [m for m in batch if m["message_id"] in remaining]
            summary.calls += 1
            try:
                raw = provider.classify_batch(request)
            except ProviderError as err:
                summary.issue_counts[f"provider_{err.code}"] += 1
                if not err.retryable:
                    summary.aborted = True
                    summary.error_code = err.code
                    summary.error_message = err.message
                    break
                summary.error_code = err.code
                summary.error_message = err.message
                if attempt < settings.max_attempts:
                    sleep(_backoff(attempt, settings, rand))
                continue
            result = validate_classification_response(
                raw,
                {mid: texts[mid] for mid in remaining},
                provider.source,
                provider.model_id,
                prompt_version,
                SCHEMA_VERSION,
            )
            summary.issue_counts.update(result.issue_counts)
            for mid, pred in result.accepted.items():
                state.predictions[mid] = pred
                state.message_status[mid] = MessageStatus.CLASSIFIED
                summary.classified += 1
                if is_live:
                    state.cache[cache_key(mid, texts[mid], provider.model_id)] = pred.to_dict()
            remaining = result.retry_ids
            if remaining and attempt < settings.max_attempts:
                sleep(_backoff(attempt, settings, rand))
        for mid in remaining:
            state.message_status[mid] = MessageStatus.FAILED
            summary.failed += 1
        done += len(batch)
        if progress:
            progress(done, total, state.status_counts())
        if summary.aborted:
            # Leave later batches pending so the owner can retry after fixing setup.
            break

    if summary.classified or summary.from_cache:
        state.prediction_mode = state.mode
        state.bump_version()
    state.analyzed = True
    state.last_error = summary.error_message if (summary.aborted or summary.failed) else None
    state.run_log.append({"time_utc": utc_now(), "mode": state.mode, "model": provider.model_id, **summary.as_log()})
    log.info("classification run %s", summary.as_log())
    return summary


def unclassifiable_placeholder(message_id: str) -> Prediction:
    """Used by manual mode to show messages awaiting the owner's label."""
    return Prediction(
        message_id=message_id,
        category=Category.UNCLEAR,
        detail=Detail.OTHER,
        evidence_quote=None,
        needs_review=True,
        review_reason="Awaiting owner label (manual review mode).",
        source=PredictionSource.MANUAL_ONLY,
        model_id="none:manual",
        prompt_version="none",
        schema_version=SCHEMA_VERSION,
        quote_valid=False,
        created_at_utc=utc_now(),
    )


def start_manual_review(state: AnalysisState) -> int:
    """Manual mode: mark every sendable message as awaiting the owner's label."""
    assert state.dataset is not None
    n = 0
    for rec in state.dataset.feedback.itertuples(index=False):
        if rec.input_issue:
            continue
        state.predictions[rec.message_id] = unclassifiable_placeholder(rec.message_id)
        state.message_status[rec.message_id] = MessageStatus.CLASSIFIED
        n += 1
    state.prediction_mode = state.mode
    state.analyzed = True
    state.bump_version()
    state.run_log.append({"time_utc": utc_now(), "mode": state.mode, "model": "none:manual", "requested": n})
    return n
