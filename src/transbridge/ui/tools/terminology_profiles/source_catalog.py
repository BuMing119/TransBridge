"""Discover user-facing terminology sources for naming-scheme creation."""

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


def configured_source_selections(context) -> tuple[TerminologySourceSelection, ...]:
    """Return project and persisted local sources without merging them."""

    selections: list[TerminologySourceSelection] = []
    seen_plugin_paths: set[str] = set()
    for source in getattr(context, "project_sources", ()):
        location = str(source.get("location") or "").strip()
        if not source.get("enabled", True) or not _is_plugin_source(source, location):
            continue
        path_key = str(Path(location)).casefold()
        if not location or path_key in seen_plugin_paths:
            continue
        seen_plugin_paths.add(path_key)
        plugin_name = str(source.get("name") or source.get("label") or Path(location).stem)
        label = f"动态词库 · {plugin_name}"
        selections.append(
            TerminologySourceSelection(
                TermSourceReadRequest("dynamic", label, esp_path=location),
                f"{plugin_name}译名方案",
                f"读取项目插件 {Path(location).name} 对应的动态术语库。",
            )
        )

    project = bound_paratranz_project(context)
    if project is not None:
        label = f"ParaTranz 术语 · {project.get('name') or project['id']}"
        selections.append(
            TerminologySourceSelection(
                TermSourceReadRequest("paratranz", label, paratranz_project_id=int(project["id"])),
                f"{project.get('name') or 'ParaTranz'}译名方案",
                "读取当前工程绑定的 ParaTranz 项目术语。",
                _frozen_paratranz_reader(context),
            )
        )

    config = LLMConfig.load_from_file()
    for source_id, label, path_value in (
        ("json", "本地 JSON", config.local_json_path),
        ("csv", "本地 CSV", getattr(config, "local_csv_path", "")),
        ("excel", "本地 Excel", config.local_excel_path),
    ):
        path = str(path_value or "").strip()
        if path:
            selections.append(_local_file_selection(path, config=config, label_prefix=label))
    return tuple(selections)


def local_file_selection(path: str) -> TerminologySourceSelection:
    """Create one source selection for an explicitly browsed local file."""

    return _local_file_selection(path, config=LLMConfig.load_from_file())


def _local_file_selection(
    path: str,
    *,
    config: LLMConfig,
    label_prefix: str | None = None,
) -> TerminologySourceSelection:
    resolved = Path(path)
    suffix = resolved.suffix.casefold()
    source_id = {".json": "json", ".csv": "csv", ".xls": "excel", ".xlsx": "excel"}.get(suffix)
    if source_id is None:
        raise ValueError("请选择 JSON、CSV、XLS 或 XLSX 术语文件。")
    prefix = label_prefix or {"json": "本地 JSON", "csv": "本地 CSV", "excel": "本地 Excel"}[source_id]
    label = f"{prefix} · {resolved.name}"
    return TerminologySourceSelection(
        TermSourceReadRequest(
            source_id,
            label,
            file_path=str(resolved),
            excel_original_column=config.excel_original_col,
            excel_translation_column=config.excel_translation_col,
        ),
        f"{resolved.stem}译名方案",
        f"读取本地文件 {resolved}；创建后与该文件独立。",
    )


def _is_plugin_source(source: dict[str, object], location: str) -> bool:
    kind = str(source.get("kind") or "").casefold()
    format_id = str(source.get("format_id") or "").casefold()
    suffix = Path(location).suffix.casefold()
    return kind == "plugin" or format_id.startswith("plugin.") or suffix in {".esp", ".esm", ".esl"}


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
    "configured_source_selections",
    "local_file_selection",
]
