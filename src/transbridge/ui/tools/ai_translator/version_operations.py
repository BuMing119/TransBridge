"""Translate failed version command outcomes into actionable task errors."""

from __future__ import annotations


def require_success(result: object) -> object:
    if result.is_success:
        return result
    diagnostics = tuple(result.diagnostics)
    if diagnostics:
        diagnostic = diagnostics[0]
        raise RuntimeError(f"{diagnostic.code}: {diagnostic.message}")
    raise RuntimeError("版本操作失败。")
