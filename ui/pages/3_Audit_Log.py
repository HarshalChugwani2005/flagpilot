"""Audit Log page: every Flagsmith write with its approval record."""

import streamlit as st

st.title("Audit log")
st.info("This page reads audit_log.jsonl once the executor writes it in a later step.")
