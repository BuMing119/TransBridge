# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Destination choices for writing a native plugin from its bound source."""

from dataclasses import replace
from pathlib import Path

from transbridge.application.io.contracts import SourceSnapshot
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.plugin_destination import PLUGIN_DESTINATION_MODE as DESTINATION_MODE

from .plan_view import EditableControl, EditableFieldState

DIRECTORY = "directory"
ORIGINAL = "original"


def destination_mode_field(mode: str = "") -> EditableFieldState:
    return EditableFieldState(
        "destination_mode",
        "插件输出方式",
        mode or DIRECTORY,
        control=EditableControl.CHOICE,
        options=((DIRECTORY, "另存到目录"), (ORIGINAL, "写回原插件")),
    )


def plugin_destination_fields(directory: str = "", mode: str = "") -> tuple[EditableFieldState, ...]:
    return (
        destination_mode_field(mode),
        EditableFieldState("output_directory", "输出目录", directory, required=True),
    )


def plugin_transfer_field(path: str = "") -> EditableFieldState:
    return EditableFieldState("plugin_path", "套用到其他插件（可选）", path)


def plugin_source_path(snapshot: SourceSnapshot) -> str:
    path = snapshot.source.uri
    return path if snapshot.content is not None and Path(path).is_absolute() else ""


def plugin_filename(snapshot: SourceSnapshot) -> str:
    return Path(plugin_source_path(snapshot) or snapshot.source.display_name or snapshot.source.uri).name


def retarget_plugin(draft: HydratedWriteDraft, mode: str, directory: str = "") -> HydratedWriteDraft:
    options = dict(draft.options)
    if mode == ORIGINAL:
        target = plugin_source_path(draft.source_snapshot)
    elif mode == DIRECTORY:
        target = str(Path(directory) / plugin_filename(draft.source_snapshot)) if directory else ""
    else:
        raise ValueError("请选择有效的插件输出方式。")
    options[DESTINATION_MODE] = mode
    return replace(draft, target_path=target, options=tuple(options.items()))
