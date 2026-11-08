"""Prepare one editable source and its optional translations without publishing it."""

from dataclasses import dataclass
import hashlib
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.projects.prepared_source_results import prepare_parsed_source
from transbridge.application.projects.provisioning_models import PreparedProjectSource
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .catalog import default_format_catalog
from .contracts import FormatId, ParseRequest, ProbeRequest, ProbeStatus, SourceDescriptor
from .open_file_options import OpenFileOptions
from .translation_import import prepare_translation_import
from .use_case import TranslationIoUseCase

OPEN_FILE_FORMATS = frozenset({
    FormatId.PLUGIN_SSE,
    FormatId.BINARY_EET,
    FormatId.XML_EET,
    FormatId.XML_XT,
    FormatId.SST_SSU8,
    FormatId.SST_SSU9,
    FormatId.JSON_DSD,
    FormatId.JSON_PARATRANZ,
})


@dataclass(frozen=True, slots=True)
class OpenedTranslationFile:
    source: PreparedProjectSource
    strings_lookup: object = None
    note: str = ""


def prepare_open_file(path: str, options: OpenFileOptions, context: RequestContext, *, apply_dictionary=None):
    """Capture native content once for publication; never reparse it during commit."""
    source_path = Path(path).resolve(strict=True)
    content = source_path.read_bytes()
    descriptor = SourceDescriptor(str(source_path), source_path.name, len(content))
    hint = FormatId(options.format_hint) if options.format_hint else None
    catalog = default_format_catalog()
    probe = catalog.resolve(ProbeRequest(descriptor, content, hint))
    if probe.status is ProbeStatus.AMBIGUOUS:
        raise ValueError("文件格式不明确，请选择具体格式（例如 DSD JSON 或 ParaTranz JSON）。")
    if probe.status is not ProbeStatus.EXACT:
        raise ValueError("文件内容与所选格式不符或无法识别，请检查文件并选择正确格式。")
    format_id = probe.candidates[0]
    if format_id not in OPEN_FILE_FORMATS:
        raise ValueError("此入口不支持该来源格式。")
    parse_options = (
        (("skip_empty", options.skip_empty), ("discover_sibling_strings", True))
        if format_id is FormatId.PLUGIN_SSE
        else ()
    )
    parsed = TranslationIoUseCase(catalog).parse(ParseRequest(descriptor, context, format_id, options=parse_options))
    if parsed.outcome is not OperationOutcome.COMPLETED or parsed.source_snapshot is None:
        detail = "\n".join(f"{item.code}: {item.message}" for item in parsed.diagnostics)
        raise ValueError(detail or "文件未完整解析，未添加到工程。")
    if parsed.source_snapshot.sha256 != hashlib.sha256(content).hexdigest():
        raise ValueError("读取期间来源文件已变化，请重新打开。")
    entries = parsed.entries
    if format_id is FormatId.JSON_PARATRANZ:
        entries = tuple(entry.to_translation_entry() for entry in entries)
    collection = TranslationEntryCollection(entries)
    strings_lookup, note = None, ""
    if options.translation_path:
        if options.translation_format_hint == "strings-directory" and format_id is not FormatId.PLUGIN_SSE:
            raise ValueError("Strings 目录只能应用到插件来源，请选择插件或使用其他译文文件。")
        imported = prepare_translation_import(
            options.translation_path,
            collection,
            format_hint=options.translation_format_hint,
            replace_existing=options.replace_existing,
            plugin_stem=source_path.stem if format_id is FormatId.PLUGIN_SSE else "",
            strings_lang=options.strings_lang,
            context=context,
            target_snapshot=parsed.source_snapshot,
        )
        collection, strings_lookup = imported.collection, imported.strings_lookup
        if not imported.applied_count:
            note = "文件已打开；所选译文未产生更新，请检查匹配内容和保留/替换策略。"
    if format_id is FormatId.PLUGIN_SSE and apply_dictionary is not None:
        apply_dictionary(collection)
    prepared = prepare_parsed_source(
        str(source_path),
        tuple(collection),
        parsed.source_snapshot,
        format_id,
        parse_options,
        diagnostics=parsed.diagnostics,
    )
    return OpenedTranslationFile(prepared, strings_lookup, note)
