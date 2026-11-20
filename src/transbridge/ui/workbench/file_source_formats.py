# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Shared display choices for opening sources and creating projects."""

from pathlib import Path

from transbridge.application.io.catalog import filename_format_hints
from transbridge.application.io.contracts import FormatId

TRANSLATION_FILE_FORMATS = (
    ("自动识别文件内容", None),
    ("Bethesda 插件", "plugin.sse"),
    ("ESP-ESM Translator EET", "binary.eet"),
    ("ESP-ESM Translator XML", "xml.eet"),
    ("xTranslator XML", "xml.xt"),
    ("xTranslator SST · SSU8", "sst.ssu8"),
    ("xTranslator SST · SSU9", "sst.ssu9"),
    ("DSD JSON", "json.dsd"),
    ("ParaTranz JSON", "json.paratranz"),
)
PLUGIN_SOURCE_FILTER = "Bethesda 插件 (*.esp *.esm *.esl)"
_NON_PLUGIN_TRANSLATION_PATTERNS = "*.eet *.xml *.sst *.json"
_TRANSLATION_PATTERNS = f"*.esp *.esm *.esl {_NON_PLUGIN_TRANSLATION_PATTERNS}"
_NON_PLUGIN_TRANSLATION_FILTERS = (
    "ESP-ESM Translator EET (*.eet)",
    "ESP-ESM Translator XML (*.xml)",
    "xTranslator XML (*.xml)",
    "xTranslator SST (*.sst)",
    "DSD JSON (*.json)",
    "ParaTranz JSON (*.json)",
)
_TRANSLATION_FILTERS = (PLUGIN_SOURCE_FILTER, *_NON_PLUGIN_TRANSLATION_FILTERS, "所有文件 (*)")
_STRINGS_PATTERNS = "*.strings *.dlstrings *.ilstrings"
INCOMPATIBLE_SOURCE_FILTER = ";;".join((
    f"非插件来源 ({_NON_PLUGIN_TRANSLATION_PATTERNS} {_STRINGS_PATTERNS})",
    f"Skyrim Strings ({_STRINGS_PATTERNS})",
    *_NON_PLUGIN_TRANSLATION_FILTERS,
))
FILE_SOURCE_FORMATS = (
    *TRANSLATION_FILE_FORMATS,
    ("Skyrim STRINGS", "strings.strings"),
    ("Skyrim DLSTRINGS", "strings.dlstrings"),
    ("Skyrim ILSTRINGS", "strings.ilstrings"),
)
TRANSLATION_FILE_FILTER = ";;".join((f"支持的文件 ({_TRANSLATION_PATTERNS})", *_TRANSLATION_FILTERS))
FILE_SOURCE_FILTER = ";;".join((
    f"支持的文件 ({_TRANSLATION_PATTERNS} {_STRINGS_PATTERNS})",
    f"Skyrim Strings ({_STRINGS_PATTERNS})",
    *_TRANSLATION_FILTERS,
))
FILE_FILTER_HINTS = {"DSD JSON (*.json)": "json.dsd", "ParaTranz JSON (*.json)": "json.paratranz"}


def is_plugin_source(path: str) -> bool:
    return FormatId.PLUGIN_SSE in filename_format_hints(Path(path).suffix)


def is_incompatible_source(path: str) -> bool:
    formats = filename_format_hints(Path(path).suffix)
    return bool(formats) and FormatId.PLUGIN_SSE not in formats


def project_source_hints(paths: tuple[str, ...]) -> frozenset[FormatId]:
    """Find format hints shared by the non-plugin sources in a creation draft."""

    formats = (filename_format_hints(Path(path).suffix) for path in paths)
    non_plugin = [choices for choices in formats if FormatId.PLUGIN_SSE not in choices]
    if non_plugin:
        return frozenset(non_plugin[0]).intersection(*non_plugin[1:])
    return frozenset((FormatId.PLUGIN_SSE,)) if paths else frozenset()
