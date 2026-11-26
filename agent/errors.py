"""Error types that carry a user-facing message instead of a stack trace."""


class FlagPilotError(Exception):
    """Base error for FlagPilot; ``friendly`` is safe to show in the UI."""

    def __init__(self, friendly: str, *, detail: str | None = None) -> None:
        """Store the user-facing message and optional technical detail."""
        super().__init__(friendly if detail is None else f"{friendly} ({detail})")
        self.friendly = friendly
        self.detail = detail


class ConfigError(FlagPilotError):
    """Raised when required settings are missing or invalid."""
