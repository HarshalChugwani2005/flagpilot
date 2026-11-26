"""Flagsmith access: MCP server first, Admin API (httpx) as fallback.

Only :mod:`agent.executor` may call the write methods.
"""


class FlagsmithClient:
    """Thin wrapper over the Flagsmith MCP server with an Admin API fallback."""

    def create_multivariate_flag(
        self, name: str, description: str, variants: dict[str, int]
    ) -> dict[str, object]:
        """Create flag ``name`` with variant values mapped to traffic weights (percent)."""
        raise NotImplementedError("FlagsmithClient.create_multivariate_flag: later step.")

    def set_winner(self, name: str, winning_value: str) -> dict[str, object]:
        """Roll out ``winning_value`` to 100% of traffic for flag ``name``."""
        raise NotImplementedError("FlagsmithClient.set_winner: later step.")

    def delete_flag(self, name: str) -> None:
        """Delete flag ``name`` (used by the demo reset script)."""
        raise NotImplementedError("FlagsmithClient.delete_flag: later step.")
