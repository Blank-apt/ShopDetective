"""Optional live smoke test. Skipped unless RUN_LIVE_TESTS=1 and GEMINI_API_KEY are set.

A skipped live test is NOT a passed live test.
"""

from __future__ import annotations

import os

import pytest
from conftest import FAST, no_sleep

from shopdetective.complaint_ai import run_classification
from shopdetective.config import load_settings
from shopdetective.data_loader import load_sample
from shopdetective.providers import GeminiProvider
from shopdetective.session import AnalysisState

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_TESTS") != "1" or not os.environ.get("GEMINI_API_KEY"),
        reason="live Gemini smoke test not requested (set RUN_LIVE_TESTS=1 and GEMINI_API_KEY)",
    ),
]


def test_live_gemini_classifies_review_cases():
    settings = load_settings()
    state = AnalysisState(mode="Live AI")
    state.load_dataset(load_sample("review_cases"))
    provider = GeminiProvider(os.environ["GEMINI_API_KEY"], settings.provider.model, settings.provider.timeout_seconds)
    summary = run_classification(state, provider, FAST, sleep=no_sleep)
    assert not summary.aborted, summary.error_code
    assert summary.classified + summary.failed == summary.requested
    assert summary.classified >= 1
    for p in state.predictions.values():
        assert p.source.value == "live_gemini"
        if p.evidence_quote and p.quote_valid:
            text = state.dataset.feedback.set_index("message_id").loc[p.message_id, "message"]
            assert p.evidence_quote.replace("\r\n", "\n") in text.replace("\r\n", "\n")
