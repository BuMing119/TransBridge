"""FOMOD 安装包翻译流水线 — FOMOD 特有逻辑。

通用能力（归档/diff/过滤/键对齐）已独立到 FR16 的 fileops/migrator，
本包仅保留 FOMOD 特有逻辑：fomod_xml（安装界面文本）、builder（组装）、pipeline（编排）。
"""

from transbridge.fomod.builder import assemble_output
from transbridge.fomod.discovery import (
    FomodExtractionResult,
    RootCandidate,
    RootDetectionResult,
    detect_mod_roots,
    extract_fomod_archive,
)
from transbridge.fomod.fomod_xml import read_fomod_xml, translate_module_config, write_fomod_xml
from transbridge.fomod.pipeline import FomodPipeline, PipelineResult

__all__ = [
    "read_fomod_xml",
    "write_fomod_xml",
    "translate_module_config",
    "assemble_output",
    "FomodExtractionResult",
    "RootCandidate",
    "RootDetectionResult",
    "detect_mod_roots",
    "extract_fomod_archive",
    "FomodPipeline",
    "PipelineResult",
]
