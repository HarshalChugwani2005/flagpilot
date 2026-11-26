"""Cached reads from Flagsmith for the UI (short TTL so the sidebar stays current)."""

import streamlit as st

from agent.errors import FlagPilotError
from agent.flagsmith_client import FlagsmithClient
from agent.models import VariantCounts


@st.cache_data(ttl=60, show_spinner=False)
def flagsmith_health() -> tuple[bool, str]:
    """Return ``(connected, message)`` from listing the project's flags."""
    try:
        names = FlagsmithClient().list_flag_names()
    except FlagPilotError as error:
        return False, error.friendly
    return True, f"{len(names)} flags in project"


@st.cache_data(ttl=60, show_spinner=False)
def variant_counts(
    flag_name: str, metric_event: str
) -> tuple[dict[str, VariantCounts] | None, str | None]:
    """Return ``(counts, None)`` from Flagsmith, or ``(None, friendly error)``."""
    try:
        return FlagsmithClient().get_variant_counts(flag_name, metric_event), None
    except FlagPilotError as error:
        return None, error.friendly
