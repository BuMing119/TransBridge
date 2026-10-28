"""The default production upload entry never enters string sync preflight."""

from types import SimpleNamespace

import pytest

from tests.ui.operations.test_production_facade import _runtime, _Signal
from transbridge.application.contracts import RequestContext
from transbridge.ui.operations import production
from transbridge.ui.operations.plan_view import OperationKind


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


@pytest.mark.parametrize("custom_dialog", [False, True])
@pytest.mark.parametrize("operation", ["upload", "download"])
def test_explicit_targets_always_use_current_transfer_launcher(monkeypatch, custom_dialog, operation):
    calls = []
    context = object()
    runtime = _runtime()
    values = {"paratranz_project_id": 42, "paratranz_project_name": "Explicit"}

    def upload(ctx, *, batch=False, **options):
        calls.append((ctx, batch, options))
        return SimpleNamespace(destroyed=_Signal())

    def download(ctx, actual_runtime, *, values):
        assert actual_runtime is runtime
        calls.append((ctx, False, values))
        return SimpleNamespace(destroyed=_Signal())

    monkeypatch.setattr(production, "open_file_upload", upload)
    monkeypatch.setattr(production, "open_batch_download", download)
    options = (
        {"dialog_factory": lambda *_args: pytest.fail("Transfer entered the old plan flow")} if custom_dialog else {}
    )
    facade = production.build_operation_plan_facade(runtime, RequestContext("explicit-target"), **options)
    window = getattr(facade, f"begin_{operation}")(context, **values)
    assert calls == [(context, False, values)]
    assert set(facade._features) == {OperationKind.WRITE, OperationKind.FOMOD}
    assert facade.active_plan_count == 1
    window.destroyed.emit()
    assert facade.active_plan_count == 0
