"""Helpers shared by the demo scripts: typed confirmation and asking who approves."""

from collections.abc import Callable


def confirm(question: str, expected: str, input_fn: Callable[[str], str] = input) -> bool:
    """Return True only if the user types ``expected`` exactly (EOF or Ctrl+C cancels)."""
    try:
        answer = input_fn(f"{question}: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip() == expected


def ask_approver(input_fn: Callable[[str], str] = input) -> str | None:
    """Ask for the approver's name for the audit log; None (cancel) if left empty."""
    try:
        name = input_fn("Your name (recorded as the approver in the audit log): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return name or None
