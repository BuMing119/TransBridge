"""Stable composition errors shared by production operation slices."""


class OperationCompositionError(RuntimeError):
    """A required production capability is unavailable at composition or submission time."""


__all__ = ["OperationCompositionError"]
