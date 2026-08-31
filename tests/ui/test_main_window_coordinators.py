from __future__ import annotations

from types import SimpleNamespace

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.coordinators.project_transfer_coordinator import ProjectTransferCoordinator


class _Action:
    def __init__(self) -> None:
        self.enabled = True
        self.visible = True

    def setEnabled(self, value: bool) -> None:
        self.enabled = value

    def isEnabled(self) -> bool:
        return self.enabled

    def setVisible(self, value: bool) -> None:
        self.visible = value


def _menu():
    return SimpleNamespace(
        upload=_Action(),
        batch_upload=_Action(),
        download=_Action(),
        batch_download=_Action(),
        write=_Action(),
        batch_write=_Action(),
    )


def test_operation_menu_state_is_derived_once_from_context() -> None:
    menu = _menu()
    host = SimpleNamespace(
        context=SimpleNamespace(
            collection=object(),
            current_project={"id": 7},
            mine_project_ids={7},
            slots={"one": object(), "two": object()},
        ),
        operation_menu=menu,
    )

    OperationCoordinator(host).update_operation_menu_state()

    assert menu.upload.enabled and menu.download.enabled and menu.write.enabled
    assert menu.batch_upload.visible and menu.batch_download.visible and menu.batch_write.visible
    assert menu.batch_upload.enabled and menu.batch_download.enabled


def test_sync_menu_actions_stay_hoverable_when_cloud_context_is_missing() -> None:
    menu = _menu()
    host = SimpleNamespace(
        context=SimpleNamespace(
            collection=object(),
            current_project=None,
            mine_project_ids=set(),
            slots={"one": object(), "two": object()},
        ),
        operation_menu=menu,
    )

    OperationCoordinator(host).update_operation_menu_state()

    assert menu.upload.enabled and menu.download.enabled
    assert menu.batch_upload.enabled and menu.batch_download.enabled


def test_operation_intent_delegates_to_exactly_one_card_action() -> None:
    calls: list[str] = []
    host = SimpleNamespace(
        context=SimpleNamespace(collection=object(), current_project={"id": 7}, slots={"one": object()}),
        upload_card=SimpleNamespace(upload=lambda: calls.append("upload")),
    )

    OperationCoordinator(host).upload()

    assert calls == ["upload"]


def test_parse_dialog_receives_real_window_host(monkeypatch) -> None:
    observed = []
    host = object()

    class Dialog:
        class DialogCode:
            Accepted = 1

        def __init__(self, *, mode, parent) -> None:
            observed.append((mode, parent))

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("transbridge.ui.workbench._parse_config_dialog.ParseConfigDialog", Dialog)

    ParseCoordinator(host).parse_plugin()

    assert observed == [("parse", host)]


def test_authoritative_source_import_commits_initial_states_without_second_variant_command() -> None:
    captured = {}
    key = EntryKey(SourceNamespace("source:plugin:test"), "entry")
    entry = TranslationEntry("entry", "entry", "Original", "导入译文", 3, "FULL", entry_key=key)
    collection = TranslationEntryCollection((entry,))

    class Commands:
        def add_source(self, request, _context, **kwargs):
            captured["options"] = dict(request.options)
            captured["kwargs"] = kwargs
            return OperationResult.completed(
                SimpleNamespace(
                    hydration=SimpleNamespace(entries=(entry.snapshot(),)),
                    project_revision=2,
                    variant_revision=4,
                )
            )

        def replace_entry_states(self, *_args, **_kwargs):
            raise AssertionError("initial source states must be part of add_source")

    context = SimpleNamespace(
        uses_authoritative_projection=True,
        project_commands=Commands(),
        runtime_context=object(),
    )
    coordinator = ParseCoordinator(SimpleNamespace(context=context))

    restored = coordinator._commit_authoritative_source(
        "D:/mods/Plugin.esp",
        collection,
        format_id="plugin.sse",
        expected_authority=(("project", "variant"), 1, 3),
    )

    payload = captured["options"]["__transbridge_initial_entry_states_v1"]
    assert payload == [{"local_key": "entry", "translation": "导入译文", "stage": 3}]
    assert captured["kwargs"]["expected_project_revision"] == 1
    assert next(iter(restored)).translation == "导入译文"


def test_migration_draft_is_non_blocking_and_owned_until_finished(monkeypatch) -> None:
    class Signal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

        def emit(self, *args) -> None:
            for callback in tuple(self.callbacks):
                callback(*args)

    class Dialog:
        accepted = Signal()
        finished = Signal()

        def __init__(self, *, mode, parent) -> None:
            self.mode = mode
            self.parent = parent
            self.modal = None
            self.visible = False

        def setModal(self, value: bool) -> None:
            self.modal = value

        def show(self) -> None:
            self.visible = True

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

    host = SimpleNamespace(
        context=SimpleNamespace(active_slot=object()),
        show_message=lambda _message: None,
    )
    monkeypatch.setattr("transbridge.ui.workbench._parse_config_dialog.ParseConfigDialog", Dialog)
    coordinator = ParseCoordinator(host)

    dialog = coordinator.apply_migration()

    assert dialog.modal is False
    assert dialog.visible is True
    assert dialog in coordinator._owned_dialogs
    dialog.finished.emit(0)
    assert dialog not in coordinator._owned_dialogs


def test_project_dialog_receives_real_window_host(monkeypatch) -> None:
    observed = []
    host = SimpleNamespace(context=SimpleNamespace(active_project=object(), variant_store=object()))
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QInputDialog.getText",
        lambda parent, *_args: (observed.append(parent) or "", False),
    )

    ProjectTransferCoordinator(host).save_snapshot()

    assert observed == [host]
