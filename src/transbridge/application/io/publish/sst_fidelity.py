"""Verify every SST field, including unselected records and header metadata."""

import hashlib

from transbridge.parser.xt.sst_codec import SstDocument

from .models import ValidationReport


def validate_sst_fidelity(request, content: bytes, adapter) -> ValidationReport:
    try:
        actual = SstDocument.from_bytes(content)
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
            "VALID" if valid else "SST_FIDELITY_MISMATCH",
            "SST 全部记录及元数据验证通过。" if valid else "SST 记录、身份或元数据与预期不一致。",
        )
    except (ValueError, TypeError, OSError) as exc:
        return ValidationReport(
            False,
            request.format_id,
            True,
            False,
            False,
            0,
            code="SST_FIDELITY_FAILED",
            message=f"SST 完整回读验证失败：{exc}",
        )
