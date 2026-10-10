"""End-to-end UI interactions with Streamlit's AppTest (no browser, no key)."""

from __future__ import annotations

import pytest
from conftest import ROOT
from streamlit.testing.v1 import AppTest

APP = str(ROOT / "app.py")


@pytest.fixture
def at(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(ROOT)
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    assert not app.exception
    return app


def _state(app):
    return app.session_state["sd_state"]


def test_startup_without_key(at):
    assert at.title[0].value == "ShopDetective"
    assert at.button(key="load_demo").label == "Load example store"
    assert at.radio(key="nav").options == ["Upload & Analyze", "Findings", "Evidence & Review", "Action Plan"]


def test_live_mode_without_key_shows_setup_and_blocks_analyze(at):
    at.button(key="load_demo").click().run()
    at.sidebar.radio(key="mode_choice").set_value("Live AI").run()
    assert not at.exception
    assert any("GEMINI_API_KEY" in w.value for w in at.warning)
    assert at.button(key="analyze").disabled


def test_sample_to_findings_evidence_correction_and_exports(at):
    at.button(key="load_demo").click().run()
    at.button(key="analyze").click().run()
    assert not at.exception
    state = _state(at)
    assert state.report_version == 1 and state.prediction_mode == "Sample demonstration"

    at.radio(key="nav").set_value("Findings").run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Reconciliation"] == "✓ balanced"
    assert metrics["Feedback coverage"] == "33/43 = 77%"

    # Hoodie-only scope reproduces the fixture numbers in the UI
    at.sidebar.multiselect(key="f_products").set_value(["P001"]).run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Recorded refunds"] == "INR 16,000.00"
    assert metrics["Refunded purchase-line rate"] == "20/100 = 20%"
    assert metrics["Feedback coverage"] == "15/20 = 75%"
    assert metrics["Unassigned refunds"] == "INR 4,000.00"
    at.sidebar.multiselect(key="f_products").set_value([]).run()

    # Finding -> evidence navigation
    at.button(key="view_evidence").click().run()
    assert at.radio(key="nav").value == "Evidence & Review"
    assert at.selectbox(key="evidence_finding").value == "P001:sizing"
    line = at.selectbox(key="evidence_line")
    assert line.value.startswith("HD-") and all(o.startswith("HD-") for o in line.options)
    # Readable evidence card above the wide table: original message, exact quote, source, review status
    cards = " ".join(m.value for m in at.markdown)
    assert "**Supporting quote:**" in cards and "✓ exact match in the message" in cards
    assert "**Label source:** Synthetic reference" in cards and "**Review status:**" in cards
    assert any(e.label.startswith("Technical provenance") for e in at.expander)

    # Resolve a review-queue line
    pick = at.selectbox(key="review_pick")
    olid = pick.value
    assert pick.format_func(olid).startswith("⚠")
    item = state  # noqa: F841
    at.selectbox(key=f"corr_cat_{olid}").set_value("sizing").run()
    msgs = at.multiselect(key=f"corr_support_{olid}").options
    at.multiselect(key=f"corr_support_{olid}").set_value([msgs[0]]).run()
    at.text_input(key=f"corr_reason_{olid}").input("Checked the customer's emails").run()
    at.checkbox(key=f"corr_confirm_{olid}").check().run()
    at.button(key="apply_item").click().run()
    assert not at.exception, at.exception
    assert len(_state(at).corrections) == 1 and _state(at).report_version == 2
    assert any("Report version is now 2" in s.value for s in at.success)

    # Action plan: generate, then downloads exist for the current version
    at.radio(key="nav").set_value("Action Plan").run()
    at.button(key="gen_actions").click().run()
    assert not at.exception
    assert any("Report version 2" in m.value for m in at.markdown)
    assert any("Synthetic reference" in c.value for c in at.caption)
    labels = [b.label for b in at.get("download_button")]
    assert labels == ["Findings CSV", "Evidence CSV", "Corrections CSV", "Report JSON", "Action plan (Markdown)"]


def test_display_filters_are_explained_and_do_not_narrow_actions(at):
    at.button(key="load_demo").click().run()
    at.button(key="analyze").click().run()
    at.radio(key="nav").set_value("Action Plan").run()
    at.button(key="gen_actions").click().run()
    plan = _state(at).actions
    at.sidebar.multiselect(key="f_categories").set_value(["delivery"]).run()
    assert not at.exception
    assert any("narrow only the Findings list" in i.value for i in at.info)
    assert _state(at).actions is plan and any("Report version 1" in m.value for m in at.markdown)
    assert {a.category for a in plan.items} > {"delivery"}  # actions still cover other categories
    at.radio(key="nav").set_value("Findings").run()
    assert any("hide" in i.value and "action plan still cover" in i.value for i in at.info)
    assert any("only narrow the findings list" in c.value for c in at.sidebar.caption)


def test_reset_requires_confirmation(at):
    at.button(key="load_demo").click().run()
    assert at.button(key="reset").disabled
    at.checkbox(key="reset_confirm").check().run()
    at.button(key="reset").click().run()
    assert _state(at).dataset is None


def test_manual_mode_on_review_cases(at):
    at.button(key="load_review").click().run()
    at.sidebar.radio(key="mode_choice").set_value("Manual review").run()
    at.button(key="analyze").click().run()
    assert not at.exception
    assert _state(at).prediction_mode == "Manual review"
    at.radio(key="nav").set_value("Evidence & Review").run()
    assert not at.exception
    assert "Unknown purchase links (1)" in [t.label for t in at.tabs]


ORDERS_CUSTOM = (
    b"Line ID,SKU,Product,Order Date,Price,Refunded\n"
    b"L-001,S1,Rain Hat,2026-09-01,25.00,25.00\n"
    b"L-002,S1,Rain Hat,2026-09-02,25.00,0\n"
)
FEEDBACK_CUSTOM = b'Ticket ID,Order Line,Body\nT1,L-001,"Far too small, sadly."\nT2,L-404,Late\n'


def _submit_mapping(app):
    next(b for b in app.button if b.label == "Confirm mapping and validate").click().run()


def test_custom_upload_with_column_mapping_and_manual_review(at):
    at.file_uploader(key="up_orders").upload("shop_orders.csv", ORDERS_CUSTOM, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("shop_feedback.csv", FEEDBACK_CUSTOM, "text/csv").run()
    assert not at.exception
    assert at.selectbox(key="map_o_order_line_id").value == "Line ID"  # suggested, not yet confirmed
    at.selectbox(key="upload_currency").set_value("USD").run()
    _submit_mapping(at)
    assert any("Validation passed" in s.value for s in at.success)
    at.button(key="use_upload").click().run()
    state = _state(at)
    assert state.dataset.currency == "USD" and not state.dataset.is_synthetic_sample
    assert state.mode == "Manual review"  # sample labels never apply to uploads; no key configured
    at.button(key="analyze").click().run()
    at.radio(key="nav").set_value("Evidence & Review").run()
    assert "Unknown purchase links (1)" in [t.label for t in at.tabs]
    at.selectbox(key="review_pick").set_value("L-001").run()
    at.selectbox(key="corr_cat_L-001").set_value("sizing").run()
    at.multiselect(key="corr_support_L-001").set_value(["T1"]).run()
    at.text_input(key="corr_quote_L-001_T1").input("Far too small").run()
    at.text_input(key="corr_reason_L-001").input("Owner read the ticket").run()
    at.checkbox(key="corr_confirm_L-001").check().run()
    at.button(key="apply_item").click().run()
    assert not at.exception
    at.radio(key="nav").set_value("Findings").run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Refunds with an accepted issue"] == "USD 25.00"
    assert metrics["Reconciliation"] == "✓ balanced"


def _use_buttons(app):
    return [b for b in app.button if b.key == "use_upload"]


def test_currency_change_after_confirmation_requires_revalidation(at):
    # Reproduces the reviewed bug: confirm in INR, switch to USD, use the files.
    at.file_uploader(key="up_orders").upload("o.csv", ORDERS_CUSTOM, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    assert at.selectbox(key="upload_currency").value == "INR"
    _submit_mapping(at)
    assert _use_buttons(at)
    at.selectbox(key="upload_currency").set_value("USD").run()
    assert not at.exception
    assert not _use_buttons(at), "stale INR confirmation must not be usable while USD is shown"
    assert any("currency changed from INR to USD" in w.value for w in at.warning)
    _submit_mapping(at)
    at.button(key="use_upload").click().run()
    assert _state(at).dataset.currency == at.selectbox(key="upload_currency").value == "USD"


def test_currency_changed_in_same_interaction_as_use_click_is_refused(at):
    at.file_uploader(key="up_orders").upload("o.csv", ORDERS_CUSTOM, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    _submit_mapping(at)
    at.selectbox(key="upload_currency").set_value("EUR")
    at.button(key="use_upload").click().run()
    assert _state(at).dataset is None
    assert any("changed before activation" in e.value for e in at.error)


def test_replacing_file_contents_under_same_name_invalidates_confirmation(at):
    at.file_uploader(key="up_orders").upload("o.csv", ORDERS_CUSTOM, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    _submit_mapping(at)
    at.button(key="use_upload").click().run()
    active = _state(at).dataset
    assert active is not None
    changed = ORDERS_CUSTOM.replace(b"25.00,0\n", b"30.00,0\n")
    at.file_uploader(key="up_orders").upload("o.csv", changed, "text/csv").run()
    assert not at.exception
    assert not _use_buttons(at) and not any("Validation passed" in s.value for s in at.success)
    assert _state(at).dataset is active  # the active analysis is untouched until explicit activation
    _submit_mapping(at)
    at.button(key="use_upload").click().run()
    assert _state(at).dataset is not active
    assert int(_state(at).dataset.orders["sale_minor"].sum()) == 5500


def test_stale_mapping_and_name_choices_are_dropped_for_new_contents(at):
    conflict = ORDERS_CUSTOM.replace(b"L-002,S1,Rain Hat", b"L-002,S1,Rain Cap")
    at.file_uploader(key="up_orders").upload("o.csv", conflict, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    at.selectbox(key="map_o_product_name").set_value("Product")
    _submit_mapping(at)
    at.selectbox(key="name_S1").set_value("Rain Cap")
    next(b for b in at.button if b.label == "Confirm names").click().run()
    assert _use_buttons(at)
    # Same filename, different contents (another name conflict): old choices must not carry over.
    conflict2 = conflict.replace(b"Rain Cap", b"Rain Bonnet")
    at.file_uploader(key="up_orders").upload("o.csv", conflict2, "text/csv").run()
    assert "confirmed_names" not in at.session_state and "confirmed_mapping" not in at.session_state
    _submit_mapping(at)
    assert not _use_buttons(at)
    assert at.selectbox(key="name_S1").options == ["Rain Hat", "Rain Bonnet"]


def test_reset_everything_clears_cached_live_predictions(at):
    at.button(key="load_demo").click().run()
    quote = "Too tight at the waist"
    _state(at).cache["seeded-key"] = {"message_id": "M0037", "evidence_quote": quote, "category": "sizing"}
    at.run()  # ordinary rerun keeps the session cache
    assert "seeded-key" in _state(at).cache
    at.checkbox(key="reset_confirm").check().run()
    at.button(key="reset").click().run()
    assert not at.exception
    fresh = _state(at)
    assert fresh.dataset is None and fresh.cache == {} and fresh.predictions == {}
    assert fresh.corrections == [] and fresh.actions is None and fresh.run_log == []
    assert quote not in repr(fresh.__dict__)
    assert not any(k.startswith(("f_", "map_", "confirmed_")) for k in at.session_state)


def test_sample_mode_refuses_uploaded_data(at):
    at.file_uploader(key="up_orders").upload("o.csv", ORDERS_CUSTOM, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    _submit_mapping(at)
    at.button(key="use_upload").click().run()
    at.sidebar.radio(key="mode_choice").set_value("Sample demonstration").run()
    assert at.button(key="analyze").disabled
    assert any("only for the shipped example" in i.value for i in at.info)


def test_invalid_financial_upload_stops_analysis(at):
    bad = ORDERS_CUSTOM.replace(b"L-002,S1,Rain Hat,2026-09-02,25.00,0", b"L-002,S1,Rain Hat,2026-09-02,25.00,30.00")
    at.file_uploader(key="up_orders").upload("o.csv", bad, "text/csv").run()
    at.file_uploader(key="up_feedback").upload("f.csv", FEEDBACK_CUSTOM, "text/csv").run()
    _submit_mapping(at)
    assert any("Nothing was analysed" in e.value for e in at.error)
    assert _state(at).dataset is None
    assert not [b for b in at.button if b.key == "use_upload"]
