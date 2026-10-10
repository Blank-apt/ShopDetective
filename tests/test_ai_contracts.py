"""Evidence gate, provider retries, cache and logging behaviour (no network)."""

from __future__ import annotations

import json
import logging

import pytest
from conftest import FAST, FakeProvider, dataset_from_csv, label, no_sleep

from shopdetective.complaint_ai import cache_key, run_classification
from shopdetective.config import ProviderSettings
from shopdetective.data_loader import load_sample
from shopdetective.providers import ProviderError, SampleReferenceProvider, _classify_sdk_error
from shopdetective.review import apply_item_correction
from shopdetective.schemas import MessageStatus, PredictionSource
from shopdetective.session import AnalysisState
from shopdetective.validation import quote_is_supported, validate_classification_response

REQ = {"M1": "I could barely get it over my head.\r\nShame.", "M2": "Great hoodie!"}


def _validate(payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return validate_classification_response(raw, REQ, PredictionSource.LIVE_GEMINI, "m", "p", "s")


def test_exact_quote_rules():
    text = "I could barely get it over my head.\r\nShame."
    assert quote_is_supported("barely get it over my head", text)
    assert quote_is_supported("head.\nShame", text)  # documented line-ending normalisation only
    assert not quote_is_supported("barely got it over my head", text)  # no fuzzy matching
    assert not quote_is_supported("BARELY get it", text)
    assert not quote_is_supported("   ", text)
    assert not quote_is_supported(None, text)


def test_valid_batch_is_accepted():
    out = _validate(
        {
            "results": [
                label("M1", "sizing", "too_small", "barely get it over my head"),
                label("M2", "no_complaint", "none"),
            ]
        }
    )
    assert set(out.accepted) == {"M1", "M2"} and not out.retry_ids
    assert out.accepted["M1"].quote_valid and not out.accepted["M1"].needs_review


def test_invented_quote_goes_to_review():
    out = _validate(
        {"results": [label("M1", "sizing", "too_small", "it was far too small"), label("M2", "no_complaint", "none")]}
    )
    p = out.accepted["M1"]
    assert p.needs_review and not p.quote_valid and "unsupported_quote" in p.validation_notes


def test_missing_quote_for_complaint_goes_to_review():
    out = _validate({"results": [label("M1", "sizing", "too_small", None), label("M2", "no_complaint", "none")]})
    assert out.accepted["M1"].needs_review


def test_invalid_category_detail_combination_is_rejected_and_retried():
    out = _validate(
        {"results": [label("M1", "sizing", "late_delivery", "barely"), label("M2", "no_complaint", "none")]}
    )
    assert "M1" not in out.accepted and out.retry_ids == {"M1"} and "M2" in out.accepted


def test_unknown_category_is_rejected():
    out = _validate({"results": [label("M1", "price", "other", "barely"), label("M2", "no_complaint", "none")]})
    assert out.retry_ids == {"M1"}


def test_missing_duplicate_and_unknown_ids_do_not_contaminate():
    out = _validate(
        {
            "results": [
                label("M2", "no_complaint", "none"),
                label("M2", "sizing", "too_small", "Great"),
                label("M9", "sizing", "too_small", "x"),
            ]
        }
    )
    assert out.accepted == {}
    assert out.retry_ids == {"M1", "M2"}
    assert out.issue_counts["unknown_id"] == 1 and out.issue_counts["missing_id"] == 1


@pytest.mark.parametrize("bad_id", [[], ["M1"], {}, {"id": "M1"}, None, 7])
def test_non_string_message_ids_are_rejected_without_crashing(bad_id):
    bad = label("M1", "sizing", "too_small", "barely get it over my head")
    bad["message_id"] = bad_id
    out = _validate({"results": [bad, label("M2", "no_complaint", "none")]})
    assert set(out.accepted) == {"M2"}  # the valid item in the same batch survives
    assert out.retry_ids == {"M1"}  # the unresolved requested ID stays retryable
    assert out.issue_counts["malformed_id"] == 1


def test_list_and_dict_ids_mixed_with_valid_result():
    out = _validate(
        {
            "results": [
                {"message_id": [], "category": "sizing"},
                {"message_id": {}, "category": "delivery"},
                label("M1", "sizing", "too_small", "barely get it over my head"),
            ]
        }
    )
    assert set(out.accepted) == {"M1"} and out.accepted["M1"].quote_valid
    assert out.retry_ids == {"M2"} and out.issue_counts["malformed_id"] == 2


def test_malformed_json_retries_everything():
    assert _validate("not json").retry_ids == {"M1", "M2"}
    assert _validate({"items": []}).retry_ids == {"M1", "M2"}


def test_unclear_always_needs_review():
    out = _validate({"results": [label("M1", "unclear", "mixed"), label("M2", "no_complaint", "none")]})
    assert out.accepted["M1"].needs_review


# ------------------------------------------------------------- run behaviour


def _small_state():
    r = dataset_from_csv(
        "order_line_id,product_id,product_name,date,sale_amount,refund_amount\n"
        + "".join(f"A{i},P,X,2026-08-01,10.00,10.00\n" for i in range(1, 6)),
        "message_id,order_line_id,message\n" + "".join(f"M{i},A{i},too small {i}\n" for i in range(1, 6)),
    )
    s = AnalysisState(mode="Live AI")
    s.load_dataset(r.dataset)
    labels = {f"M{i}": label(f"M{i}", "sizing", "too_small", f"too small {i}") for i in range(1, 6)}
    return s, labels


def test_transient_errors_are_retried_with_bounded_attempts():
    state, labels = _small_state()
    sleeps = []
    p = FakeProvider(labels, {1: ProviderError("rate_limited", "429", True), 2: ProviderError("timeout", "t", True)})
    settings = ProviderSettings(batch_size=10, max_attempts=3, backoff_base_seconds=1, backoff_max_seconds=8)
    s = run_classification(state, p, settings, sleep=sleeps.append, rand=lambda: 1.0)
    assert len(p.calls) == 3 and s.classified == 5
    assert sleeps == [1.0, 2.0]  # exponential backoff with jitter factor 1.0


def test_retries_stop_after_max_attempts_and_results_are_kept():
    state, labels = _small_state()
    p = FakeProvider(labels, {i: ProviderError("server_error", "500", True) for i in range(1, 10)})
    s = run_classification(state, p, FAST, sleep=no_sleep)
    assert len(p.calls) == 3 and s.failed == 5
    assert all(v == MessageStatus.FAILED for v in state.message_status.values())
    # Retry later succeeds for the failed ones only
    p2 = FakeProvider(labels)
    run_classification(state, p2, FAST, sleep=no_sleep)
    assert len(p2.calls) == 1 and all(v == MessageStatus.CLASSIFIED for v in state.message_status.values())


def test_invalid_credentials_are_not_retried_and_completed_batches_survive():
    state, labels = _small_state()
    p = FakeProvider(labels, {2: ProviderError("auth", "bad key", False)})
    settings = ProviderSettings(batch_size=2, max_attempts=3, backoff_base_seconds=0, backoff_max_seconds=0)
    s = run_classification(state, p, settings, sleep=no_sleep)
    assert s.aborted and s.error_code == "auth"
    assert len(p.calls) == 2  # no retry of the auth failure, no further batches
    assert s.classified == 2 and len(state.predictions) == 2
    assert state.last_error == "bad key"


def test_missing_ids_are_rerequested_within_bound():
    state, labels = _small_state()
    partial = json.dumps({"results": [labels["M1"], labels["M2"]]})
    p = FakeProvider(labels, {1: partial})
    run_classification(state, p, ProviderSettings(batch_size=10, backoff_base_seconds=0), sleep=no_sleep)
    assert p.calls[1] == ["M3", "M4", "M5"]
    assert len(state.predictions) == 5


def test_session_cache_reuses_and_invalidates_on_model_or_prompt_change(monkeypatch):
    state, labels = _small_state()
    run_classification(state, FakeProvider(labels), FAST, sleep=no_sleep)
    # Same dataset reloaded: cached live results, no calls
    state.load_dataset(state.dataset)
    p = FakeProvider(labels)
    s = run_classification(state, p, FAST, sleep=no_sleep)
    assert p.calls == [] and s.from_cache == 5
    assert all(pr.source == PredictionSource.CACHED_LIVE for pr in state.predictions.values())
    # Different model: cache miss
    state.load_dataset(state.dataset)
    p2 = FakeProvider(labels, model_id="other-model")
    run_classification(state, p2, FAST, sleep=no_sleep)
    assert len(p2.calls) == 1
    # Prompt version change: key changes
    k1 = cache_key("M1", "too small 1", "fake-model")
    monkeypatch.setattr("shopdetective.complaint_ai.PROMPT_VERSION", "classify-v2")
    assert cache_key("M1", "too small 1", "fake-model") != k1
    # Edited message text: key changes
    assert cache_key("M1", "too small 1!", "fake-model") != k1


def test_corrected_items_are_not_resent_and_not_overwritten():
    state, labels = _small_state()
    p = FakeProvider(labels, {1: ProviderError("server_error", "x", True)}, model_id="m")
    run_classification(state, p, ProviderSettings(batch_size=10, max_attempts=1), sleep=no_sleep)
    apply_item_correction(state, "A1", "delivery", "late_delivery", ["M1"], "Checked tracking", {"M1": "too small"})
    p2 = FakeProvider(labels)
    run_classification(state, p2, FAST, sleep=no_sleep)
    assert "M1" not in p2.calls[0]
    from shopdetective.analytics import resolve_items

    assert resolve_items(state)["A1"].category == "delivery"


def test_logs_and_errors_contain_no_secrets_or_customer_text(caplog):
    state, labels = _small_state()
    with caplog.at_level(logging.DEBUG):
        run_classification(state, FakeProvider(labels), FAST, sleep=no_sleep)
    assert "too small" not in caplog.text

    from shopdetective.providers import GeminiProvider

    g = GeminiProvider("AIza-SECRET-KEY-123", "gemini-x")
    assert "SECRET" not in repr(g)


def test_sdk_errors_map_to_actionable_codes():
    from google.genai import errors

    def api(code, status="X"):
        return errors.ClientError(code, {"error": {"code": code, "status": status, "message": "m"}})

    assert _classify_sdk_error(api(401)).code == "auth"
    assert not _classify_sdk_error(api(403)).retryable
    assert _classify_sdk_error(api(404)).code == "model_not_found"
    assert _classify_sdk_error(api(429)).retryable
    assert _classify_sdk_error(errors.ServerError(503, {"error": {"code": 503}})).retryable
    assert _classify_sdk_error(TimeoutError()).retryable


def test_sample_reference_refuses_other_data():
    ds = load_sample("demo")
    with pytest.raises(ProviderError) as e:
        SampleReferenceProvider("demo", "0" * 64)
    assert e.value.code == "digest_mismatch"
    assert SampleReferenceProvider("demo", ds.feedback_digest).source == PredictionSource.SYNTHETIC_REFERENCE


def test_fixture_negation_paraphrase_and_injection_cases(review_state):
    """Fixture behaviour (synthetic reference labels), NOT a measure of model quality."""
    p = review_state.predictions
    assert p["RM08"].category.value == "no_complaint"  # "delivery wasn't late"
    assert p["RM09"].category.value == "sizing" and p["RM09"].quote_valid  # injection text is data
    assert p["RM05"].needs_review and not p["RM05"].quote_valid  # deliberately unsupported quote
    assert "RM07" not in p and review_state.message_status["RM07"] == MessageStatus.NOT_SENT
