# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Open and retain the desktop project management dialogs."""

from transbridge.ui.project_management_dialogs import (
    ProjectPropertiesDialog,
    SnapshotHistoryDialog,
    VariantManagementDialog,
)
from transbridge.ui.windowing import show_and_activate


class ProjectDialogs:
    def __init__(self, host, *, dispatch, availability) -> None:
        self._host = host
        self._dispatch = dispatch
        self._availability = availability
        self._dialogs = {}

    def open_variants(self) -> None:
        self._open(
            VariantManagementDialog,
            dispatch=self._dispatch,
            availability=self._availability,
            switch=self._host.variant_coordinator.switch_variant,
        )

    def history_availability(self) -> tuple[bool, str | None]:
        if not getattr(self._host.context, "active_version_identity", None):
            return False, "请先打开一个翻译版本。"
        runtime = getattr(self._host, "app_runtime", None)
        if (
            runtime is None
            or self._host.runtime_context is None
            or "project_snapshots" not in runtime.use_cases.names()
        ):
            return False, "当前工程的历史还原服务不可用。"
        return True, None

    def open_history(self) -> None:
        self._open(SnapshotHistoryDialog, transfer=self._host.project_transfer_coordinator)

    def open_properties(self) -> None:
        self._open(ProjectPropertiesDialog, dispatch=self._dispatch, availability=self._availability)

    def _open(self, dialog_type, **options) -> None:
        dialog = self._dialogs.get(dialog_type)
        if dialog is not None and not dialog.disposed and not dialog.targets_current_scope():
            dialog.close()
            dialog = None
        if dialog is None or dialog.disposed:
            dialog = dialog_type(self._host, self._host.context, **options)
            self._dialogs[dialog_type] = dialog
            dialog.finished.connect(lambda _code, view=dialog: self._forget(dialog_type, view))
        show_and_activate(dialog, deferred=True)

    def _forget(self, dialog_type, dialog) -> None:
        if self._dialogs.get(dialog_type) is dialog:
            del self._dialogs[dialog_type]

    def close(self) -> None:
        for dialog in tuple(self._dialogs.values()):
            if not dialog.disposed:
                dialog.close()
