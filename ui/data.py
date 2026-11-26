"""Cached reads from Flagsmith for the UI (short TTL so pages stay current)."""

import streamlit as st

from agent.errors import FlagPilotError
from agent.flagsmith_client import FlagsmithClient
from agent.models import ExperimentPlan, ExperimentResult
from agent.reporter import fetch_result
from agent.results import FlagsmithResults


@st.cache_data(ttl=60, show_spinner=False)
def flagsmith_health() -> tuple[bool, str]:
    """Return ``(connected, message)`` from listing the project's flags."""
    try:
        names = FlagsmithClient().list_flag_names()
    except FlagPilotError as error:
        return False, error.friendly
    return True, f"{len(names)} flags in project"


@st.cache_data(ttl=60, show_spinner=False)
def experiment_result(plan_json: str) -> tuple[ExperimentResult | None, str | None]:
    """Return ``(result, None)`` computed from Flagsmith's counts, or ``(None, error)``."""
    plan = ExperimentPlan.model_validate_json(plan_json)
    try:
        return fetch_result(plan, FlagsmithResults()), None
    except FlagPilotError as error:
        return None, error.friendly
