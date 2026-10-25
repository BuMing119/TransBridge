from types import SimpleNamespace

from transbridge.ui.coordinators.operation_coordinator import OperationCoordinator


class Facade:
    def __init__(self) -> None:
        self.calls = []

    def begin_upload(self, context, *, batch=False):
        self.calls.append(("upload", context, batch))

    def begin_download(self, context, *, batch=False):
        self.calls.append(("download", context, batch))

    def begin_write(self, context, *, batch=False):
        self.calls.append(("write", context, batch))


class CapabilityFacade(Facade):
    def supports(self, kind, _context, *, batch=False):
        return not batch and kind != "write"


def test_upload_menu_uses_card_mode_selection_while_other_intents_use_plan_facade() -> None:
    legacy = []
    context = SimpleNamespace(
        collection=object(),
        current_project={"id": 7},
        slots={"one": object(), "two": object()},
    )
    facade = Facade()
    host = SimpleNamespace(
        context=context,
        operation_plan_facade=facade,
        upload_card=SimpleNamespace(
            upload=lambda: legacy.append("upload"), batch_upload=lambda: legacy.append("batch")
        ),
        download_card=SimpleNamespace(download=lambda: legacy.append("download")),
        write_card=SimpleNamespace(write=lambda: legacy.append("write")),
    )
    coordinator = OperationCoordinator(host)

    coordinator.upload()
    coordinator.batch_upload()
    coordinator.download()
    coordinator.write()

    assert [(kind, batch) for kind, _context, batch in facade.calls] == [
        ("download", False),
        ("write", False),
    ]
    assert legacy == ["upload", "batch"]


def test_old_download_alias_uses_single_intent_while_other_unmigrated_actions_keep_their_fallback() -> None:
    legacy = []
    context = SimpleNamespace(
        collection=object(),
        current_project={"id": 7},
        slots={"one": object(), "two": object()},
    )
    facade = CapabilityFacade()
    host = SimpleNamespace(
        context=context,
        operation_plan_facade=facade,
        upload_card=SimpleNamespace(
            upload=lambda: legacy.append("upload"), batch_upload=lambda: legacy.append("batch-upload")
        ),
        download_card=SimpleNamespace(
            download=lambda: legacy.append("download"),
            batch_download=lambda: legacy.append("batch-download"),
        ),
        write_card=SimpleNamespace(
            write=lambda: legacy.append("write"), batch_write=lambda: legacy.append("batch-write")
        ),
    )
    coordinator = OperationCoordinator(host)

    coordinator.batch_upload()
    coordinator.batch_download()
    coordinator.write()
    coordinator.batch_write()

    assert facade.calls == [("download", context, False)]
    assert legacy == ["batch-upload", "write", "batch-write"]
