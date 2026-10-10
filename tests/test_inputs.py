"""Input contracts: money parsing, CSV reading, mapping and validation."""

from __future__ import annotations

import pytest
from conftest import dataset_from_csv

from shopdetective.config import Limits
from shopdetective.data_loader import (
    FEEDBACK_FIELDS,
    ORDER_FIELDS,
    MoneyError,
    apply_mapping,
    format_minor,
    parse_money_minor,
    read_csv_bytes,
    suggest_mapping,
)

ORDERS_HEADER = "order_line_id,product_id,product_name,date,sale_amount,refund_amount\n"
FB_HEADER = "message_id,order_line_id,message\n"


@pytest.mark.parametrize(
    "raw,expected",
    [("800", 80000), ("800.5", 80050), ("800.50", 80050), ("0.01", 1), ("0", 0), ("  12.30 ", 1230), ("0.10", 10)],
)
def test_money_parses_exactly_to_minor_units(raw, expected):
    assert parse_money_minor(raw) == expected


def test_money_avoids_binary_float_error():
    # 0.1 + 0.2 is not 0.3 in binary floats; minor units are exact.
    assert parse_money_minor("0.10") + parse_money_minor("0.20") == parse_money_minor("0.30")


@pytest.mark.parametrize(
    "raw", ["NaN", "Infinity", "-inf", "1e3", "-5.00", "12.345", "1,200.00", "₹800", "", "abc", "800.0.0"]
)
def test_money_rejects_invalid_values(raw):
    with pytest.raises(MoneyError):
        parse_money_minor(raw)


def test_format_minor():
    assert format_minor(1_600_000, "INR") == "INR 16,000.00"
    assert format_minor(4999, "USD") == "USD 49.99"


def test_valid_dataset_keeps_leading_zero_ids_and_zero_refund_lines():
    r = dataset_from_csv(
        ORDERS_HEADER + "0007,0042,Gloves,2026-08-01,15.00,0\n0008,0042,Gloves,2026-08-02,15.00,15.00\n",
        FB_HEADER + "007,0008,Too small\n",
    )
    assert r.ok
    assert list(r.dataset.orders["order_line_id"]) == ["0007", "0008"]
    assert set(r.dataset.orders["product_id"]) == {"0042"}
    assert r.dataset.feedback.iloc[0]["message_id"] == "007"
    assert r.dataset.feedback.iloc[0]["link_status"] == "linked"
    assert list(r.dataset.orders["refund_minor"]) == [0, 1500]


def _codes(result):
    return {i.code for i in result.issues if i.level == "error"}


def test_missing_columns_are_reported():
    o = read_csv_bytes(b"order_line_id,product_id,date\nA,P,2026-01-01\n", "orders.csv", Limits())
    mapping = suggest_mapping(list(o.frame.columns), ORDER_FIELDS)
    canon, issues = apply_mapping(o.frame, mapping, ORDER_FIELDS, "orders.csv")
    assert canon is None
    assert {i.field for i in issues} == {"product_name", "sale_amount", "refund_amount"}


def test_conflicting_duplicate_ids_stop_analysis():
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,10.00,0\nA1,P,X,2026-08-01,12.00,0\n",
        FB_HEADER + "M1,A1,ok\n",
    )
    assert not r.ok and "conflicting_duplicate_id" in _codes(r)


def test_identical_duplicate_rows_warn_and_count_once():
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,10.00,0\nA1,P,X,2026-08-01,10.00,0\n",
        FB_HEADER + "M1,A1,ok\nM1,A1,ok\n",
    )
    assert r.ok
    assert len(r.dataset.orders) == 1 and len(r.dataset.feedback) == 1
    assert sum(1 for i in r.issues if i.code == "duplicate_row") == 2


@pytest.mark.parametrize("bad_date", ["2026-02-30", "01/08/2026", "2026-8-1", ""])
def test_invalid_dates_are_errors(bad_date):
    r = dataset_from_csv(ORDERS_HEADER + f"A1,P,X,{bad_date},10.00,0\n", FB_HEADER + "M1,A1,ok\n")
    assert "invalid_date" in _codes(r)


def test_refund_exceeding_sale_and_bad_amounts_stop_analysis():
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,10.00,10.01\nA2,P,X,2026-08-01,NaN,0\nA3,P,X,2026-08-01,5.999,0\n",
        FB_HEADER + "M1,A1,ok\n",
    )
    assert not r.ok
    assert {"refund_exceeds_sale", "invalid_amount"} <= _codes(r)
    lines = {i.row for i in r.issues if i.level == "error"}
    assert lines == {2, 3, 4}


def test_oversized_single_amount_is_a_validation_error_not_a_crash():
    huge = "100000000000000000000"  # reproduced OverflowError during int64 conversion
    r = dataset_from_csv(
        ORDERS_HEADER + f"A1,P,X,2026-08-01,{huge},{huge}\nA2,P,X,2026-08-01,10.00,0\n",
        FB_HEADER + "M1,A1,ok\n",
    )
    assert not r.ok
    errors = [i for i in r.issues if i.level == "error"]
    assert {(i.code, i.row, i.field) for i in errors} == {
        ("amount_too_large", 2, "sale_amount"),
        ("amount_too_large", 2, "refund_amount"),
    }
    assert "1,000,000,000,000.00" in errors[0].message


def test_amounts_summing_past_int64_are_rejected_before_storage():
    # Each 5e16 (= 5e18 minor units) fit int64 alone; their pandas sum overflowed.
    big = "50000000000000000"
    r = dataset_from_csv(
        ORDERS_HEADER + f"A1,P,X,2026-08-01,{big},{big}\nA2,P,X,2026-08-01,{big},{big}\n",
        FB_HEADER + "M1,A1,ok\n",
    )
    assert not r.ok and _codes(r) == {"amount_too_large"}


def test_aggregate_bound_is_checked_with_exact_integers():
    limits = Limits(max_line_amount_minor=1_000_00, max_total_amount_minor=1_500_00)
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,1000.00,1000.00\nA2,P,X,2026-08-01,500.01,0\n",
        FB_HEADER + "M1,A1,ok\n",
        limits=limits,
    )
    assert not r.ok and _codes(r) == {"total_too_large"}
    assert "1,500.01" in r.issues[-1].message
    # Exactly at both bounds is accepted, stored exactly and reconciles.
    ok = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,1000.00,1000.00\nA2,P,X,2026-08-01,500.00,250.50\n",
        FB_HEADER + "M1,A1,ok\n",
        limits=limits,
    )
    assert ok.ok
    assert list(ok.dataset.orders["refund_minor"]) == [1_000_00, 250_50]


def test_default_bounds_accept_large_realistic_totals():
    line = "999999999999.99"  # just under the per-line bound
    r = dataset_from_csv(
        ORDERS_HEADER + f"A1,P,X,2026-08-01,{line},{line}\nA2,P,X,2026-08-01,{line},0.01\n",
        FB_HEADER + "M1,A1,ok\n",
    )
    assert r.ok
    from shopdetective.analytics import compute_report
    from shopdetective.session import AnalysisState

    state = AnalysisState()
    state.load_dataset(r.dataset)
    rep = compute_report(state)
    assert rep.refund_minor == 99_999_999_999_999 + 1 and rep.reconciles
    assert rep.sales_minor == 2 * 99_999_999_999_999


def test_limits_reject_unsafe_bounds():
    with pytest.raises(ValueError):
        Limits(max_total_amount_minor=2**63)


def test_unknown_link_empty_and_long_messages_stay_reviewable():
    limits = Limits(max_message_chars=20)
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,10.00,0\n",
        FB_HEADER + 'M1,ZZZ,too small\nM2,A1,"  "\nM3,A1,' + "x" * 21 + "\n",
        limits=limits,
    )
    assert r.ok
    fb = r.dataset.feedback.set_index("message_id")
    assert fb.loc["M1", "link_status"] == "unknown"
    assert fb.loc["M2", "input_issue"] == "empty_message"
    assert fb.loc["M3", "input_issue"] == "message_too_long"


def test_empty_file_oversize_encoding_and_delimiter_messages():
    limits = Limits(max_file_bytes=50)
    assert read_csv_bytes(b"", "o.csv", limits).issues[0].code == "empty_file"
    assert read_csv_bytes(b"a,b\n", "o.csv", limits).issues[0].code == "empty_file"
    assert read_csv_bytes(b"x" * 51, "o.csv", limits).issues[0].code == "file_too_large"
    assert read_csv_bytes("a,b\n\xe9,1\n".encode("latin-1"), "o.csv", limits).issues[0].code == "encoding"
    assert read_csv_bytes(b"a;b;c\n1;2;3\n", "o.csv", Limits()).issues[0].code == "delimiter"


def test_utf8_bom_and_quoted_commas():
    data = ("﻿" + FB_HEADER + 'M1,A1,"Too small, sadly"\n').encode()
    t = read_csv_bytes(data, "feedback.csv", Limits())
    assert t.ok and list(t.frame.columns) == ["message_id", "order_line_id", "message"]
    assert t.frame.iloc[0]["message"] == "Too small, sadly"


def test_row_limits():
    r = dataset_from_csv(
        ORDERS_HEADER + "A1,P,X,2026-08-01,10.00,0\nA2,P,X,2026-08-01,10.00,0\n",
        FB_HEADER + "M1,A1,ok\n",
        limits=Limits(max_order_lines=1),
    )
    assert "too_many_rows" in _codes(r)


def test_column_mapping_suggests_but_requires_explicit_fields():
    cols = ["Line ID", "SKU", "Product", "Order Date", "Price", "Refunded"]
    s = suggest_mapping(cols, ORDER_FIELDS)
    assert s == {
        "order_line_id": "Line ID",
        "product_id": "SKU",
        "product_name": "Product",
        "date": "Order Date",
        "sale_amount": "Price",
        "refund_amount": "Refunded",
    }
    fb = suggest_mapping(["Ticket ID", "Order Line", "Body"], FEEDBACK_FIELDS)
    assert fb == {"message_id": "Ticket ID", "order_line_id": "Order Line", "message": "Body"}


def test_custom_mapped_columns_produce_canonical_data():
    raw = read_csv_bytes(
        b"Line ID,SKU,Product,Order Date,Price,Refunded\nL1,S1,Tee,2026-08-01,10.00,10.00\n", "o.csv", Limits()
    )
    mapping = suggest_mapping(list(raw.frame.columns), ORDER_FIELDS)
    canon, issues = apply_mapping(raw.frame, mapping, ORDER_FIELDS, "o.csv")
    assert not issues and list(canon.columns)[1:] == list(ORDER_FIELDS)


def test_mapping_same_column_twice_is_rejected():
    raw = read_csv_bytes(b"id,msg\n1,hi\n", "f.csv", Limits())
    _, issues = apply_mapping(
        raw.frame, {"message_id": "id", "order_line_id": "id", "message": "msg"}, FEEDBACK_FIELDS, "f.csv"
    )
    assert any(i.code == "mapping_reused" for i in issues)


def test_product_name_conflict_requires_confirmation_and_ids_never_merge():
    orders = (
        ORDERS_HEADER + "A1,P1,Hoodie,2026-08-01,10.00,0\nA2,P1,Hoody,2026-08-01,10.00,0\nA3,P2,Hoodie,2026-08-01,1,0\n"
    )
    r = dataset_from_csv(orders, FB_HEADER + "M1,A1,ok\n")
    assert r.dataset is None and r.name_conflicts == {"P1": ["Hoodie", "Hoody"]}
    from conftest import FEEDBACK_FIELDS as FF
    from conftest import ORDER_FIELDS as OF
    from conftest import build_dataset

    o = read_csv_bytes(orders.encode(), "orders.csv", Limits())
    f = read_csv_bytes((FB_HEADER + "M1,A1,ok\n").encode(), "feedback.csv", Limits())
    ok = build_dataset(o, f, {k: k for k in OF}, {k: k for k in FF}, "INR", Limits(), confirmed_names={"P1": "Hoodie"})
    assert ok.ok
    assert ok.dataset.display_names == {"P1": "Hoodie", "P2": "Hoodie"}  # two IDs stay two products
