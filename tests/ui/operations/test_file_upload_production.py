"""The default production upload entry never enters string sync preflight."""

from types import SimpleNamespace

from tests.ui.operations.test_production_facade import _runtime, _Signal
from transbridge.application.contracts import RequestContext
from transbridge.ui.operations import production


def test_default_production_uploads_launch_shared_file_ui_and_retain_windows(monkeypatch):
    calls = []

    def launch(context, *, batch):
        calls.append((context, batch))
        return SimpleNamespace(destroyed=_Signal())

    monkeypatch.setattr(production, "open_file_upload", launch)
    facade = production.build_operation_plan_facade(_runtime(), RequestContext("file-upload-test"))
    context = object()
    assert facade.supports("upload", context, batch=True)
    single = facade.begin_upload(context)
    batch = facade.begin_upload(context, batch=True)
    assert calls == [(context, False), (context, True)]
    assert facade.active_plan_count == 2
    single.destroyed.emit()
    batch.destroyed.emit()
    assert facade.active_plan_count == 0
