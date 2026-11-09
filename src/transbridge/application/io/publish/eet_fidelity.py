# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Validate every EET field, including caches and records outside the edit set."""

import hashlib

from transbridge.parser.eet_binary_codec import EetDocument

from .models import ValidationReport


def validate_eet_fidelity(request, content: bytes, adapter) -> ValidationReport:
    try:
        actual = EetDocument.from_bytes(content)
        expected = adapter.render_document(request)
        valid = actual == expected
        return ValidationReport(
            valid,
            request.format_id,
            True,
            True,
            valid,
            len(actual.records),
            hashlib.sha256(content).hexdigest(),
            "VALID" if valid else "EET_FIDELITY_MISMATCH",
            "EET 全部记录、缓存及元数据验证通过。" if valid else "EET 记录、缓存或元数据与预期不一致。",
        )
    except (ValueError, TypeError, OSError) as exc:
        return ValidationReport(
            False,
            request.format_id,
            True,
            False,
            False,
            0,
            code="EET_FIDELITY_FAILED",
            message=f"EET 完整回读验证失败：{exc}",
        )
