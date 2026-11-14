# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Protect the bound plugin and its physical resources during export."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.application.contracts import Diagnostic

from .contracts import SourceSnapshot
from .plugin_localized_sources import localized_physical_inputs

if TYPE_CHECKING:
    from .publish.filesystem import PublishFilesystemPort
    from .publish.models import FileFingerprint

PLUGIN_DESTINATION_MODE = "plugin_destination_mode"


def validate_plugin_destination(
    snapshot: SourceSnapshot,
    mode: object,
    requested_target: str,
    fingerprints: tuple[tuple[str, FileFingerprint], ...],
    filesystem: PublishFilesystemPort,
) -> Diagnostic | None:
    source = os.path.normcase(filesystem.canonicalize(snapshot.source.uri))
    primary = fingerprints[0][0]
    inputs = localized_physical_inputs(snapshot)
    if mode == "directory":
        name = Path(snapshot.source.uri).name
        if Path(requested_target).name != name or os.path.normcase(Path(primary).name) != os.path.normcase(name):
            return Diagnostic("PLUGIN_FILENAME_CHANGED", "另存的插件必须保留来源插件的原文件名。")
        protected = {source, *(os.path.normcase(filesystem.canonicalize(path)) for path, _snapshot in inputs)}
        if any(os.path.normcase(path) in protected for path, _fingerprint in fingerprints):
            return Diagnostic("PLUGIN_SOURCE_PROTECTED", "输出与来源插件或其配套资源重叠，请选择其他另存目录。")
    elif mode == "original":
        if os.path.normcase(primary) != source:
            return Diagnostic("PLUGIN_ORIGINAL_TARGET_MISMATCH", "写回原插件时，输出路径必须与已验证的来源路径一致。")
        expected = {
            source: snapshot.sha256,
            **{
                os.path.normcase(filesystem.canonicalize(path)): captured.sha256
                for path, captured in inputs
                if captured is not None
            },
        }
        if any(expected.get(os.path.normcase(path), value.sha256) != value.sha256 for path, value in fingerprints):
            return Diagnostic("SOURCE_FINGERPRINT_CONFLICT", "原插件或配套字符串已变化，请重新选择来源后再写回。")
    else:
        return Diagnostic("PLUGIN_DESTINATION_INVALID", "插件输出方式无效。")
    return None
