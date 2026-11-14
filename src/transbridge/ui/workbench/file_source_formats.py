# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Shared display choices for opening sources and creating projects."""

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
_TRANSLATION_PATTERNS = "*.esp *.esm *.esl *.eet *.xml *.sst *.json"
_TRANSLATION_FILTERS = (
    "Bethesda 插件 (*.esp *.esm *.esl)",
    "ESP-ESM Translator EET (*.eet)",
    "ESP-ESM Translator XML (*.xml)",
    "xTranslator XML (*.xml)",
    "xTranslator SST (*.sst)",
    "DSD JSON (*.json)",
    "ParaTranz JSON (*.json)",
    "所有文件 (*)",
)
_STRINGS_PATTERNS = "*.strings *.dlstrings *.ilstrings"
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
