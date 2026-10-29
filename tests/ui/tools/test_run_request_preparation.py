from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import get_ident
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime
from transbridge.application.contracts import JobRef
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.ui.tools.ai_translator import run_controller, run_request_preparation
from transbridge.ui.tools.ai_translator.run_controller import RunAlreadyActiveError, RunController
from transbridge.ui.tools.ai_translator.run_request_preparation import (
    capture_terminology_owner,
    prepare_run_request,
)


def config():
    return SimpleNamespace(max_concurrent=3, model="frozen-model", api_key="secret")


def prepared(controller, **updates):
    options = dict(project_id="project", variant_id="variant", esp_path="source.esp", run_id="ai-captured")
    options.update(updates)
    return prepare_run_request(
        controller.owner_id,
        "translate",
        config(),
        (SimpleNamespace(id="one", key="one", original="Sword"),),
        **options,
    )


def admit(controller, value, **updates):
    options = dict(project_id="project", variant_id="variant", esp_path="source.esp", run_id="ai-captured")
    options.update(updates)
    return controller.begin("translate", None, value.entries, prepared=value, **options)


def test_capture_and_background_preparation_never_read_live_owner(monkeypatch):
    gui = get_ident()
    factory_threads, hash_threads = [], []

    class Factory:
        def effective_adapter(self, project, variant):
            factory_threads.append(get_ident())
            return SimpleNamespace(
                effective_snapshot=lambda _: EffectiveTerminologySnapshot(
                    project, variant, EffectiveSnapshotStatus.READY, "v1", "digest"
                )
            )

        def freeze_echo_links(self, _):
            factory_threads.append(get_ident())
            return None

    factory = Factory()

    class GuiOwner:
        @property
        def active_version_identity(self):
            assert get_ident() == gui
            return "project", "variant"

        @property
        def effective_terminology_factory(self):
            assert get_ident() == gui
            return factory

        @property
        def terminology_echo_links_factory(self):
            assert get_ident() == gui
            return factory

    frozen_owner = capture_terminology_owner(GuiOwner())
    assert not factory_threads
    original = run_request_preparation.build_run_spec

    def build(**kwargs):
        hash_threads.append(get_ident())
        return original(**kwargs)

    monkeypatch.setattr(run_request_preparation, "build_run_spec", build)
    controller = RunController("owner", task_runtime=ai_task_runtime())
    with ThreadPoolExecutor(max_workers=1) as pool:
        value = pool.submit(
            prepare_run_request,
            controller.owner_id,
            "translate",
            config(),
            (SimpleNamespace(id="one", key="one", original="Sword"),),
            project_id="project",
            variant_id="variant",
            esp_path="source.esp",
            terminology_owner=frozen_owner,
        ).result(timeout=5)
    assert len(factory_threads) == 2 and all(thread != gui for thread in factory_threads)
    assert len(hash_threads) == 1 and hash_threads[0] != gui
    assert value.terminology_binding.snapshot_ref.version_id == "v1"


def test_prepared_admission_does_not_rebuild_or_refreeze(monkeypatch):
    controller = RunController("owner", task_runtime=ai_task_runtime())
    value = prepared(controller)

    def forbidden(*args, **kwargs):
        pytest.fail("GUI admission must not repeat background preparation")

    monkeypatch.setattr(run_controller, "build_run_spec", forbidden)
    monkeypatch.setattr(run_controller, "FrozenExecutionConfig", forbidden)
    monkeypatch.setattr(run_request_preparation, "freeze_project_terminology", forbidden)
    request = admit(controller, value)
    assert request.entries is value.entries
    assert request.spec.input_fingerprint == value.spec.input_fingerprint
    assert request.run_id == request.runtime_ref.run_id
    assert request.config.model == "frozen-model"
    request.config.model = "edited copy"
    assert request.config.model == "frozen-model"
    assert request.request_budget.max_in_flight == 3


@pytest.mark.parametrize("change", ["owner", "project", "variant", "run_id", "revision", "entries"])
def test_prepared_admission_rejects_mismatched_scope(change):
    controller = RunController("owner", task_runtime=ai_task_runtime())
    value = prepared(controller)
    options = {}
    if change == "owner":
        controller = RunController("other-owner", task_runtime=ai_task_runtime())
    elif change == "entries":
        with pytest.raises(ValueError, match="同一份"):
            controller.begin(
                "translate",
                None,
                list(value.entries),
                prepared=value,
                project_id="project",
                variant_id="variant",
                esp_path="source.esp",
            )
        return
    else:
        key = {
            "project": "project_id",
            "variant": "variant_id",
            "revision": "project_revision",
        }.get(change, change)
        options[key] = 2 if change == "revision" else "changed"
    with pytest.raises(ValueError, match="不匹配"):
        admit(controller, value, **options)
    assert controller.active_request is None


@pytest.mark.parametrize("reference", [None, "", "different-reference.esp"])
def test_prepared_admission_ignores_external_reference_changes(reference):
    controller = RunController("owner", task_runtime=ai_task_runtime())
    value = prepared(controller)

    request = admit(controller, value, esp_path=reference)

    assert request.spec.input_ref == value.spec.input_ref
    assert request.spec.input_fingerprint == value.spec.input_fingerprint
    controller.close()


def test_prepared_runtime_admission_keeps_gui_ownership_and_reuses_fingerprints(monkeypatch):
    gui = get_ident()

    class Runtime:
        def submit(self, spec, owner):
            assert get_ident() == gui
            self.submitted = spec, owner
            return SimpleNamespace(ref=JobRef(job_id="runtime-job", owner_id=owner.owner_id, run_id="runtime-run"))

        def start(self, ref, owner):
            assert get_ident() == gui
            self.started = ref, owner

    runtime = Runtime()
    controller = RunController("owner", task_runtime=runtime)
    value = prepared(controller)
    monkeypatch.setattr(run_controller, "build_run_spec", lambda **_: pytest.fail("must reuse prepared fingerprint"))
    first = admit(controller, value)
    assert first.run_id == "runtime-run" and first.generation == 1
    assert first.spec == replace(value.spec, run_id="runtime-run")
    with pytest.raises(RunAlreadyActiveError):
        admit(controller, value)
    controller.finish(first.run_id)
    second = admit(controller, value)
    assert second.generation == 2
    assert second.request_budget is not first.request_budget
    controller.finish(second.run_id)
    controller.close()
    with pytest.raises(RuntimeError, match="closed"):
        admit(controller, value)


def test_preparation_rejects_live_owner_or_other_terminology_scope():
    controller = RunController("owner", task_runtime=ai_task_runtime())
    live = SimpleNamespace(active_version_identity=("other", "variant"), effective_terminology_factory=None)
    with pytest.raises(TypeError, match="live UI"):
        prepared(controller, terminology_owner=live)
    with pytest.raises(ValueError, match="Project/Variant"):
        prepared(controller, terminology_owner=capture_terminology_owner(live))
