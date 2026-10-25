"""Write the shared ParaTranz classification to a local directory."""

from __future__ import annotations

from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParatranzJsonAdapter, SourceDescriptor, WriteRequest
from transbridge.application.io.paratranz_classification import classify_entries
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def export_to_categorized_json_files(
    collection: TranslationEntryCollection,
    output_dir: str | Path,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> None:
    """Export the same relative files as categorized upload, beneath ``output_dir``.

    Dialogue files live in ``任务/`` and prefer the matching QUST:FULL original
    name. Classification prepares display order once across the complete source;
    writing individual files must not reassign their order positions.
    """
    output_dir = Path(output_dir)
    groups = classify_entries(tuple(collection))
    output_dir.mkdir(parents=True, exist_ok=True)
    adapter = ParatranzJsonAdapter()
    for group in groups:
        file_path = output_dir / group.path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        result = adapter.write(
            WriteRequest(
                SourceDescriptor(str(file_path), file_path.name, media_type="application/json"),
                FormatId.JSON_PARATRANZ,
                group.entries,
                0,
                RequestContext("categorized-paratranz-export"),
                new_template=b"",
                options=(("ensure_ascii", ensure_ascii), ("indent", indent), ("context_source_order", False)),
            )
        )
        if result.outcome is not OperationOutcome.COMPLETED:
            messages = "; ".join(diagnostic.message for diagnostic in result.diagnostics)
            raise ValueError(messages or f"Unable to write ParaTranz JSON: {file_path}")
