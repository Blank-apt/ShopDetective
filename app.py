"""ShopDetective — Streamlit entry point.

Run with:  python -m streamlit run app.py
"""

from __future__ import annotations

import logging

import streamlit as st

from shopdetective.analytics import compute_report
from shopdetective.config import load_settings
from shopdetective.session import get_state
from shopdetective.ui import actions, evidence, findings, upload
from shopdetective.ui.common import NAV_PAGES, render_sidebar

# Logs carry counts, codes and versions only — never keys or customer text.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="ShopDetective", page_icon="🔎", layout="wide")

settings = load_settings()
state = get_state(st.session_state)

st.title("ShopDetective")
st.markdown(
    "Find which recurring product problems are associated with the most recorded refunds, "
    "see the evidence, and decide what to investigate first."
)

filters = render_sidebar(state, settings)

if "nav" not in st.session_state:
    st.session_state["nav"] = NAV_PAGES[0]
page = st.radio("Section", NAV_PAGES, horizontal=True, key="nav", label_visibility="collapsed")
st.divider()

report = None
if state.dataset is not None and state.analyzed:
    report = compute_report(state, filters)

if page == "Upload & Analyze":
    upload.render(state, settings)
elif page == "Findings":
    findings.render(state, report)
elif page == "Evidence & Review":
    evidence.render(state, report)
else:
    actions.render(state, report, filters, settings)
