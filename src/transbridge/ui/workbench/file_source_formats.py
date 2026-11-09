# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Shared display choices for opening sources and creating projects."""

FILE_SOURCE_FORMATS = (
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
FILE_SOURCE_FILTER = (
    "支持的文件 (*.esp *.esm *.esl *.eet *.xml *.sst *.json);;"
    "Bethesda 插件 (*.esp *.esm *.esl);;ESP-ESM Translator EET (*.eet);;"
    "ESP-ESM Translator XML (*.xml);;xTranslator XML (*.xml);;xTranslator SST (*.sst);;"
    "DSD JSON (*.json);;ParaTranz JSON (*.json);;所有文件 (*)"
)
FILE_FILTER_HINTS = {"DSD JSON (*.json)": "json.dsd", "ParaTranz JSON (*.json)": "json.paratranz"}
