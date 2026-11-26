"""FlagPilot Streamlit entry point: ``streamlit run ui/app.py``."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_settings  # noqa: E402
from agent.errors import ConfigError  # noqa: E402


def show_config_status() -> None:
    """Show whether ``.env`` is complete, with a friendly message if it is not."""
    try:
        settings = get_settings()
    except ConfigError as error:
        st.error(error.friendly)
        return
    st.success(f"Configured for Flagsmith project {settings.flagsmith_project_id}.")


def main() -> None:
    """Render the landing page."""
    st.set_page_config(page_title="FlagPilot", page_icon="🚩", layout="wide")
    st.title("FlagPilot")
    st.caption("Plain-English A/B experiments on Flagsmith, with human approval at every step.")
    show_config_status()
    st.markdown(
        "- **Plan**: describe an experiment, review the exact Flagsmith changes, then "
        "approve, edit or reject them.\n"
        "- **Audit Log**: every approval, rejection and Flagsmith call.\n"
        "- **Results**: arrives with the reporter step."
    )


main()
