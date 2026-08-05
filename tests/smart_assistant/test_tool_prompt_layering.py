"""Story 04: 工具提示词分层加载 — 单元测试。"""
import pytest
from src.transbridge.smart_assistant.tool_registry import (
    ToolSpec, ToolRegistry, _levenshtein_distance,
)


@pytest.fixture(scope="module", autouse=True)
def _ensure_tools_registered():
    """确保所有工具已注册（模块级，只执行一次）。"""
    from src.transbridge.smart_assistant.tools import register_all
    register_all()


# ── Levenshtein distance ──────────────────────────────────────

def test_levenshtein_identical():
    assert _levenshtein_distance("hello", "hello") == 0


def test_levenshtein_one_edit():
    assert _levenshtein_distance("hello", "hallo") == 1


def test_levenshtein_empty():
    assert _levenshtein_distance("", "abc") == 3
    assert _levenshtein_distance("abc", "") == 3


def test_levenshtein_large_diff():
    assert _levenshtein_distance("start_translation", "start_translatin") == 1  # o→i
    assert _levenshtein_distance("get_app_state", "get_app_stats") == 1  # e→s
    assert _levenshtein_distance("hello", "hxllo") == 1


# ── ToolSpec.summary 自动提取 ────────────────────────────────

def test_summary_auto_extract_from_description():
    spec = ToolSpec(
        name="test", display_name="测试",
        description="①状态概览②参数: 无③返回: data",
        parameters={},
    )
    assert spec.summary == "状态概览"


def test_summary_no_marker_keeps_empty():
    spec = ToolSpec(
        name="test", display_name="测试",
        description="普通描述，无①②标记",
        parameters={},
    )
    assert spec.summary == ""


def test_summary_only_1_marker():
    spec = ToolSpec(
        name="test", display_name="测试",
        description="①功能描述",
        parameters={},
    )
    assert spec.summary == "功能描述"


def test_summary_truncated_at_80_chars():
    long_text = "①" + "A" * 100
    spec = ToolSpec(
        name="test", display_name="测试",
        description=long_text,
        parameters={},
    )
    assert len(spec.summary) <= 80


def test_summary_manual_preserved():
    spec = ToolSpec(
        name="test", display_name="测试",
        description="①应该被忽略②因为手动填写了",
        summary="手动填写的摘要",
        parameters={},
    )
    assert spec.summary == "手动填写的摘要"


# ── build_tool_directory ─────────────────────────────────────

def test_build_tool_directory_returns_string():
    result = ToolRegistry.build_tool_directory()
    assert isinstance(result, str)
    assert "可用工具目录" in result


def test_build_tool_directory_excludes_deprecated():
    # 注册一个 deprecated 工具验证它不出现在目录中
    ToolRegistry.register(ToolSpec(
        name="_test_deprecated", display_name="测试废弃",
        description="①临时测试。②无参数。",
        parameters={}, deprecated=True,
    ), namespace="default")
    result = ToolRegistry.build_tool_directory()
    assert "_test_deprecated" not in result


def test_build_tool_directory_default_first():
    result = ToolRegistry.build_tool_directory()
    first_ns_line = [l for l in result.split("\n") if l.startswith("[")][0]
    assert first_ns_line.startswith("[default]")


# ── build_tool_help ──────────────────────────────────────────

def test_build_tool_help_single_tool():
    result = ToolRegistry.build_tool_help(tool="get_app_state")
    assert "get_app_state" in result
    assert "| 参数 | 类型 | 必填 | 说明 |" not in result  # 无参数工具不显示表格


def test_build_tool_help_namespace():
    result = ToolRegistry.build_tool_help(namespace="default")
    assert "get_app_state" in result
    assert "get_statistics" in result


def test_build_tool_help_overview():
    result = ToolRegistry.build_tool_help()
    assert "工具概览" in result
    assert "default" in result


def test_build_tool_help_nonexistent_tool():
    result = ToolRegistry.build_tool_help(tool="nonexistent_tool_xyz")
    assert "未找到" in result


def test_build_tool_help_fuzzy_match():
    result = ToolRegistry.build_tool_help(tool="get_app_states")
    assert "get_app_state" in result  # 距离 2，应被匹配


def test_build_tool_help_multi_namespace():
    result = ToolRegistry.build_tool_help(namespace="default,translator")
    assert "default" in result
    assert "translator" in result


def test_build_tool_help_nonexistent_namespace():
    result = ToolRegistry.build_tool_help(namespace="nonexistent")
    assert "不存在" in result


def test_build_tool_help_with_params_shows_table():
    result = ToolRegistry.build_tool_help(tool="switch_collection")
    assert "| 参数 | 类型 | 必填 | 说明 |" in result


# ── build_system_prompt 新结构 ───────────────────────────────

def test_build_system_prompt_has_layered_structure():
    from src.transbridge.smart_assistant.prompts import build_system_prompt
    prompt = build_system_prompt(context="test")
    assert "核心工具" in prompt
    assert "工具发现" in prompt
    assert "工具路由" in prompt
    assert "可用工具目录" in prompt


def test_build_system_prompt_no_old_guide():
    from src.transbridge.smart_assistant.prompts import build_system_prompt
    prompt = build_system_prompt()
    assert "工具选择指南" not in prompt
    assert "易混淆工具对" not in prompt


def test_build_system_prompt_has_preloaded():
    from src.transbridge.smart_assistant.prompts import build_system_prompt
    prompt = build_system_prompt()
    assert "get_app_state" in prompt
    assert "get_statistics" in prompt


def test_build_system_prompt_has_routing_table():
    from src.transbridge.smart_assistant.prompts import build_system_prompt
    prompt = build_system_prompt()
    assert "用户意图关键词" in prompt
    assert "加载命名空间" in prompt
    for ns in ["default", "translator", "parser", "editor", "paratranz", "proofreader", "writer"]:
        assert ns in prompt, f"路由表应包含 namespace: {ns}"


# ── get_tool_help 工具 ───────────────────────────────────────

def test_get_tool_help_registered():
    spec = ToolRegistry.get("get_tool_help")
    assert spec is not None
    assert spec.permission == "read"
    assert spec.execute is not None


def test_get_tool_help_execute_single_tool():
    from src.transbridge.smart_assistant.tools.tool_default import _tool_get_tool_help
    result = _tool_get_tool_help({"tool": "get_app_state"}, None)
    assert "get_app_state" in result.data["help"]


def test_get_tool_help_execute_namespace():
    from src.transbridge.smart_assistant.tools.tool_default import _tool_get_tool_help
    result = _tool_get_tool_help({"namespace": "default"}, None)
    assert "get_app_state" in result.data["help"]


def test_get_tool_help_execute_empty():
    from src.transbridge.smart_assistant.tools.tool_default import _tool_get_tool_help
    result = _tool_get_tool_help({}, None)
    assert "工具概览" in result.data["help"]
