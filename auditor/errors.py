class AuditError(Exception):
    """A safe, actionable error suitable for display to a user."""


class ToolError(AuditError):
    """An unavailable or invalid inspection; returned to the LLM for recovery."""
