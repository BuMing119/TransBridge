import json
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLabel, QPushButton, QWidget
import pytest

from tests.smart_assistant.tools.test_source_input_prerequisites import saved_context as saved_context_fixture
from tests.ui.test_on_demand_source_inputs import _open_without_artifact
from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as plugin_window_fixture
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until as wait
from transbridge.application.assistant_requests.models import ItemKind, RequestItem, UserRequest
from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools.types import ToolResult
from transbridge.ui.coordinators.source_input_coordinator import SourceInputCoordinator
from transbridge.ui.source_supplement_dialog import SourceSupplementDialog
from transbridge.ui.tools.smart_assistant import panel as panel_module
from transbridge.ui.tools.smart_assistant.request_confirmation import renew_confirmation
from transbridge.ui.tools.smart_assistant.source_input_binding import AssistantSourceInputBinding
from transbridge.ui.tools.smart_assistant.tool_card import ToolCard

shared_plugin_window = plugin_window_fixture
saved_context = saved_context_fixture


def test_template_list_choice_reproposes_only_confirmed_ready_sources(saved_context, monkeypatch):
    facade = QWidget()
    facade._ctx = saved_context
    facade._request_binding = None
    saved_context.slots["source-ready"] = SimpleNamespace(
        collection=saved_context.collection, source_snapshot=object(), label="Ready.esp"
    )
    opened, proposals = [], []
    saved_context.source_input_coordinator = SimpleNamespace(
        resolved_keys=lambda keys: tuple(keys),
        resolved_entry_keys=lambda keys: tuple(keys),
        entry_mappings=dict,
        ensure=lambda *args, **kwargs: opened.append((args, kwargs)),
    )
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.source_input_binding.ensure_execution", lambda *_args: True
    )
    action = AssistantSourceInputBinding(facade)
    monkeypatch.setattr(action, "_repropose", lambda intent, step, *_args: proposals.append((intent, step)))
    intent = {
        "requirement": "template",
        "source_keys": ["source-ready", "source-saved"],
        "entry_keys": [entry.identity.to_dict() for entry in saved_context.collection],
        "project_id": "project",
        "variant_id": "variant",
    }
    original = {"tool": "write_back", "args": {"source_keys": list(intent["source_keys"]), "output_dir": "chosen"}}
    try:
        action._ensure(intent, original, None, QLabel(facade), QPushButton(facade), lambda exc: pytest.fail(str(exc)))
        assert opened[0][1]["allow_partial"] and not proposals
        assert "chosen" in opened[0][1]["operation_label"]
        opened[0][1]["on_scope_ready"](("source-ready",))
        assert len(proposals) == 1
        assert proposals[0][1]["args"]["source_keys"] == ["source-ready"]
        assert proposals[0][0]["_source_scope"] == {
            "requested_source_keys": ["source-ready", "source-saved"],
            "selected_source_keys": ["source-ready"],
            "resolved_source_keys": ["source-ready"],
        }
        assert original["args"]["source_keys"] == ["source-ready", "source-saved"]
        assert saved_context.slots["source-saved"].source_snapshot is None
    finally:
        facade.close()


def test_busy_repair_keeps_the_action_available_for_retry(saved_context):
    facade = QWidget()
    facade._ctx = saved_context
    facade._request_binding = None
    source = SourceInputCoordinator(SimpleNamespace(context=saved_context))
    source._busy = True
    saved_context.source_input_coordinator = source
    action = AssistantSourceInputBinding(facade)
    label, button = QLabel(facade), QPushButton(facade)
    button.setEnabled(False)
    entry = saved_context.collection.get("unknown")
    action._ensure(
        {
            "entry_keys": [entry.identity.to_dict()],
            "requirement": "originals",
            "project_id": "project",
            "variant_id": "variant",
        },
        {"tool": "start_translation", "args": {}},
        None,
        label,
        button,
        lambda exc: pytest.fail(str(exc)),
    )
    assert button.isEnabled()
    facade.close()


def test_known_selected_targets_do_not_repair_an_unselected_unknown_sibling(saved_context, monkeypatch):
    facade = QWidget()
    facade._ctx = saved_context
    known = next(iter(saved_context.collection))
    saved_context.source_input_coordinator = SimpleNamespace(
        resolved_entry_keys=lambda keys: keys,
        entry_mappings=dict,
        ensure=lambda *_args, **_kwargs: pytest.fail("Unselected unknown original must not expand repair scope"),
    )
    action = AssistantSourceInputBinding(facade)
    prepared = []
    monkeypatch.setattr(action, "_repropose", lambda _intent, step, *_args: prepared.append(step))
    action._ensure(
        {"entry_keys": [known.identity.to_dict()], "requirement": "originals"},
        {"tool": "start_translation", "args": {}},
        None,
        QLabel(facade),
        QPushButton(facade),
        lambda exc: pytest.fail(str(exc)),
    )
    assert prepared[0]["args"]["entry_ids"] == [known.identity.serialize()]
    facade.close()


@pytest.mark.parametrize("delivery", ["normal", "capture_before", "capture_after", "close"])
def test_real_assistant_repairs_legacy_source_and_requires_a_new_scoped_confirmation(
    shared_plugin_window, monkeypatch, delivery
):
    case = shared_plugin_window
    window = case.window
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    variant = json.loads(variant_path.read_bytes())
    variant["data"]["entries"][0]["entry_key"] = {"namespace": "legacy:v1", "local_key": "legacy-request-row"}
    orphan = {**variant["data"]["entries"][0], "translation": "Unselected saved translation"}
    orphan["entry_key"] = {"namespace": "legacy:v1", "local_key": "unselected-legacy-row"}
    variant["data"]["entries"].append(orphan)
    variant["data"]["source_fingerprints"] = [{"namespace": "legacy:v1", "sha256": None}]
    variant_path.write_text(json.dumps(variant), encoding="utf-8")
    _open_without_artifact(case)
    old_entry = next(iter(window.context.collection))
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: case.root / "assistant")
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: LLMConfig(api_key="offline-test", model="offline"))
    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.chat_widget.QSettings",
        lambda *_: SimpleNamespace(value=lambda _key, default, **_kwargs: default),
    )
    monkeypatch.setattr(panel_module.SmartAssistantPanel, "_init_skills", lambda _self: None)
    monkeypatch.setattr(
        panel_module.SmartAssistantPanel,
        "_configured_service_availability",
        staticmethod(lambda: SimpleNamespace(model_name="offline", ready=True, message="", missing_fields=())),
    )
    monkeypatch.setattr(panel_module, "set_window_app_user_model_id", lambda *_: False)
    panel = window.tool_windows.get_assistant_panel()
    _until(lambda: panel.chat.session_ready)
    if not panel._workspace.ready():
        panel._sessions.create("Repair selected input")
    _until(lambda: panel._workspace.ready())
    chat = panel.chat
    monkeypatch.setattr(chat._confirmation_view, "ask_permission", lambda *_: True)
    binding = chat._request_binding
    _until(lambda: binding.context is not None and binding._accepting == 0)
    binding._user_stopped = True
    chat._conversation.load_tool_namespaces(("translator",))
    model_rounds = []
    monkeypatch.setattr(chat._orchestrator, "start_round", lambda: model_rounds.append("round"))
    launched = []
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.tool_translator.launch_translation_task",
        lambda _ctx, _collection, targets, _config, **_kwargs: (
            launched.append(tuple(entry.identity for entry in targets)) or ToolResult.ok("Prepared offline", data={})
        ),
    )
    context = binding.context
    service = binding.service
    source = service.accept_input(
        context,
        "Polish the selected saved row",
        selection={
            "active_version_identity": list(window.context.active_version_identity),
            "selected_entry_ids": [old_entry.id],
            "filter_state": {},
            "translation_scope": {},
        },
    )
    request = UserRequest(
        "repair-request",
        context.session_id,
        "Polish selected row",
        (RequestItem("polish", "Polish selected row", ItemKind.EXECUTION),),
        scope=(("project_id", context.project_id), ("variant_id", context.variant_id)),
        source_message_ids=(source["message_id"],),
        work_round_id=source["message_id"],
    )

    def install(state):
        state["requests"] = [request.to_dict()]
        state["ingress"][0]["status"] = "applied"

    service.transact(context, install)
    selected = service.scheduler.select_next_turn(
        context.session_id,
        binding.view_id,
        (request,),
        allowed_tools=("start_translation",),
        automatic=False,
    )
    assert selected is not None
    binding.admission = selected.admission
    binding._set_gate()
    step = {"id": 1, "tool": "start_translation", "args": {"mode": "polish", "entry_ids": [old_entry.id]}}
    parsed = renew_confirmation({"mode": "react", "steps": [step]}, chat._conversation)
    chat._controller.restore_confirmation(parsed)
    binding.confirmations.capture(parsed)
    cards = chat.findChildren(ToolCard)
    original = cards[-1]
    _until(lambda: original._exec_btn.isEnabled() and binding._accepting == 0)
    original._exec_btn.click()
    _until(lambda: any(button.text() == "补充输入并重新准备" for button in chat.findChildren(QPushButton)))
    assert launched == []
    waiting = next(item for item in service.requests(service.state(context)) if item.request_id == request.request_id)
    assert waiting.items[0].waiting_reasons == ("source_input_required",)
    assert waiting.effects[0].status == "failed"
    repair = next(button for button in chat.findChildren(QPushButton) if button.text() == "补充输入并重新准备")
    monkeypatch.setattr(QInputDialog, "getItem", lambda _p, _t, _l, items, *_: (items[0], True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: ("", ""))
    repair.click()
    _until(lambda: repair.isEnabled() and binding._accepting == 0)
    assert window.context.active_slot.source_snapshot is None
    assert next(iter(window.context.collection)).translation == "Project A translation"
    assert window.workbench.isEnabled() and launched == []

    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(case.plugin), ""))

    def mapped(dialog):
        dialog.selector.setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SourceSupplementDialog, "exec", mapped)
    if delivery.startswith("capture_"):
        capture = binding.confirmations.capture
        failures = []

        def fail_once(*args, **kwargs):
            if not failures:
                failures.append(True)
                if delivery == "capture_after":
                    capture(*args, **kwargs)
                raise OSError("injected confirmation save failure")
            return capture(*args, **kwargs)

        monkeypatch.setattr(binding.confirmations, "capture", fail_once)
    if delivery == "close":
        submit = binding.background.submit

        def close_on_delivery(work, received=None, **kwargs):
            if work.__name__ == "prepare":

                def close(selected):
                    panel.dispose()
                    received(selected)

                return submit(work, close, **kwargs)
            return submit(work, received, **kwargs)

        monkeypatch.setattr(binding.background, "submit", close_on_delivery)
    repair.click()
    if delivery == "close":
        _until(lambda: panel.is_disposed and binding._accepting == 0)
        assert launched == []
        from transbridge.application.assistant_requests.source_input_selection import readmit_source_input_confirmation

        latest = next(
            item for item in service.requests(service.state(context)) if item.request_id == request.request_id
        )
        service.scheduler.activate(context.session_id, "retry-after-close")
        selected = readmit_source_input_confirmation(
            service,
            context,
            latest.request_id,
            latest.revision,
            latest.lease_epoch,
            ("polish",),
            view_id="retry-after-close",
            allowed_tools=("start_translation",),
        )
        service.scheduler.release(selected.admission)
        return
    if delivery.startswith("capture_"):
        wait(
            lambda: repair.isEnabled() and binding._accepting == 0 and bool(failures),
            diagnostic=lambda: [label.text() for label in chat.findChildren(QLabel)],
        )
        assert launched == []
        repair.click()
    expected_cards = len(cards) + (2 if delivery.startswith("capture_") else 1)
    wait(
        lambda: len(chat.findChildren(ToolCard)) == expected_cards,
        diagnostic=lambda: (
            [label.text() for label in chat.findChildren(QLabel)],
            binding.context,
            context,
            binding._active,
            panel._workspace.ready(),
            window.source_input_coordinator._busy,
        ),
    )
    fresh = chat.findChildren(ToolCard)[-1]
    _until(lambda: fresh._exec_btn.isEnabled() and binding._accepting == 0)
    assert fresh._step["tool_call_id"] != original._step["tool_call_id"]
    assert not original._exec_btn.isEnabled() and launched == []
    assert next(iter(window.context.collection)).translation == "Project A translation"
    assert window.context.active_slot.source_snapshot is not None
    retained = next(slot for slot in window.context.slots.values() if slot.source_snapshot is None)
    assert next(iter(retained.collection)).translation == "Unselected saved translation"
    fresh._exec_btn.click()
    _until(lambda: len(launched) == 1)
    assert launched[0] == (next(iter(window.context.collection)).identity,)
    assert launched[0][0] != old_entry.identity
    _until(lambda: binding._accepting == 0)
    after = next(item for item in service.requests(service.state(context)) if item.request_id == request.request_id)
    assert [effect.status for effect in after.effects] == ["failed", "succeeded"], [
        effect.receipt for effect in after.effects
    ]
    assert after.items[0].status == "satisfied"
    assert service.state(context)["ingress"][0]["selection"]["selected_entry_ids"] == [old_entry.id]
