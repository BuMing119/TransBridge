"""Worker ownership boundary for Step1 parsing operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar

from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import XT_XmlParser


class WorkerPort(Protocol):
    def isRunning(self) -> bool: ...


WorkerT = TypeVar("WorkerT", bound=WorkerPort)


@dataclass(frozen=True, slots=True)
class MigrationRequest:
    eet_path: str | None
    xt_path: str | None
    translated_plugin_path: str | None
    sst_path: str | None
    strings_dir: str | None
    strings_language: str
    apply_strings_to_all: bool


@dataclass(frozen=True, slots=True)
class MigrationResult:
    migrated_count: int
    request: MigrationRequest
    updated_slots: tuple[tuple[object, int], ...]


class ParsePresenter:
    """Own worker references without moving parser/application authority into UI."""

    def __init__(self) -> None:
        self.workers: list[WorkerPort] = []
        self._closed = False

    def track(self, worker: WorkerT) -> WorkerT:
        if self._closed:
            raise RuntimeError("parse presenter is closed")
        self.workers[:] = [item for item in self.workers if item.isRunning()]
        self.workers.append(worker)
        return worker

    def close(self) -> None:
        """Release completed workers; running workers keep their existing runtime owner."""
        if self._closed:
            return
        self._closed = True
        self.workers[:] = [item for item in self.workers if item.isRunning()]

    @staticmethod
    def apply_migration(context, slot, request: MigrationRequest) -> MigrationResult:
        migrated_count = 0
        updated_slots: list[tuple[object, int]] = []
        slots = list(context.slots.values()) if request.apply_strings_to_all else [slot]
        for current in slots:
            collection = current.collection
            slot_migrated = 0
            if current is slot:
                if request.eet_path and current.eet_path is None:
                    try:
                        slot_migrated += collection.update_from_eet_xml(Path(request.eet_path))
                    except Exception:
                        pass
                if request.xt_path and current.xt_path is None:
                    try:
                        parser = XT_XmlParser.from_file(request.xt_path)
                        slot_migrated += collection.apply_xt_entries(parser.entries)
                    except Exception:
                        pass
                if request.sst_path and getattr(current, "sst_path", None) is None:
                    try:
                        from transbridge.parser.xt.sst_parser import SST_Parser

                        parsed = SST_Parser.from_file(request.sst_path)
                        slot_migrated += collection.apply_sst_entries(parsed.entries)["updated"]
                    except Exception:
                        pass
                if request.translated_plugin_path:
                    try:
                        slot_migrated += collection.update_from_translated_plugin(Path(request.translated_plugin_path))
                    except Exception:
                        pass
            if request.strings_dir and current.strings_path is None:
                try:
                    stem = Path(current.esp_path).stem if current.esp_path else ""
                    lookup = PluginStringsLookup.from_strings_dir(
                        Path(request.strings_dir), stem, request.strings_language
                    )
                    if lookup:
                        slot_migrated += collection.update_from_strings_lookup(lookup)
                        current.strings_lookup = lookup
                except Exception:
                    pass
            if slot_migrated:
                updated_slots.append((current, slot_migrated))
            migrated_count += slot_migrated
        return MigrationResult(migrated_count, request, tuple(updated_slots))
