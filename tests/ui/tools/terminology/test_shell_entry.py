from transbridge.application.projects.source_registry import legacy_source_role, select_workbench_source
from transbridge.ui.shell.action_catalog import DEFAULT_ACTION_CATALOG, IntentId


def test_schema_v3_workbench_source_uses_nested_legacy_facade_without_duplicate_role() -> None:
    translation = {
        "source_id": "translation",
        "enabled": True,
        "format_id": "xml.eet",
        "location": "D:/translation.xml",
        "kind": "bilingual",
        "bilingual_capability": "self_contained",
        "format_options": {},
        "legacy": {"role": "migration"},
    }
    plugin = {
        "source_id": "plugin",
        "enabled": True,
        "format_id": "plugin.sse",
        "location": "D:/Main.esm",
        "kind": "plugin",
        "bilingual_capability": "none",
        "format_options": {},
        "legacy": {"role": "primary"},
    }

    assert "role" not in plugin
    assert legacy_source_role(plugin) == "primary"
    assert select_workbench_source((translation, plugin)) is plugin


def test_terminology_intent_is_discoverable_in_the_shared_catalog() -> None:
    descriptor = DEFAULT_ACTION_CATALOG.get(IntentId.TERMINOLOGY_WORKBENCH)

    assert descriptor.label == "构建术语库…"
    assert "术语工作台" in descriptor.aliases
