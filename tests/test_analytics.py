"""Business outcomes: exact hoodie fixture, deduplication and reconciliation."""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from conftest import FAST, FakeProvider, dataset_from_csv, label, no_sleep

from shopdetective.analytics import NEEDS_REVIEW, compute_report, format_ratio, ratio
from shopdetective.complaint_ai import run_classification
from shopdetective.schemas import Filters
from shopdetective.session import AnalysisState

HOODIE = Filters(product_ids=("P001",))


def test_hoodie_fixture_exact_assertions(demo_state):
    r = compute_report(demo_state, HOODIE)
    hoodie = demo_state.dataset.orders[demo_state.dataset.orders["product_id"] == "P001"]
    assert r.scope_lines == 100
    assert set(hoodie["sale_minor"]) == {80000}
    assert int((hoodie["refund_minor"] == hoodie["sale_minor"]).sum()) == 20
    assert r.refund_minor == 1_600_000
    assert r.refunded_lines == 20
    assert r.covered_refunded_lines == 15
    f = {x.category: x for x in r.all_findings}
    assert f["sizing"].refunded_lines == 12 and f["sizing"].associated_refund_minor == 960_000
    assert f["delivery"].refunded_lines == 3 and f["delivery"].associated_refund_minor == 240_000
    assert r.unassigned_refund_minor == 400_000
    assert format_ratio(r.refunded_lines, r.scope_lines) == "20/100 = 20%"
    assert format_ratio(r.covered_refunded_lines, r.refunded_lines) == "15/20 = 75%"
    assert format_ratio(f["sizing"].refunded_lines, f["sizing"].covered_refunded_lines_product) == "12/15 = 80%"
    assert r.reconciles


def test_hoodie_sizing_is_top_finding_store_wide(demo_state):
    top = compute_report(demo_state).findings[0]
    assert (top.product_id, top.category, top.associated_refund_minor) == ("P001", "sizing", 960_000)


def test_shipped_demo_has_duplicate_messages_that_do_not_change_totals(demo_state):
    fb = demo_state.dataset.feedback
    hoodie_lines = set(demo_state.dataset.orders.query("product_id == 'P001'")["order_line_id"])
    counts = fb[fb["order_line_id"].isin(hoodie_lines)]["order_line_id"].value_counts()
    assert (counts > 1).sum() >= 3  # several hoodie lines have repeated feedback
    r = compute_report(demo_state, HOODIE)
    sizing = next(f for f in r.all_findings if f.category == "sizing")
    assert sizing.supporting_messages > sizing.affected_lines  # more messages than lines
    assert sizing.associated_refund_minor == 960_000


def _state(orders_rows, fb_rows, labels):
    r = dataset_from_csv(
        "order_line_id,product_id,product_name,date,sale_amount,refund_amount\n" + "".join(orders_rows),
        "message_id,order_line_id,message\n" + "".join(fb_rows),
    )
    assert r.ok, r.issues
    s = AnalysisState(mode="Live AI")
    s.load_dataset(r.dataset)
    run_classification(s, FakeProvider(labels), FAST, sleep=no_sleep)
    return s


def test_partial_refunds_zero_refund_complaints_and_duplicates():
    s = _state(
        [
            "A1,P,Tee,2026-08-01,450.00,225.00\n",  # partial refund, sizing x2 messages
            "A2,P,Tee,2026-08-02,450.00,0.00\n",  # zero-refund complaint
            "A3,P,Tee,2026-08-03,450.00,450.00\n",  # refund, no feedback
        ],
        ["M1,A1,too small\n", "M2,A1,still too small\n", "M3,A2,too small too\n"],
        {
            "M1": label("M1", "sizing", "too_small", "too small"),
            "M2": label("M2", "sizing", "too_small", "too small"),
            "M3": label("M3", "sizing", "too_small", "too small"),
        },
    )
    r = compute_report(s)
    (f,) = r.all_findings
    assert f.associated_refund_minor == 22_500
    assert (f.refunded_lines, f.affected_lines, f.zero_refund_lines, f.supporting_messages) == (1, 2, 1, 3)
    assert r.unassigned_refund_minor == 45_000 and r.reconciles
    assert r.unassigned_breakdown == {"no_feedback": {"lines": 1, "refund_minor": 45_000}}


def test_conflicting_categories_and_positive_mix_need_review_not_double_allocation():
    s = _state(
        ["A1,P,X,2026-08-01,100.00,100.00\n", "A2,P,X,2026-08-01,100.00,100.00\n"],
        ["M1,A1,too tight\n", "M2,A1,arrived late\n", "M3,A2,love it\n", "M4,A2,too tight\n"],
        {
            "M1": label("M1", "sizing", "too_small", "too tight"),
            "M2": label("M2", "delivery", "late_delivery", "arrived late"),
            "M3": label("M3", "no_complaint", "none"),
            "M4": label("M4", "sizing", "too_small", "too tight"),
        },
    )
    r = compute_report(s)
    assert r.all_findings == []
    assert r.items["A1"].status == NEEDS_REVIEW and r.items["A2"].status == NEEDS_REVIEW
    assert r.attributed_refund_minor == 0 and r.unassigned_refund_minor == 20_000


def test_explicit_human_resolution_assigns_once():
    from shopdetective.review import apply_item_correction

    s = _state(
        ["A1,P,X,2026-08-01,100.00,100.00\n"],
        ["M1,A1,too tight\n", "M2,A1,arrived late\n"],
        {
            "M1": label("M1", "sizing", "too_small", "too tight"),
            "M2": label("M2", "delivery", "late_delivery", "arrived late"),
        },
    )
    apply_item_correction(s, "A1", "sizing", "too_small", ["M1"], "Customer email confirms fit")
    r = compute_report(s)
    (f,) = r.all_findings
    assert (f.category, f.associated_refund_minor, f.review_status) == ("sizing", 10_000, "all owner reviewed")
    assert r.reconciles


def test_unknown_links_stay_outside_financial_issue_totals():
    s = _state(
        ["A1,P,X,2026-08-01,100.00,100.00\n"],
        ["M1,NOPE,too tight\n"],
        {"M1": label("M1", "sizing", "too_small", "too tight")},
    )
    r = compute_report(s)
    assert r.all_findings == [] and r.covered_refunded_lines == 0 and r.unassigned_refund_minor == 10_000


def test_ranking_tie_breaks_are_deterministic():
    s = _state(
        [
            "A1,P2,B,2026-08-01,100.00,100.00\n",
            "A2,P1,A,2026-08-01,50.00,50.00\n",
            "A3,P1,A,2026-08-01,50.00,50.00\n",
            "A4,P1,A,2026-08-01,100.00,100.00\n",
        ],
        ["M1,A1,small\n", "M2,A2,late\n", "M3,A3,late\n", "M4,A4,small\n"],
        {
            "M1": label("M1", "sizing", "too_small", "small"),
            "M2": label("M2", "delivery", "late_delivery", "late"),
            "M3": label("M3", "delivery", "late_delivery", "late"),
            "M4": label("M4", "sizing", "too_small", "small"),
        },
    )
    ids = [f.finding_id for f in compute_report(s).all_findings]
    # equal amounts: more refunded lines first, then product/category
    assert ids == ["P1:delivery", "P1:sizing", "P2:sizing"]


def test_divide_by_zero_shows_unavailable():
    assert ratio(0, 0) is None
    assert format_ratio(0, 0) == "n/a (no lines)"


@pytest.mark.parametrize(
    "products,dates",
    list(
        itertools.product(
            [None, ("P001",), ("P002", "P003"), ("P005",)],
            [(None, None), (date(2026, 7, 1), date(2026, 7, 31)), (date(2026, 8, 15), None)],
        )
    ),
)
def test_reconciliation_holds_for_every_filter_scope(demo_state, products, dates):
    r = compute_report(demo_state, Filters(product_ids=products, date_from=dates[0], date_to=dates[1]))
    assert r.attributed_refund_minor + r.unassigned_refund_minor == r.refund_minor
    assert sum(f.associated_refund_minor for f in r.all_findings) == r.attributed_refund_minor
    orders = demo_state.dataset.orders
    scoped = orders[orders["order_line_id"].isin(r.scope_order_ids)]
    assert r.refund_minor == int(scoped["refund_minor"].sum())


def test_category_display_filter_does_not_change_totals(demo_state):
    full = compute_report(demo_state)
    only = compute_report(demo_state, Filters(categories=("delivery",)))
    assert {f.category for f in only.findings} == {"delivery"}
    assert only.refund_minor == full.refund_minor and only.unassigned_refund_minor == full.unassigned_refund_minor


def test_colliding_product_names_get_distinct_chart_labels():
    from shopdetective.analytics import product_labels

    labels = product_labels({"P1": "Rain Hat", "P2": "Rain Hat", "P3": "rain hat ", "P4": "Scarf"})
    assert labels == {"P1": "Rain Hat (P1)", "P2": "Rain Hat (P2)", "P3": "rain hat  (P3)", "P4": "Scarf"}
    assert len(set(labels.values())) == 4


def test_category_display_filter_does_not_change_money_or_action_scope(demo_state):
    from shopdetective.report import scope_key

    full = compute_report(demo_state)
    narrowed = compute_report(demo_state, Filters(categories=("delivery",)))
    assert {f.category for f in narrowed.findings} == {"delivery"}
    assert narrowed.all_findings == full.all_findings
    assert (
        narrowed.refund_minor == full.refund_minor and narrowed.attributed_refund_minor == full.attributed_refund_minor
    )
    assert scope_key(Filters(categories=("delivery",))) == scope_key(Filters())
    assert scope_key(Filters(product_ids=("P001",))) != scope_key(Filters())
