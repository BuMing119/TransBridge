from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.terminology.test_llm_extraction_workflow import _snapshot
from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.terminology.query_control import QueryCancelled
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets
from transbridge.ui.tools.terminology.extraction_options import ExtractionOptionsView
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "status,diagnostics,candidates,enabled",
    [
        ("skipped", ("LLM_DISABLED",), (), False),
        ("skipped", (), (), False),
        ("skipped", ("LLM_NO_TEXT",), (), True),
        ("performed", (), (SimpleNamespace(extraction_method="llm_text"),), True),
        ("partial", (), (), True),
    ],
)
def test_initial_persisted_mode_restores_once_without_signalling_user_changes(status, diagnostics, candidates, enabled):
    view = ExtractionOptionsView()
    changes = []
    view.changed.connect(lambda: changes.append(True))
    try:
        build = SimpleNamespace(llm_status=status, diagnostics=diagnostics, candidates=candidates)
        assert view.restore_from_build(build)
        assert view.configuration() == {"llm_enabled": enabled, "force_llm": False}
        assert not changes
        assert not view.restore_from_build(SimpleNamespace(llm_status="performed"))
        assert view.configuration()["llm_enabled"] is enabled
    finally:
        view.close()


@pytest.mark.parametrize("action", ["mode", "force", "build"])
def test_late_assets_do_not_override_a_user_choice(action):
    view = ExtractionOptionsView()
    try:
        if action == "mode":
            view.mode.setCurrentIndex(1)
            view.mode.setCurrentIndex(0)
        elif action == "force":
            view.force.setChecked(True)
        else:
            view.configuration(user_action=True)
        expected = view.configuration()
        assert not view.restore_from_build(SimpleNamespace(llm_status="skipped", diagnostics=("LLM_DISABLED",)))
        assert view.configuration() == expected
    finally:
        view.close()


def test_late_llm_preflight_cannot_overwrite_restored_rule_configuration_or_snapshot(monkeypatch):
    entered, release = Event(), Event()
    llm_snapshot = _snapshot()
    rules_snapshot = SimpleNamespace(**{**vars(llm_snapshot), "extraction": None})
    published = []

    def capture(_context, *, config):
        if config.get("llm_enabled"):
            entered.set()
            assert release.wait(5)
            return OperationResult.completed(llm_snapshot)
        return OperationResult.completed(rules_snapshot)

    presenter = TerminologyPresenter(
        TerminologyUiServices(
            build_inputs=SimpleNamespace(capture_build_input=capture),
            commands=SimpleNamespace(preview_publish=lambda _ctx, *, config: published.append(config)),
        ),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    presenter.set_source_scope(("chosen-source",))
    window = TerminologyWindow(presenter)
    queued, callbacks, refreshes = [], [], []
    monkeypatch.setattr(
        window._actions, "run", lambda call, ready, **_kwargs: queued.append(call) or callbacks.append(ready)
    )
    monkeypatch.setattr(window._asset_refresh, "request", lambda *_args: refreshes.append(True))
    monkeypatch.setattr(window._browsing, "refresh", lambda: None)
    monkeypatch.setattr(window, "bind_page", lambda *_args: None)
    try:
        window.run_preflight(repair=False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            old = pool.submit(queued.pop())
            try:
                assert entered.wait(5)
                assets = WorkbenchAssets(
                    build=SimpleNamespace(
                        completeness="full",
                        freshness="current",
                        llm_status="skipped",
                        diagnostics=("LLM_DISABLED",),
                        candidates=(),
                    ),
                    draft=SimpleNamespace(decisions=()),
                )
                window._asset_view.apply_shared(assets)
                assert window.build_view.extraction_options.configuration()["llm_enabled"] is False
                # Publication sees the restored choice immediately, even before
                # the newly scheduled source check has reached a worker.
                presenter.preview_publish()
                assert published[-1] == {
                    "source_ids": ["chosen-source"],
                    "llm_enabled": False,
                    "force_llm": False,
                }
                restored = queued.pop()()
                callbacks.pop()(restored)
                assert restored.extraction_label == "仅规则整理，不调用 LLM"
                assert presenter.snapshot is rules_snapshot
                assert not refreshes
            finally:
                release.set()
            with pytest.raises(QueryCancelled):
                old.result(timeout=5)
        assert presenter.snapshot is rules_snapshot
        window._asset_view.apply_shared(
            replace(assets, build=SimpleNamespace(**{**vars(assets.build), "llm_status": "performed"}))
        )
        assert not queued
    finally:
        release.set()
        window.close()
