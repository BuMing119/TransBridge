from types import SimpleNamespace

import pytest

from transbridge.ui.coordinators import profile_variant_save as save_module
from transbridge.ui.coordinators.profile_variant_save import ProfileVariantSaveCoordinator


def _host(*, dirty=False, preview=True, targets=()):
    calls, messages = [], []
    projection = SimpleNamespace(
        project_id="project",
        variant_id="A",
        profile=SimpleNamespace(name="B"),
        source_profile=SimpleNamespace(name="A"),
        base_snapshot=None,
    )
    controller = SimpleNamespace(
        refresh_pending=False, state=SimpleNamespace(is_preview=preview), capture_write_projection=lambda: projection
    )
    context = SimpleNamespace(
        active_version_identity=("project", "A"),
        dirty=dirty,
        project_variants=(),
        project_revision=2,
        variant_revision=3,
    )
    success = SimpleNamespace(is_success=True)

    def save(name, request, **kwargs):
        calls.append(("save", name, request, kwargs))
        return success

    commands = SimpleNamespace(
        profile_variant_targets=lambda *_: SimpleNamespace(is_success=True, value=targets), save_profile_variant=save
    )

    def run(operation, **kwargs):
        calls.append(("worker", kwargs["message"]))
        kwargs["on_result"](operation())

    def save_source(*, on_finished):
        calls.append(("save-source",))
        on_finished(True)

    host = SimpleNamespace(
        workbench=SimpleNamespace(terminology_profile_controller=controller),
        context=context,
        runtime_context="request",
        project_commands=commands,
        start_foreground_task=run,
        save_current_project_async=save_source,
        show_message=messages.append,
        project_coordinator=SimpleNamespace(restore_active_sources=lambda: calls.append(("reload",))),
    )
    return host, calls, messages, projection


def test_plain_save_and_loading_do_not_create_preview_variant():
    host, calls, messages, _ = _host(preview=False)
    coordinator = ProfileVariantSaveCoordinator(host)
    assert not coordinator.request()
    host.workbench.terminology_profile_controller.refresh_pending = True
    assert coordinator.request()
    assert calls == []
    assert "正在读取" in messages[-1]


def test_new_preview_save_first_saves_dirty_source_then_commits_source_and_target(monkeypatch):
    host, calls, messages, projection = _host(dirty=True)
    monkeypatch.setattr(save_module.QInputDialog, "getText", lambda *args, **kwargs: ("独立 B", True))
    assert ProfileVariantSaveCoordinator(host).request()
    assert calls[0] == ("save-source",)
    saved = next(item for item in calls if item[0] == "save")
    assert saved[1:3] == ("独立 B", "request")
    assert saved[3] == dict(
        target_profile=projection.profile,
        source_profile=projection.source_profile,
        base_snapshot=None,
        target_variant_id=None,
        expected_target_revision=None,
        expected_project_revision=2,
        expected_variant_revision=3,
    )
    assert calls[-1] == ("reload",)
    assert "独立编辑" in messages[-1]


@pytest.mark.parametrize("choice", ["new", "overwrite", "cancel"])
def test_existing_b_requires_explicit_choice_and_passes_target_revision(monkeypatch, choice):
    target = dict(id="existing-b", name="已有 B", revision=7)
    host, calls, _, _ = _host(targets=(target,))

    def choose(*args):
        choices, default = args[3:5]
        assert choices[0] == "另存为新的翻译版本" and default == 0
        return (choices[1] if choice == "overwrite" else choices[0], choice != "cancel")

    monkeypatch.setattr(save_module.QInputDialog, "getItem", choose)
    monkeypatch.setattr(save_module.QInputDialog, "getText", lambda *args, **kwargs: ("新 B", True))
    monkeypatch.setattr(save_module.QMessageBox, "question", lambda *args: save_module.QMessageBox.StandardButton.Yes)
    assert ProfileVariantSaveCoordinator(host).request()
    saves = [item for item in calls if item[0] == "save"]
    if choice == "cancel":
        assert saves == [] and ("reload",) not in calls
    else:
        assert len(saves) == 1
        assert saves[0][3]["target_variant_id"] == ("existing-b" if choice == "overwrite" else None)
        assert saves[0][3]["expected_target_revision"] == (7 if choice == "overwrite" else None)


def test_cancel_new_name_does_not_write_or_activate(monkeypatch):
    host, calls, _, _ = _host()
    monkeypatch.setattr(save_module.QInputDialog, "getText", lambda *args, **kwargs: ("", False))
    assert ProfileVariantSaveCoordinator(host).request()
    assert not any(item[0] in {"save", "reload"} for item in calls)


def test_dialog_time_revision_changes_do_not_rebase_the_captured_save_request(monkeypatch):
    host, calls, _, _ = _host()

    def rename(*args, **kwargs):
        host.context.project_revision = 20
        host.context.variant_revision = 30
        return "B", True

    monkeypatch.setattr(save_module.QInputDialog, "getText", rename)
    assert ProfileVariantSaveCoordinator(host).request()
    saved = next(item for item in calls if item[0] == "save")
    assert saved[3]["expected_project_revision"] == 2
    assert saved[3]["expected_variant_revision"] == 3
