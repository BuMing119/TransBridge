# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Explicit source/output format compatibility and artifact routing."""

from .contracts import FormatId, SourceSnapshot
from .plugin_write import plugin_artifact_paths

SST_FORMATS = frozenset({FormatId.SST_SSU8, FormatId.SST_SSU9})


def source_output_compatible(source: FormatId, output: FormatId) -> bool:
    # XML_EET and BINARY_EET are independent, as are XML_XT and SST.
    # Same producer never implies conversion support; only this explicit
    # plugin -> SSU9 export may cross source/output format boundaries.
    return source is output or (source is FormatId.PLUGIN_SSE and output is FormatId.SST_SSU9)


def write_artifact_paths(snapshot: SourceSnapshot, target: str, options=(), *, format_id=None) -> tuple[str, ...]:
    if format_id in {*SST_FORMATS, FormatId.BINARY_EET}:
        return (str(target),)
    return plugin_artifact_paths(snapshot, target, options)
