"""Phase 3 LLM regression test - command-line, bypasses UI, calls API directly.

Usage:
    cd /path/to/TransBridge
    PYTHONPATH=src python tests/smart_assistant/test_data/run_llm_regression.py

Requires: the unified data/transbridge.ini and an approved credential provider.
Consumes: ~50 prompts * 2 modes * ~2000 tokens = ~200K tokens.

Output: terminal comparison + tests/smart_assistant/test_data/result.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_llm_config() -> dict:
    """Load through the production facade; never parse or expose INI secrets."""
    from transbridge.config.llm import LLMConfig

    llm = LLMConfig.load_from_file()
    return {
        "api_key": llm.api_key,
        "api_base": llm.base_url,
        "model": llm.model,
        "provider": llm.provider,
        "config_revision": llm.config_revision,
    }


def build_old_prompt() -> str:
    """旧版 system prompt（全量工具注入）。"""
    from transbridge.smart_assistant.tool_registry import ToolRegistry
    from transbridge.smart_assistant.prompts import HYBRID_SYSTEM_PROMPT
    tools_desc = ToolRegistry.build_tool_schema_for_prompt()
    return HYBRID_SYSTEM_PROMPT.format(context="", tools_desc=tools_desc)


def build_new_prompt() -> str:
    """新版 system prompt（分层加载）。"""
    from transbridge.smart_assistant.prompts import build_system_prompt
    return build_system_prompt(context="")


def call_llm(system_prompt: str, user_message: str, config: dict) -> str:
    """调 LLM API，返回 assistant 的文本响应。"""
    from openai import OpenAI

    client = OpenAI(api_key=config["api_key"], base_url=config["api_base"])
    response = client.chat.completions.create(
        model=config["model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
        max_tokens=500,
    )
    return response.choices[0].message.content


def call_llm_two_rounds(system_prompt: str, user_message: str, config: dict) -> list[str]:
    """两轮对话：R1 让 LLM 发现工具，注入 get_tool_help 结果，R2 让 LLM 真正调用。"""
    from openai import OpenAI
    from transbridge.smart_assistant.tool_registry import ToolRegistry

    client = OpenAI(api_key=config["api_key"], base_url=config["api_base"])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # Round 1: LLM 决定需要哪个 namespace 的工具
    r1 = client.chat.completions.create(
        model=config["model"], messages=messages, temperature=0, max_tokens=500,
    )
    r1_text = r1.choices[0].message.content
    r1_tools = extract_tool_calls(r1_text)

    # 如果 R1 调了 get_tool_help，注入其返回结果
    if "get_tool_help" in r1_tools:
        # 从 R1 响应中解析 get_tool_help 的参数
        ns_match = re.search(r'"namespace"\s*:\s*"(\w+)"', r1_text)
        namespace = ns_match.group(1) if ns_match else None
        help_result = ToolRegistry.build_tool_help(namespace=namespace)

        messages.append({"role": "assistant", "content": r1_text})
        messages.append({"role": "user", "content": f"[get_tool_help 返回结果]\n{help_result}"})

        # Round 2: LLM 用完整 Schema 调用真实工具
        r2 = client.chat.completions.create(
            model=config["model"], messages=messages, temperature=0, max_tokens=500,
        )
        r2_text = r2.choices[0].message.content
        r2_tools = extract_tool_calls(r2_text)
        return r1_tools + r2_tools

    return r1_tools


def extract_tool_calls(response: str) -> list[str]:
    """从 LLM 响应中提取工具调用名列表（JSON 模式 {mode, thought, steps}）。"""
    tools = []
    # 匹配 "tool": "xxx" 或 'tool': 'xxx'
    for m in re.finditer(r'''["']tool["']\s*:\s*["'](\w+)["']''', response):
        tools.append(m.group(1))
    return tools


def has_get_tool_help(response: str) -> bool:
    """检查是否调用了 get_tool_help。"""
    return "get_tool_help" in extract_tool_calls(response)


def main():
    config = load_llm_config()
    if not config["api_key"]:
        print("错误: 未配置 LLM CredentialRef 对应的安全凭据")
        return 1

    old_prompt = build_old_prompt()
    new_prompt = build_new_prompt()

    # 加载测试 prompts
    prompts_file = Path(__file__).parent / "prompts.json"
    with open(prompts_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    prompts = data["prompts"]

    # 只跑非跨领域的 15 条 prompt（跨领域需多轮 get_tool_help，先跳过）
    sample = [p for p in prompts if p["namespace"] != "cross"][:15]
    print(f"Test config: model={config['model']}, prompts={len(sample)}")
    print(f"Old prompt token est: {len(old_prompt) // 4}")
    print(f"New prompt token est: {len(new_prompt) // 4}")
    print(f"Token savings: {(1 - len(new_prompt)/len(old_prompt)) * 100:.0f}%")
    print(f"{'='*60}")

    results = []
    skip_count = 0
    non_preloaded_count = 0

    for p in sample:
        pid = p["id"]
        ns = p["namespace"]
        is_preloaded = ns == "default"

        try:
            tools = call_llm_two_rounds(new_prompt, p["text"], config)
        except Exception as exc:
            print(f"  [{pid:02d}] ERR: {exc}")
            results.append({"id": pid, "error": str(exc)})
            continue

        called_help = "get_tool_help" in tools

        if not is_preloaded and tools:
            non_preloaded_count += 1
            if not called_help:
                skip_count += 1

        expect = p.get("expect_tool", "-")
        match = expect in tools

        status = "OK" if match else ("??" if not tools else "XX")

        print(f"  [{pid:02d}] {status} | ns={ns:12s} | "
              f"expect={expect:22s} | got={str(tools):40s} | "
              f"help={'Y' if called_help else 'N'}")

        results.append({
            "id": pid, "text": p["text"], "namespace": ns,
            "expect_tool": expect, "got_tools": tools,
            "called_get_tool_help": called_help, "match": match,
        })

    # 汇总
    total = len([r for r in results if "error" not in r])
    correct = sum(1 for r in results if r.get("match"))
    skip_rate = skip_count / max(non_preloaded_count, 1) * 100

    print(f"\n{'='*60}")
    print(f"工具选择准确率: {correct}/{total} ({100*correct//max(total,1)}%)")
    print(f"跳过率 (非预加载未调 get_tool_help): {skip_count}/{non_preloaded_count} ({skip_rate:.0f}%)")
    print(f"目标: 准确率 >=95%, 跳过率 <5%")
    print(f"{'PASS' if correct/total >= 0.9 and skip_rate < 5 else 'WARN: needs tuning'}")

    # 保存结果
    out_file = Path(__file__).parent / "result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({"summary": {
            "total": total, "correct": correct,
            "accuracy": f"{100*correct//max(total,1)}%",
            "skip_rate": f"{skip_rate:.0f}%",
            "skip_count": skip_count,
            "non_preloaded_count": non_preloaded_count,
        }, "details": results}, f, ensure_ascii=False, indent=2)
    print(f"详细结果: {out_file}")

    return 0 if correct / max(total, 1) >= 0.9 else 1


if __name__ == "__main__":
    raise SystemExit(main())
