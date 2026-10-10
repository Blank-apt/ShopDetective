from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Offline by default: never pick up a developer key during ordinary tests.
if os.environ.get("RUN_LIVE_TESTS") != "1":
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ["SD_DISABLE_DOTENV"] = "1"  # ignore a local .env key during offline tests

from shopdetective.complaint_ai import run_classification  # noqa: E402
from shopdetective.config import Limits, ProviderSettings  # noqa: E402
from shopdetective.data_loader import (  # noqa: E402
    FEEDBACK_FIELDS,
    ORDER_FIELDS,
    build_dataset,
    load_sample,
    read_csv_bytes,
)
from shopdetective.providers import ProviderError, SampleReferenceProvider  # noqa: E402
from shopdetective.schemas import PredictionSource  # noqa: E402
from shopdetective.session import AnalysisState  # noqa: E402

FAST = ProviderSettings(batch_size=15, max_attempts=3, backoff_base_seconds=0.0, backoff_max_seconds=0.0)


def no_sleep(_seconds: float) -> None:
    return None


def dataset_from_csv(orders_csv: str, feedback_csv: str, currency: str = "INR", limits: Limits | None = None):
    limits = limits or Limits()
    o = read_csv_bytes(orders_csv.encode(), "orders.csv", limits)
    f = read_csv_bytes(feedback_csv.encode(), "feedback.csv", limits)
    return build_dataset(o, f, {k: k for k in ORDER_FIELDS}, {k: k for k in FEEDBACK_FIELDS}, currency, limits)


class FakeProvider:
    """Deterministic provider for tests. ``script`` maps call number -> behaviour.

    A behaviour is a dict {message_id: label dict}, a raw string, or an exception.
    Unscripted calls answer every requested message from ``labels``.
    """

    name = "Fake"
    source = PredictionSource.LIVE_GEMINI
    supports_classification = True

    def __init__(self, labels: dict[str, dict], script: dict | None = None, model_id: str = "fake-model"):
        self.labels = labels
        self.script = script or {}
        self.model_id = model_id
        self.calls: list[list[str]] = []

    def classify_batch(self, messages):
        self.calls.append([m["message_id"] for m in messages])
        behaviour = self.script.get(len(self.calls))
        if isinstance(behaviour, Exception):
            raise behaviour
        if isinstance(behaviour, str):
            return behaviour
        if callable(behaviour):
            return behaviour(messages)
        return json.dumps(
            {"results": [self.labels[m["message_id"]] for m in messages if m["message_id"] in self.labels]}
        )

    def suggest_actions(self, bundles):
        raise ProviderError("unsupported", "not scripted", False)


def label(mid, cat, detail, quote=None, review=False, reason=None):
    return {
        "message_id": mid,
        "category": cat,
        "detail": detail,
        "evidence_quote": quote,
        "needs_review": review,
        "review_reason": reason,
    }


@pytest.fixture
def demo_state() -> AnalysisState:
    state = AnalysisState()
    ds = load_sample("demo")
    state.load_dataset(ds)
    run_classification(state, SampleReferenceProvider("demo", ds.feedback_digest), FAST, sleep=no_sleep)
    return state


@pytest.fixture
def review_state() -> AnalysisState:
    state = AnalysisState()
    ds = load_sample("review_cases")
    state.load_dataset(ds)
    run_classification(state, SampleReferenceProvider("review_cases", ds.feedback_digest), FAST, sleep=no_sleep)
    return state
