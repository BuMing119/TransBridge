"""Strict, side-effect-free reads for one configured AI terminology source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from transbridge.ai_translator.term_formats import (
    TermEntry,
    load_terms_csv,
    load_terms_excel,
    load_terms_json,
)
from transbridge.application.terminology_profiles import (
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.config.paths import get_data_dir


class TermSourceUnavailableError(RuntimeError):
    """The explicitly selected source cannot provide a trustworthy snapshot."""


class _ParaTranzTerms(Protocol):
    def snapshot_terms(self, project_id: int): ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TermSourceReadRequest:
    source_id: str
    source_label: str
    file_path: str | None = None
    esp_path: str | None = None
    excel_original_column: str = "A"
    excel_translation_column: str = "B"
    paratranz_project_id: int | None = None


class ConfiguredTermSourceReader:
    """Read exactly one source without merged-cache fallback or silent absence."""

    _LOCAL_LOADERS = {
        "json": load_terms_json,
        "csv": load_terms_csv,
    }

    def __init__(self, paratranz_factory: Callable[[], _ParaTranzTerms] | None = None) -> None:
        self._paratranz_factory = paratranz_factory

    def read(self, request: TermSourceReadRequest) -> TerminologySourceSnapshot:
        source_id = request.source_id.strip().casefold()
        if source_id == "dynamic":
            entries = self._read_dynamic(request)
        elif source_id in self._LOCAL_LOADERS:
            entries = self._read_local(request, source_id)
        elif source_id == "excel":
            path = self._required_file(request.file_path, "Excel")
            entries = load_terms_excel(
                path,
                source="excel",
                original_column=request.excel_original_column,
                translation_column=request.excel_translation_column,
            )
        elif source_id == "paratranz":
            entries = self._read_paratranz(request)
        else:
            raise TermSourceUnavailableError(f"不支持的术语来源：{request.source_id}")
        return TerminologySourceSnapshot.capture(
            source_id,
            request.source_label,
            tuple(TerminologySourceEntry(entry.term, entry.translation) for entry in entries),
        )

    def _read_dynamic(self, request: TermSourceReadRequest) -> list[TermEntry]:
        esp_path = str(request.esp_path or "").strip()
        if not esp_path:
            raise TermSourceUnavailableError("动态词库需要先选择一个具体插件。")
        stem = Path(esp_path).stem
        path = Path(get_data_dir()) / "ai_translator" / stem / f"{stem}_terms.json"
        self._required_file(str(path), "动态词库")
        return load_terms_json(path, source=None)

    def _read_local(self, request: TermSourceReadRequest, source_id: str) -> list[TermEntry]:
        label = "JSON" if source_id == "json" else "CSV"
        path = self._required_file(request.file_path, label)
        return self._LOCAL_LOADERS[source_id](path, source=source_id)

    def _read_paratranz(self, request: TermSourceReadRequest) -> list[TermEntry]:
        if request.paratranz_project_id is None:
            raise TermSourceUnavailableError("当前工程尚未绑定可用的 ParaTranz 项目。")
        if self._paratranz_factory is None:
            raise TermSourceUnavailableError("ParaTranz 术语服务当前不可用。")
        service = self._paratranz_factory()
        try:
            snapshot = service.snapshot_terms(request.paratranz_project_id)
        finally:
            service.close()
        if not snapshot.stable:
            raise TermSourceUnavailableError("ParaTranz 术语在读取期间发生变化，请稍后重试。")
        return [item.entry for item in snapshot.items]

    @staticmethod
    def _required_file(value: str | None, label: str) -> Path:
        path = Path(str(value or "").strip())
        if not str(value or "").strip():
            raise TermSourceUnavailableError(f"请先配置{label}术语文件。")
        if not path.is_file():
            raise TermSourceUnavailableError(f"{label}术语文件不存在：{path}")
        return path


__all__ = [
    "ConfiguredTermSourceReader",
    "TermSourceReadRequest",
    "TermSourceUnavailableError",
]
