"""Evaluation tooling: baseline metrics and honest not_run reporting."""

from __future__ import annotations

import csv
import json
import subprocess
import sys

import pytest
from conftest import ROOT

from shopdetective.evaluation import Scored, keyword_classify, metrics

ALLOWED_STATUSES = {"provisional_synthetic", "human_verified"}


def test_evaluation_set_is_large_separate_and_uses_documented_label_statuses():
    rows = list(csv.DictReader((ROOT / "data/evaluation/labelled_feedback.csv").open(encoding="utf-8")))
    assert len(rows) >= 60
    # Rows become human_verified only after a person reviews them; both statuses are valid.
    assert {r["label_status"] for r in rows} <= ALLOWED_STATUSES
    assert {r["expected_category"] for r in rows} == {
        "sizing",
        "delivery",
        "wrong_item",
        "other",
        "no_complaint",
        "unclear",
    }
    demo_text = (ROOT / "data/demo/feedback.csv").read_text(encoding="utf-8")
    prompt = (ROOT / "shopdetective/prompts.py").read_text(encoding="utf-8")
    for r in rows:
        assert r["text"] not in demo_text and r["text"] not in prompt


def test_metrics_count_abstentions_as_misses():
    rows = [
        Scored("1", "sizing", "sizing", False, True),
        Scored("2", "sizing", "unclear", True, None),
        Scored("3", "delivery", None, False, None),
    ]
    m = metrics(rows)
    for family in ("raw_category", "accepted_decisions"):
        assert m[family]["per_category"]["sizing"]["recall"] == 0.5
        assert m[family]["per_category"]["sizing"]["precision"] == 1.0
    assert m["abstention_or_review_rate"] == 1 / 3 and m["no_valid_prediction"] == 1
    acc = m["accepted_decisions"]
    assert acc["auto_accepted"] == 1 and acc["routed_to_review"] == 2 and acc["n"] == 3


def test_reviewed_match_with_invalid_quote_is_not_an_accepted_success():
    # Reviewer's case: correct returned category, but flagged and quote rejected.
    m = metrics([Scored("1", "sizing", "sizing", True, False)])
    raw, acc = m["raw_category"], m["accepted_decisions"]
    assert raw["accuracy"] == 1.0 and raw["per_category"]["sizing"]["recall"] == 1.0  # clearly raw only
    assert acc["auto_accepted"] == 0 and acc["auto_acceptance_coverage"] == 0.0
    assert acc["review_or_failure_rate"] == 1.0 and acc["quote_rejected"] == 1
    assert acc["correct_auto_accepted_rate"] == 0.0 and acc["accepted_precision"] is None
    assert acc["per_category"]["sizing"]["recall"] == 0.0 and acc["per_category"]["sizing"]["support"] == 1


@pytest.mark.parametrize(
    "row",
    [
        Scored("q", "sizing", "sizing", False, False),  # quote failed even without a review flag
        Scored("r", "sizing", "sizing", True, True),  # needs_review
        Scored("f", "sizing", None, False, None),  # failed validation
    ],
)
def test_not_auto_accepted_cases_stay_in_denominator(row):
    acc = metrics([row, Scored("ok", "delivery", "delivery", False, True)])["accepted_decisions"]
    assert acc["n"] == 2 and acc["auto_accepted"] == 1 and acc["auto_acceptance_coverage"] == 0.5
    assert acc["accepted_precision"] == 1.0 and acc["correct_auto_accepted_rate"] == 0.5
    assert acc["per_category"]["sizing"]["recall"] == 0.0


def test_expected_unclear_is_correct_only_when_routed_to_review():
    rows = [
        Scored("u1", "unclear", "unclear", True, None),
        Scored("u2", "unclear", "sizing", False, True),  # auto-accepted -> incorrect automatic decision
    ]
    acc = metrics(rows)["accepted_decisions"]
    assert acc["expected_unclear"] == 2 and acc["expected_unclear_routed_to_review"] == 1
    assert acc["incorrect_auto_accepted"] == 1 and acc["accepted_precision"] == 0.0
    assert "unclear" not in acc["per_category"]
    assert acc["per_category"]["sizing"]["precision"] == 0.0


def test_keyword_baseline_quotes_are_exact_substrings():
    p = keyword_classify("x", "It was Too Small for me")
    assert p.category == "sizing" and p.evidence_quote in "It was Too Small for me"


def test_cli_without_key_marks_live_not_run(tmp_path):
    env = {"PATH": "", "PYTHONPATH": str(ROOT), "SYSTEMROOT": "", "SD_DISABLE_DOTENV": "1"}
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate.py"), "--out", str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    report = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert report["live_model"]["status"] == "not_run"
    assert report["keyword_baseline"]["status"] == "completed"
    assert "metrics" not in report["live_model"]
    base = report["keyword_baseline"]["metrics"]
    assert {"raw_category", "accepted_decisions"} <= set(base)
    assert (
        report["label_status_counts"]["provisional_synthetic"] + report["label_status_counts"]["human_verified"]
        == report["n_examples"]
    )
    md = (tmp_path / "latest.md").read_text(encoding="utf-8")
    assert "Raw category accuracy" in md and "Auto-accepted" in md


def test_cli_rescore_recomputes_from_stored_predictions_only(tmp_path):
    stored = {
        "live_model": {
            "status": "completed",
            "model": "recorded-model",
            "prompt_version": "classify-v1",
            "schema_version": "1.0",
            "provider_errors": {},
            "predictions": [
                {"example_id": "E001", "predicted": "sizing", "abstained": True, "quote_valid": False},
                {"example_id": "E002", "predicted": "sizing", "abstained": False, "quote_valid": True},
            ],
        }
    }
    src = tmp_path / "stored.json"
    src.write_text(json.dumps(stored), encoding="utf-8")
    env = {"PATH": "", "PYTHONPATH": str(ROOT), "SYSTEMROOT": "", "SD_DISABLE_DOTENV": "1"}
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts/evaluate.py"), "--rescore", str(src), "--out", str(tmp_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    live = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))["live_model"]
    assert live["model"] == "recorded-model" and live["rescored_from"] == str(src)
    acc = live["metrics"]["accepted_decisions"]
    assert acc["n"] == 2 and acc["auto_accepted"] == 1 and acc["quote_rejected"] == 1
