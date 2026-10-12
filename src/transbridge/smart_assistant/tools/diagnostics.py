"""Project caught technical exceptions, separately from business tool results."""

from transbridge.application.security import diagnostic_exception_text


def technical_error(error: BaseException) -> str:
    """Hide credentials in an exception without scanning surrounding business data."""
    return diagnostic_exception_text(error)
