from __future__ import annotations

import json
import re

from transbridge.smart_assistant.agents.agent_registry import AgentRegistry
from transbridge.smart_assistant.context_builder import ContextBuilder
from transbridge.smart_assistant.native_tools import build_native_tool_definitions
from transbridge.smart_assistant.prompts import build_system_prompt
from transbridge.smart_assistant.reflexion.retry_handler import RetryHandler
from transbridge.smart_assistant.skills.skill_executor import SkillExecutor
from transbridge.smart_assistant.skills.skill_loader import SkillSpec
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all


_HAN = re.compile(r"[\u3400-\u9fff]")


def _assert_english(value: str) -> None:
    assert _HAN.search(value) is None, value


def _schema_descriptions(value) -> list[str]:
    descriptions: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "description" and isinstance(item, str):
                descriptions.append(item)
            else:
                descriptions.extend(_schema_descriptions(item))
    elif isinstance(value, list):
        for item in value:
            descriptions.extend(_schema_descriptions(item))
    return descriptions


def test_built_system_prompt_and_static_context_are_english() -> None:
    register_all()

    _assert_english(build_system_prompt(ContextBuilder().build()))


def test_native_tool_descriptions_and_parameter_descriptions_are_english() -> None:
    register_all()

    namespaces = tuple(ToolRegistry.list_all_namespaces())
    definitions = build_native_tool_definitions(namespaces)
    for definition in definitions:
        _assert_english(definition.description)
        for description in _schema_descriptions(definition.input_schema):
            _assert_english(description)


def test_builtin_agent_instructions_are_english() -> None:
    register_all()

    for agent in AgentRegistry.list_all():
        _assert_english(agent.role)
        _assert_english(agent.system_prompt)


def test_reflexion_prompt_is_english() -> None:
    class Client:
        prompt = ""

        def chat(self, messages, max_tokens):
            del max_tokens
            self.prompt = messages[0]["content"]
            return json.dumps({"retry": False, "adjusted_args": {}, "reason": "invalid input"})

    client = Client()
    RetryHandler(client).analyze_and_adjust({"tool": "sample", "args": {}}, "failed", 0)
    _assert_english(client.prompt)


def test_skill_model_messages_use_stable_english_name_not_localized_display_name() -> None:
    class Chat:
        system_prompt = ""
        user_message = ""

        def add_system_message(self, _message: str) -> None:
            pass

        def add_system_prompt(self, message: str) -> None:
            self.system_prompt = message

        def send_user_message(self, message: str) -> None:
            self.user_message = message

    chat = Chat()
    SkillExecutor(chat).execute(
        SkillSpec(
            name="translate_with_terms",
            display_name="术语辅助翻译",
            description="Translate with approved terminology.",
            prompt_template="Follow the glossary.",
        )
    )

    _assert_english(chat.system_prompt)
    _assert_english(chat.user_message)
