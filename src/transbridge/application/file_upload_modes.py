# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""File operations shared by ParaTranz upload execution and project policies."""

from enum import StrEnum


class UploadOperation(StrEnum):
    ORIGINAL = "orig_only"
    SAFE = "trans_safe"
    FORCE = "trans_force"
    BOTH = "both"
    FILL_EMPTY = "trans_fill_empty"


UPLOAD_MODES = tuple(operation.value for operation in UploadOperation)
