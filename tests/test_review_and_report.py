"""Corrections, invalidation, action validation and exports."""

from __future__ import annotations

import csv
import io
import json

import pytest

from shopdetective.analytics import compute_report
from shopdetective.complaint_ai import start_manual_review
from shopdetective.data_loader import load_sample
from shopdetective.providers import ProviderError, SampleReferenceProvider
from shopdetective.report import (
    ACTION_SOURCE_SYNTHETIC,
    ACTION_SOURCE_TEMPLATE,
    action_plan_markdown,
    build_fact_bundles,
    corrections_csv,
    csv_safe,
    current_actions,
    evidence_csv,
    findings_csv,
    generate_action_plan,
    report_json,
    validate_actions,
)
from shopdetective.review import CorrectionError, apply_item_correction, apply_link_correction, review_queue
from shopdetective.schemas import Filters
from shopdetective.session import AnalysisState


def _jeans_mixed_line(state):
    return next(q.key for q in review_queue(state) if q.kind == "item" and "fit problem" in q.reason)


def test_review_queue_lists_mixed_conflicting_and_unclear(demo_state):
    reasons = " | ".join(q.reason for q in review_queue(demo_state))
    assert "different complaint categories" in reasons
    assert "fit problem and late delivery" in reasons
    assert "No specific reason" in reasons


def test_correction_recalculates_increments_version_and_invalidates_actions(demo_state):
    f = Filters()
    before = compute_report(demo_state, f)
    jeans_before = next(x for x in before.all_findings if x.finding_id == "P002:sizing")
    generate_action_plan(demo_state, before, f, None)
    assert current_actions(demo_state, f) is not None
    v = demo_state.report_version

    olid = _jeans_mixed_line(demo_state)
    mid = compute_report(demo_state).items[olid].message_ids[0]
    c = apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "Customer confirmed fit was the reason")

    assert demo_state.report_version == v + 1 == c.report_version_after
    assert current_actions(demo_state, f) is None  # stale action plan hidden
    after = compute_report(demo_state, f)
    jeans_after = next(x for x in after.all_findings if x.finding_id == "P002:sizing")
    assert jeans_after.associated_refund_minor == jeans_before.associated_refund_minor + 150_000
    assert after.unassigned_refund_minor == before.unassigned_refund_minor - 150_000
    assert after.reconciles
    # Original input and model prediction preserved for audit
    assert demo_state.predictions[mid].category.value == "unclear"
    assert c.before["status"] == "needs_review" and c.reason.startswith("Customer")


def test_scripted_demo_correction_jn0044_exact_numbers(demo_state):
    """The exact correction rehearsed in docs/DEMO_SCRIPT.md."""
    f = Filters()
    before = compute_report(demo_state, f)
    generate_action_plan(demo_state, before, f, None)
    assert demo_state.report_version == 1
    assert (before.refund_minor, before.attributed_refund_minor, before.unassigned_refund_minor) == (
        3_647_500,
        2_467_500,
        1_180_000,
    )
    assert next(x for x in before.all_findings if x.finding_id == "P002:sizing").associated_refund_minor == 450_000
    assert before.items["JN-0044"].message_ids == ["M0037"]

    apply_item_correction(
        demo_state,
        "JN-0044",
        "sizing",
        "too_small",
        ["M0037"],
        "For this synthetic demonstration, choose fit as the primary issue.",
        {"M0037": "Too tight at the waist"},
    )
    after = compute_report(demo_state, f)
    assert demo_state.report_version == 2 and current_actions(demo_state, f) is None
    assert next(x for x in after.all_findings if x.finding_id == "P002:sizing").associated_refund_minor == 600_000
    assert (after.refund_minor, after.attributed_refund_minor, after.unassigned_refund_minor) == (
        3_647_500,
        2_617_500,
        1_030_000,
    )
    assert after.reconciles and demo_state.predictions["M0037"].category.value == "unclear"


def test_filter_change_invalidates_actions_but_keeps_corrections(demo_state):
    generate_action_plan(demo_state, compute_report(demo_state), Filters(), None)
    olid = _jeans_mixed_line(demo_state)
    mid = compute_report(demo_state).items[olid].message_ids[0]
    apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "ok")
    generate_action_plan(demo_state, compute_report(demo_state), Filters(), None)
    assert current_actions(demo_state, Filters(product_ids=("P001",))) is None
    assert len(demo_state.corrections) == 1


def test_dataset_replacement_discards_incompatible_state(demo_state):
    olid = _jeans_mixed_line(demo_state)
    mid = compute_report(demo_state).items[olid].message_ids[0]
    apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "ok")
    demo_state.load_dataset(load_sample("review_cases"))
    assert demo_state.corrections == [] and demo_state.predictions == {} and demo_state.actions is None


def test_correction_validation_rules(demo_state):
    olid = _jeans_mixed_line(demo_state)
    mid = compute_report(demo_state).items[olid].message_ids[0]
    with pytest.raises(CorrectionError):
        apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "")  # reason required
    with pytest.raises(CorrectionError):
        apply_item_correction(demo_state, olid, "sizing", "late_delivery", [mid], "x")  # bad detail
    with pytest.raises(CorrectionError):
        apply_item_correction(demo_state, olid, "sizing", "too_small", [], "x")  # evidence required
    with pytest.raises(CorrectionError):
        apply_item_correction(demo_state, olid, "sizing", "too_small", ["M9999"], "x")  # not linked
    with pytest.raises(CorrectionError):
        apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "x", {mid: "invented words"})
    assert demo_state.corrections == []


def test_review_case_unsupported_quote_corrected_with_real_quote(review_state):
    c = apply_item_correction(
        review_state,
        "RJ-003",
        "wrong_item",
        "wrong_color",
        ["RM05"],
        "Quote checked against the message",
        {"RM05": "I ordered navy but this is clearly black"},
    )
    r = compute_report(review_state)
    f = next(x for x in r.all_findings if x.finding_id == "R01:wrong_item")
    assert f.associated_refund_minor == 4999 and c.supporting_quotes["RM05"].startswith("I ordered navy")


def test_link_correction_requires_existing_exact_id(review_state):
    with pytest.raises(CorrectionError):
        apply_link_correction(review_state, "RM04", "RJ-99", "typo")
    with pytest.raises(CorrectionError):
        apply_link_correction(review_state, "RM02", "RJ-001", "already linked")
    before = compute_report(review_state)
    c = apply_link_correction(review_state, "RM04", "RJ-009", "Matched via support ticket")
    after = compute_report(review_state)
    assert c.before["order_line_id"] == "RJ-099" and c.after["order_line_id"] == "RJ-009"
    assert review_state.dataset.feedback.set_index("message_id").loc["RM04", "order_line_id"] == "RJ-099"
    assert after.items["RJ-009"].category == "other"  # existing validated prediction now counts
    assert after.covered_refunded_lines == before.covered_refunded_lines + 1
    assert after.reconciles


def test_manual_mode_owner_labels_create_findings():
    s = AnalysisState(mode="Manual review")
    s.load_dataset(load_sample("review_cases"))
    start_manual_review(s)
    r = compute_report(s)
    assert r.all_findings == [] and r.attributed_refund_minor == 0
    apply_item_correction(s, "RJ-002", "sizing", "too_small", ["RM02"], "Owner read the message")
    r = compute_report(s)
    assert [f.finding_id for f in r.all_findings] == ["R01:sizing"]
    plan = generate_action_plan(s, r, Filters(), None)
    assert plan.items[0].source == ACTION_SOURCE_TEMPLATE


# ------------------------------------------------------------ actions


def test_action_validation_whitelists_evidence_and_rejects_numbers_and_causal_claims(demo_state):
    r = compute_report(demo_state)
    bundles = build_fact_bundles(demo_state, r)
    b = bundles[0]
    ok_id = b["allowed_evidence_ids"][0]
    other = next(x for x in bundles[1]["allowed_evidence_ids"])

    def act(**kw):
        base = {
            "finding_id": b["finding_id"],
            "product_id": b["product_id"],
            "action_type": "measure_against_size_chart",
            "recommendation": "Measure stock against the size chart and review the fit notes.",
            "evidence_ids": [ok_id],
        }
        base.update(kw)
        return json.dumps({"actions": [base]})

    assert b["finding_id"] in validate_actions(act(), bundles)[0]
    assert validate_actions(act(evidence_ids=[other]), bundles)[0] == {}
    assert validate_actions(act(evidence_ids=["M_UNSEEN"]), bundles)[0] == {}
    assert validate_actions(act(recommendation="Refunds of 9600 rupees came from sizing."), bundles)[0] == {}
    assert validate_actions(act(recommendation="The size chart definitely caused these refunds."), bundles)[0] == {}
    assert validate_actions(act(product_id="P999"), bundles)[0] == {}
    assert validate_actions("garbage", bundles)[0] == {}


class _FailingProvider:
    source = SampleReferenceProvider.source
    model_id = "x"

    def suggest_actions(self, bundles):
        raise ProviderError("timeout", "timed out", True)


def test_action_fallback_templates_are_labelled(demo_state):
    plan = generate_action_plan(demo_state, compute_report(demo_state), Filters(), _FailingProvider())
    assert all(a.source == ACTION_SOURCE_TEMPLATE for a in plan.items)
    assert any("unavailable" in n for n in plan.notes)


def test_sample_actions_are_labelled_synthetic_and_figures_come_from_python(demo_state):
    ds = demo_state.dataset
    r = compute_report(demo_state)
    plan = generate_action_plan(demo_state, r, Filters(), SampleReferenceProvider("demo", ds.feedback_digest))
    top = plan.items[0]
    assert top.source == ACTION_SOURCE_SYNTHETIC
    assert "INR 9,600.00" in top.fact_summary and "12/15 = 80%" in top.fact_summary
    assert not any(ch.isdigit() for ch in top.recommendation)
    assert set(top.evidence_ids) <= set(r.all_findings[0].message_ids)


# ------------------------------------------------------------ exports


def _rows(data: bytes):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def test_exports_include_metadata_and_current_numbers(demo_state, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-SECRET-DO-NOT-LEAK")
    f = Filters(product_ids=("P001",))
    r = compute_report(demo_state, f)
    rows = _rows(findings_csv(demo_state, r, f))
    assert rows[0]["finding_id"] == "P001:sizing" and rows[0]["associated_recorded_refunds"] == "9600.00"
    for col in (
        "report_version",
        "currency",
        "analysis_mode",
        "dataset_digest",
        "filter_scope",
        "model_ids",
        "prompt_version",
        "schema_version",
        "generated_at_utc",
    ):
        assert col in rows[0]
    assert rows[0]["currency"] == "INR" and rows[0]["model_ids"] == "none:synthetic-reference"
    ev = _rows(evidence_csv(demo_state, r, f))
    assert {e["product_id"] for e in ev if e["link_status"] == "linked"} == {"P001"}
    j = json.loads(report_json(demo_state, r, f))
    assert j["overview"]["recorded_refunds_minor"] == 1_600_000 and j["overview"]["reconciles"]
    assert j["metadata"]["filter_scope"]["product_ids"] == ["P001"]
    md = action_plan_markdown(demo_state, r, f).decode()
    assert "INR 9,600.00" in md and "Reviewed category template" in md
    everything = b"".join(
        [
            findings_csv(demo_state, r, f),
            evidence_csv(demo_state, r, f),
            corrections_csv(demo_state, f),
            report_json(demo_state, r, f),
            action_plan_markdown(demo_state, r, f),
        ]
    ).decode("utf-8", "ignore")
    assert "SECRET" not in everything and "/home/" not in everything and "\\Users\\" not in everything


def test_csv_formula_injection_is_neutralised():
    assert csv_safe('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert csv_safe("+1") == "'+1" and csv_safe("@SUM(A1)") == "'@SUM(A1)" and csv_safe("ok") == "ok"


def test_corrections_csv_has_audit_fields(demo_state):
    olid = _jeans_mixed_line(demo_state)
    mid = compute_report(demo_state).items[olid].message_ids[0]
    apply_item_correction(demo_state, olid, "sizing", "too_small", [mid], "=cmd|' /C calc'!A0")
    row = _rows(corrections_csv(demo_state, Filters()))[0]
    assert row["reviewer"] == "local_reviewer" and row["timestamp_utc"].endswith("Z")
    assert row["reason"].startswith("'=")  # neutralised
    assert "category=sizing" in row["after"] and row["supporting_message_ids"] == mid


def test_sessions_are_isolated():
    a, b = AnalysisState(), AnalysisState()
    a.load_dataset(load_sample("demo"))
    a.cache["k"] = {"x": 1}
    assert b.dataset is None and b.cache == {} and b.corrections == []
