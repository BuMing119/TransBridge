"""Manual native function-calling regression test for the smart assistant.

Usage:
    uv run python tests/smart_assistant/test_data/run_llm_regression.py
    uv run python tests/smart_assistant/test_data/run_llm_regression.py --limit 0

The harness uses the configured OpenAI-compatible or Anthropic provider through
TransBridge's provider-neutral LLM client. It never executes business tools: it
only returns real ``get_tool_help`` results so the model can load a namespace,
then records the native tool calls selected by the model.

Output: terminal comparison + tests/smart_assistant/test_data/result.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_llm_config():
    """Load the production LLM configuration without exposing credentials."""
    from transbridge.config.llm import LLMConfig

    return LLMConfig.load_from_file()


def build_prompt() -> str:
    """Build the production system prompt after registering every tool namespace."""
    from transbridge.smart_assistant.prompts import build_system_prompt
    from transbridge.smart_assistant.tools import register_all

    register_all()
    return build_system_prompt(context="")


def _tool_result_message(
    call,
    result: dict[str, Any],
    *,
    is_error: bool = False,
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": json.dumps(result, ensure_ascii=False, default=str),
        "is_error": is_error,
    }


def _namespace_for_tool(tool_name: str) -> str | None:
    from transbridge.smart_assistant.tool_registry import ToolRegistry

    for namespace, specs in ToolRegistry.list_all_namespaces().items():
        if any(spec.name == tool_name for spec in specs):
            return namespace
    return None


def _requested_namespaces(arguments: dict[str, Any]) -> tuple[str, ...]:
    raw_namespace = str(arguments.get("namespace") or "")
    namespaces = [part.strip() for part in raw_namespace.split(",") if part.strip()]
    if not namespaces:
        tool_name = str(arguments.get("tool") or "").strip()
        inferred = _namespace_for_tool(tool_name) if tool_name else None
        if inferred:
            namespaces.append(inferred)
    return tuple(dict.fromkeys(namespaces))


def collect_native_tool_calls(
    client,
    system_prompt: str,
    user_message: str,
    *,
    max_tokens: int,
    max_rounds: int = 3,
) -> list:
    """Collect native calls while servicing discovery calls without side effects."""
    from transbridge.smart_assistant.native_tools import build_native_tool_definitions, turn_to_parsed_response
    from transbridge.smart_assistant.tool_registry import ToolRegistry

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    loaded_namespaces: list[str] = []
    observed_calls: list = []

    for _round in range(max_rounds):
        tools = build_native_tool_definitions(loaded_namespaces)
        turn = client.chat_stream_with_tools(messages, max_tokens, tools, lambda _chunk: None)
        turn_to_parsed_response(turn)
        observed_calls.extend(turn.tool_calls)
        if not turn.tool_calls:
            break

        messages.append(turn.to_assistant_message())
        help_calls = [call for call in turn.tool_calls if call.name == "get_tool_help"]
        business_calls = [call for call in turn.tool_calls if call.name != "get_tool_help"]

        for call in help_calls:
            namespaces = _requested_namespaces(call.arguments)
            for namespace in namespaces:
                if namespace not in loaded_namespaces:
                    loaded_namespaces.append(namespace)
            help_text = ToolRegistry.build_tool_help(
                tool=str(call.arguments.get("tool") or "").strip() or None,
                namespace=str(call.arguments.get("namespace") or "").strip() or None,
            )
            messages.append(_tool_result_message(call, {"success": True, "help": help_text}))

        if business_calls:
            for call in business_calls:
                messages.append(
                    _tool_result_message(
                        call,
                        {
                            "success": False,
                            "message": "Regression harness records business calls but does not execute them.",
                        },
                        is_error=True,
                    )
                )
            break

        if not help_calls:
            break

    return observed_calls


def infer_first_namespace(calls: list) -> str | None:
    """Infer the first routed namespace from native discovery, plan, or tool calls."""
    for call in calls:
        if call.name == "get_tool_help":
            namespaces = _requested_namespaces(call.arguments)
            return namespaces[0] if namespaces else None
        if call.name == "propose_plan":
            steps = call.arguments.get("steps")
            if isinstance(steps, list) and steps and isinstance(steps[0], dict):
                return _namespace_for_tool(str(steps[0].get("tool") or ""))
        namespace = _namespace_for_tool(call.name)
        if namespace:
            return namespace
    return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=15, help="Prompt count; use 0 to run all prompts")
    parser.add_argument("--max-rounds", type=int, default=3, help="Maximum provider rounds per prompt")
    return parser.parse_args()


def main() -> int:
    from transbridge.infra.llm_client import create_llm_client
    from transbridge.smart_assistant.native_tools import CORE_TOOL_NAMES

    args = _parse_args()
    config = load_llm_config()
    if not config.api_key:
        print("错误: 未配置 LLM CredentialRef 对应的安全凭据")
        return 1

    system_prompt = build_prompt()
    client = create_llm_client(config)
    max_tokens = int(config.max_output_tokens or 0)
    if config.provider == "anthropic" and max_tokens <= 0:
        max_tokens = 4096

    prompts_file = Path(__file__).parent / "prompts.json"
    with prompts_file.open("r", encoding="utf-8") as stream:
        prompts = json.load(stream)["prompts"]
    sample = prompts if args.limit <= 0 else prompts[: args.limit]

    print(f"Test config: provider={config.provider}, model={config.model}, prompts={len(sample)}")
    print(f"System prompt token estimate: {len(system_prompt) // 4}")
    print("=" * 80)

    results: list[dict[str, Any]] = []
    skip_count = 0
    discovery_required_count = 0

    for prompt in sample:
        prompt_id = prompt["id"]
        expected_tool = prompt.get("expect_tool")
        expected_namespace = prompt.get("expect_first_ns")
        try:
            calls = collect_native_tool_calls(
                client,
                system_prompt,
                prompt["text"],
                max_tokens=max_tokens,
                max_rounds=max(args.max_rounds, 1),
            )
        except Exception as exc:
            print(f"  [{prompt_id:02d}] ERR: {exc}")
            results.append({"id": prompt_id, "error": str(exc)})
            continue

        tool_names = [call.name for call in calls]
        called_help = "get_tool_help" in tool_names
        routed_namespace = infer_first_namespace(calls)
        if expected_tool:
            matched = expected_tool in tool_names
            discovery_required = expected_tool not in CORE_TOOL_NAMES
        else:
            matched = routed_namespace == expected_namespace
            discovery_required = bool(expected_namespace)

        if discovery_required:
            discovery_required_count += 1
            if not called_help:
                skip_count += 1

        status = "OK" if matched else ("??" if not tool_names else "XX")
        expected = expected_tool or f"namespace:{expected_namespace}"
        print(
            f"  [{prompt_id:02d}] {status} | ns={prompt['namespace']:12s} | "
            f"expect={expected:24s} | got={str(tool_names):45s} | "
            f"route={routed_namespace or '-':12s} | help={'Y' if called_help else 'N'}"
        )
        results.append({
            "id": prompt_id,
            "text": prompt["text"],
            "namespace": prompt["namespace"],
            "expect_tool": expected_tool,
            "expect_first_ns": expected_namespace,
            "got_tools": tool_names,
            "routed_namespace": routed_namespace,
            "called_get_tool_help": called_help,
            "match": matched,
        })

    successful = [result for result in results if "error" not in result]
    total = len(successful)
    correct = sum(1 for result in successful if result.get("match"))
    accuracy = correct / max(total, 1) * 100
    skip_rate = skip_count / max(discovery_required_count, 1) * 100

    print("\n" + "=" * 80)
    print(f"工具/路由准确率: {correct}/{total} ({accuracy:.0f}%)")
    print(f"跳过率 (需要发现但未调 get_tool_help): {skip_count}/{discovery_required_count} ({skip_rate:.0f}%)")
    print("目标: 准确率 >=90%, 跳过率 <5%")
    passed = total > 0 and accuracy >= 90 and skip_rate < 5
    print("PASS" if passed else "WARN: needs tuning")

    out_file = Path(__file__).parent / "result.json"
    with out_file.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "summary": {
                    "total": total,
                    "correct": correct,
                    "accuracy": f"{accuracy:.0f}%",
                    "skip_rate": f"{skip_rate:.0f}%",
                    "skip_count": skip_count,
                    "discovery_required_count": discovery_required_count,
                },
                "details": results,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
    print(f"详细结果: {out_file}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
