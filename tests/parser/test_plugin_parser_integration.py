import os
import pytest
from pathlib import Path

from src.transbridge.parser.plugin_parser import PluginParser
from src.transbridge.converter.translation_entry import TranslationEntry


def _esp_path() -> Path:
    # 方式 A：默认 tests/data/sample.esp
    default = Path(__file__).parent / "data" / "sample.esp"

    # 方式 B：允许通过环境变量指定真实路径
    env = os.environ.get("TRANSBRIDGE_TEST_ESP")
    return Path(env) if env else default


@pytest.mark.integration
def test_parse_real_esp_smoke():
    esp = _esp_path()
    if not esp.exists():
        pytest.skip(f"Real esp not found: {esp} (set TRANSBRIDGE_TEST_ESP to run)")

    parser = PluginParser()

    progress_calls = []
    def progress_callback(current: int, total: int, desc: str):
        progress_calls.append((current, total, desc))

    items = parser.parse_plugin(esp, progress_callback=progress_callback, skip_empty=True)

    # 1) 基本断言：能解析出列表
    assert isinstance(items, list)
    assert all(isinstance(x, TranslationEntry) for x in items)

    # 2) 至少有一些结果（如果你的文件确实含文本）
    assert len(items) > 0

    # 3) 关键字段不变量：id/key/original 格式
    for it in items[:200]:  # 只抽样前 200 条，避免太慢
        assert isinstance(it.id, str) and ":" in it.id          # editor_id:form_id
        assert isinstance(it.key, str) and len(it.key) > 0
        assert it.translation == ""
        assert it.stage == 0
        # 因为 skip_empty=True，这里 original 应该非空且 strip 后非空
        assert isinstance(it.original, str) and it.original.strip()

    # 4) progress_callback 行为：总次数应等于 extract 出来的总条目数
    # 由于 parser 是对每条 plugin string 都调用 callback（即便后续被 skip）
    assert len(progress_calls) > 0
    assert progress_calls[-1][0] == progress_calls[-1][1]  # last: current == total

    # 5) getter 状态
    assert parser.get_source_path() == esp
    assert parser.get_plugin() is not None
