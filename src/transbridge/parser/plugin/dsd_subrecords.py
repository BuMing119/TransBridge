# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read DSD indices that differ from the plugin parser's native indices."""

from collections.abc import Iterator

from sse_plugin_interface.record import Record
from sse_plugin_interface.subrecord import Subrecord


def iter_dsd_subrecords(record: Record) -> Iterator[tuple[Subrecord, int | None]]:
    """Yield physical fields with an index override when the binary layout proves it."""
    if record.type == "QUST":
        yield from _quest_dsd_subrecords(record.subrecords)
    elif record.type == "REGN":
        yield from _region_dsd_subrecords(record.subrecords)
    else:
        yield from ((field, None) for field in record.subrecords)


def _quest_dsd_subrecords(subrecords: list[Subrecord]) -> Iterator[tuple[Subrecord, int | None]]:
    stage_index = None
    objective_index = None
    log_position = -1
    for field in subrecords:
        index = None
        if field.type == "INDX":
            stage_index = int.from_bytes(field.data[:2], "little") if len(field.data) >= 2 else None
            log_position = -1
        elif field.type == "QSDT":
            # xEdit counts every log entry, even when it contains no CNAM.
            log_position += 1
        elif field.type == "QOBJ":
            stage_index = None
            objective_index = int.from_bytes(field.data[:2], "little") if len(field.data) >= 2 else None
        elif field.type == "ANAM":
            stage_index = None
            objective_index = None
        elif field.type == "CNAM" and stage_index is not None and log_position >= 0:
            index = stage_index + log_position
        elif field.type == "NNAM":
            index = objective_index
        yield field, index


def _region_dsd_subrecords(subrecords: list[Subrecord]) -> Iterator[tuple[Subrecord, int | None]]:
    position = -1
    for field in subrecords:
        if field.type == "RDAT":
            position += 1
        yield field, position if field.type == "RDMP" and position >= 0 else None
