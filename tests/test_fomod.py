"""fomod 流水线后端测试：fomod_xml + builder + pipeline（精简）。"""

from __future__ import annotations

from pathlib import Path
import shutil
import uuid

import pytest

from transbridge.config.language_profiles import LanguageProfile
from transbridge.fileops import FilterRules
from transbridge.fomod import fomod_xml as fomod_xml_module
from transbridge.fomod.builder import assemble_output
from transbridge.fomod.fomod_xml import read_fomod_xml, translate_module_config, write_fomod_xml


@pytest.fixture
def workdir():
    base = Path(__file__).resolve().parent.parent / ".tmp_tests"
    base.mkdir(exist_ok=True)
    d = base / f"fomod_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _sample_xml():
    return (
        '<?xml version="1.0" encoding="utf-16"?>'
        "<config><moduleName>Test Mod</moduleName>"
        '<installSteps><installStep name="step1">'
        '<optionalFileGroups><group name="g1">'
        '<plugins><plugin name="A.esp"><description>Hello World</description></plugin>'
        "</plugins></group></optionalFileGroups></installStep></installSteps></config>"
    )


def test_fomod_xml_utf16le_roundtrip(workdir):
    """UTF-16LE BOM 写回 + 读回往返一致。"""
    p = workdir / "ModuleConfig.xml"
    content = "test-content-中文"
    write_fomod_xml(str(p), content)
    # BOM 校验
    raw = p.read_bytes()
    assert raw[:2] == b"\xff\xfe"
    # 读回
    read = read_fomod_xml(str(p))
    assert read == content


def test_translate_module_config_reuse_old(workdir):
    """层级键复用：旧版同 key 文本被复用。"""

    class FakeLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            self.calls += 1
            return "新译文"

    old = _sample_xml().replace("Test Mod", "测试 Mod").replace("Hello World", "你好世界")
    new = _sample_xml()
    llm = FakeLLM()
    out = translate_module_config(new, old, llm)
    # 旧版文本被复用
    assert "测试 Mod" in out
    assert "你好世界" in out
    # 无新增文本，AI 不应被调用
    assert llm.calls == 0


@pytest.mark.parametrize(
    ("target_lang", "language_name"),
    [
        ("zh_CN", "Simplified Chinese"),
        ("en", "English"),
        ("zh_TW", "Traditional Chinese"),
        ("ja", "Japanese"),
    ],
)
def test_translate_module_config_no_old(workdir, target_lang, language_name, monkeypatch):
    """无旧版 → 全部走 AI 翻译。"""

    monkeypatch.setattr(
        fomod_xml_module,
        "load_language_profile",
        lambda locale: LanguageProfile(locale, language_name, "English", language_name),
    )

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat(self, messages):
            self.calls.append(messages)
            return "AI译"

    llm = FakeLLM()
    out = translate_module_config(_sample_xml(), None, llm, target_lang=target_lang)
    expected_prefix = (
        "You are a Skyrim mod localization assistant. "
        f"Translate the following FOMOD installer UI text into {language_name}. "
        "Return only the translation, without explanations, quotation marks, or any additional text:\n\n"
    )
    assert llm.calls == [
        [{"role": "user", "content": expected_prefix + "Test Mod"}],
        [{"role": "user", "content": expected_prefix + "Hello World"}],
    ]
    assert "AI译" in out


def test_assemble_output_filter(workdir):
    """组装：过滤侵权资源，保留脚本插件。"""
    src = workdir / "src"
    (src / "fomod").mkdir(parents=True)
    (src / "fomod" / "ModuleConfig.xml").write_text("<c/>", encoding="utf-8")
    (src / "A.esp").write_bytes(b"ESP")
    (src / "patch.pex").write_bytes(b"PEX")
    (src / "textures").mkdir(parents=True)
    (src / "textures" / "armor.dds").write_bytes(b"DDS")

    dest = workdir / "out"
    res = assemble_output(str(src), str(dest), FilterRules())
    assert res["stripped_count"] >= 1  # dds 被剔除
    assert (dest / "A.esp").exists()
    assert (dest / "patch.pex").exists()
    assert not (dest / "textures" / "armor.dds").exists()
