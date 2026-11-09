# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Build explicit local-file and current-project ParaTranz import requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from transbridge.ai_translator.term_source_reader import ConfiguredTermSourceReader, TermSourceReadRequest
from transbridge.paratranz.config_manager import LLMConfig, ParatranzConfig
from transbridge.ui.paratranz.target_context import bound_paratranz_project


@dataclass(frozen=True, slots=True)
class TerminologySourceSelection:
    """One exact source request selected before the asynchronous read starts."""

    request: TermSourceReadRequest
    default_name: str
    detail: str
    reader_factory: Callable[[], object] | None = None

    @property
    def label(self) -> str:
        return self.request.source_label


def bound_paratranz_selection(context) -> TerminologySourceSelection | None:
    """Read only the current local project's bound ParaTranz target."""
    project = bound_paratranz_project(context)
    if project is None:
        return None
    return TerminologySourceSelection(
        TermSourceReadRequest(
            "paratranz",
            f"ParaTranz 术语 · {project.get('name') or project['id']}",
            paratranz_project_id=int(project["id"]),
        ),
        f"{project.get('name') or 'ParaTranz'}译名方案",
        "读取当前工程绑定的 ParaTranz 项目术语。",
        _frozen_paratranz_reader(context),
    )


def local_file_selection(path: str) -> TerminologySourceSelection:
    """Create one source selection for an explicitly browsed local file."""

    resolved = Path(path)
    suffix = resolved.suffix.casefold()
    source_id = {".json": "json", ".csv": "csv", ".xls": "excel", ".xlsx": "excel"}.get(suffix)
    if source_id is None:
        raise ValueError("请选择 JSON、CSV、XLS 或 XLSX 术语文件。")
    config = LLMConfig.load_from_file() if source_id == "excel" else None
    prefix = {"json": "本地 JSON", "csv": "本地 CSV", "excel": "本地 Excel"}[source_id]
    label = f"{prefix} · {resolved.name}"
    return TerminologySourceSelection(
        TermSourceReadRequest(
            source_id,
            label,
            file_path=str(resolved),
            excel_original_column=config.excel_original_col if config is not None else "A",
            excel_translation_column=config.excel_translation_col if config is not None else "B",
        ),
        f"{resolved.stem}译名方案",
        f"读取本地文件 {resolved}；创建后与该文件独立。",
    )


def _frozen_paratranz_reader(context) -> Callable[[], object]:
    live = context.config
    frozen = ParatranzConfig(
        token=live.token,
        user_id=live.user_id,
        base_url=live.base_url,
        timeout=live.timeout,
        extra_headers=dict(live.headers),
    )

    def factory():
        from transbridge.paratranz.terms_service import ParaTranzTermsService

        return ConfiguredTermSourceReader(lambda: ParaTranzTermsService.from_config(frozen))

    return factory


__all__ = [
    "TerminologySourceSelection",
    "bound_paratranz_selection",
    "local_file_selection",
]
