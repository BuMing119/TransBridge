# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Destination choices for writing a native plugin from its bound source."""

from transbridge.application.io.native_write import (
    DESTINATION_MODE as DESTINATION_MODE,
    DIRECTORY as DIRECTORY,
    ORIGINAL as ORIGINAL,
    retarget_plugin as retarget_plugin,
)

from .plan_view import EditableControl, EditableFieldState


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
