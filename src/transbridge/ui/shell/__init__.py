"""Public shell API with lazy imports at the composition boundary."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "ActionAvailability": "action_catalog",
    "ActionCatalog": "action_catalog",
    "ActionDescriptor": "action_catalog",
    "ActionSection": "action_catalog",
    "DEFAULT_ACTION_CATALOG": "action_catalog",
    "DangerLevel": "action_catalog",
    "IntentId": "action_catalog",
    "IntentPlacement": "action_catalog",
    "CommandActivation": "command_palette",
    "CommandCandidateKind": "command_palette",
    "CommandIntentRequest": "command_palette",
    "CommandPaletteController": "command_palette",
    "CommandPaletteModel": "command_palette",
    "DynamicCommandCandidate": "command_palette",
    "CommandPaletteDialog": "command_palette_qt",
    "ContextHelpController": "context_help",
    "DEFAULT_CONTEXT_HELP": "context_help",
    "ContextHelpPanel": "context_help_qt",
    "IntentDispatchResult": "intent_router",
    "IntentRouter": "intent_router",
    "ShellIntentComposition": "intent_composition",
    "MenuBuilder": "menu_builder",
    "MenuCallbacks": "menu_builder",
    "MenuHandles": "menu_builder",
    "StatusPresenter": "status_presenter",
    "RecentProjectViewState": "start_center",
    "RecoveryItemViewState": "start_center",
    "StartCenterViewState": "start_center",
    "StartCenterWidget": "start_center",
    "StartDestinationState": "start_center",
    "StartCenterController": "start_center_controller",
    "ToolWindows": "tool_windows",
    "TaskCenterController": "task_center",
    "TaskCenterPanel": "task_center",
    "AutoSaveManager": "window_lifecycle",
    "WindowLifecycle": "window_lifecycle",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_EXPORTS))
