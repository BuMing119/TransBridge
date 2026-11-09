from types import SimpleNamespace

from PyQt6.QtWidgets import QLabel, QPushButton, QWidget
import pytest

from tests.smart_assistant.tools.test_source_input_prerequisites import saved_context as saved_context_fixture
from transbridge.ui.coordinators.source_input_coordinator import SourceInputCoordinator
from transbridge.ui.tools.smart_assistant.source_input_binding import AssistantSourceInputBinding

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
