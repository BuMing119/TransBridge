"""Select independent ESP-ESM Translator EET or XML formats from captured bytes."""

from pathlib import Path

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext

from .catalog import FormatCatalog
from .contracts import FormatId, ParseRequest, ParseResult, ParseStats, ProbeRequest, ProbeStatus, SourceDescriptor
from .identity import SourceNamespace


def probe_eet_format(path: str | Path) -> FormatId:
    """Bounded content probe; full structural validation occurs during parsing."""
    if "://" in str(path):
        raise ValueError("EET 来源必须是本地文件。")
    source = SourceDescriptor(str(path), Path(path).name)
    with Path(path).open("rb") as stream:
        content = stream.read(65536)
    return _format_from_content(source, content)


def _format_from_content(source: SourceDescriptor, content: bytes) -> FormatId:
    probe = FormatCatalog().resolve(ProbeRequest(source, content))
    if probe.status is not ProbeStatus.EXACT or probe.candidates[0] not in {FormatId.BINARY_EET, FormatId.XML_EET}:
        raise ValueError("内容不是 ESP-ESM Translator EET 文件或该工具导出的 XML 文件。")
    return probe.candidates[0]


def parse_eet_source(
    path: str | Path,
    context: RequestContext | None = None,
    *,
    source_namespace: SourceNamespace | None = None,
    cancellation=None,
) -> ParseResult:
    """Route EET files and XML exports to their separate format contracts.

    The historical helper name denotes ESP-ESM Translator input selection,
    not a shared file format. Content detection handles misleading suffixes;
    it never converts XML to EET or makes either format a variant of the other.
    The returned format_id and snapshot retain the actual independent format.
    """
    from .eet_adapter import EetAdapter
    from .legacy_adapters import EetXmlAdapter

    source = SourceDescriptor(str(path), Path(path).name)
    format_id = FormatId.BINARY_EET
    state = cancellation.is_cancelled if cancellation is not None else False
    if state() if callable(state) else state:
        return ParseResult(OperationOutcome.CANCELLED, format_id, source, stats=ParseStats(cancelled=1))
    try:
        if "://" in str(path):
            raise ValueError("EET 来源必须是本地文件。")
        content = Path(path).read_bytes()
        format_id = _format_from_content(source, content)
        request = ParseRequest(
            source,
            context or RequestContext("eet-source"),
            format_id,
            source_namespace=source_namespace,
            cancellation=cancellation,
        )
        adapter = EetAdapter() if format_id is FormatId.BINARY_EET else EetXmlAdapter()
        return adapter.parse_bytes(request, content)
    except (OSError, ValueError) as exc:
        return ParseResult(
            OperationOutcome.FAILED,
            format_id,
            source,
            diagnostics=(Diagnostic("EET_SOURCE_INVALID", f"EET 来源无法读取：{exc}"),),
            stats=ParseStats(failed=1),
        )
