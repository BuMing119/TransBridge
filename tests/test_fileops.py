"""fileops 通用文件操作工具测试。

覆盖：归档 zip 解包/打包往返、目录 diff、资源过滤规则。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from src.transbridge.fileops import (
    extract, pack,
    diff_directories,
    FilterRules, filter_files, PRESETS, DEFAULT_PRESET,
)


@pytest.fixture
def workdir():
    """在工作区创建独立工作目录（DSH 沙箱仅允许写工作区），测试后清理。"""
    import uuid
    base = Path(__file__).resolve().parent.parent / ".tmp_tests"
    base.mkdir(exist_ok=True)
    d = base / f"fileops_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def sample_dir(workdir):
    src = workdir / "src"
    (src / "fomod").mkdir(parents=True)
    (src / "fomod" / "ModuleConfig.xml").write_text("<config/>", encoding="utf-8")
    (src / "fomod" / "info.xml").write_text("<info/>", encoding="utf-8")
    (src / "Dragonborn.esp").write_bytes(b"ESP-PLACEHOLDER")
    (src / "textures").mkdir()
    (src / "textures" / "armor.dds").write_bytes(b"DDSPLACEHOLDER")
    return src


def test_zip_roundtrip(sample_dir, workdir):
    archive = workdir / "out.zip"
    pack(str(sample_dir), str(archive), fmt="zip")
    assert archive.exists()

    dest = workdir / "dest"
    result = extract(str(archive), str(dest))
    assert result["extracted_count"] == 4
    assert (dest / "fomod" / "ModuleConfig.xml").exists()
    assert (dest / "Dragonborn.esp").exists()
    assert (dest / "textures" / "armor.dds").exists()


def test_zip_selective_extract(sample_dir, workdir):
    archive = workdir / "out.zip"
    pack(str(sample_dir), str(archive), fmt="zip")
    dest = workdir / "dest"
    result = extract(str(archive), str(dest), files=["Dragonborn.esp", "fomod/ModuleConfig.xml"])
    assert result["extracted_count"] == 2
    assert (dest / "Dragonborn.esp").exists()
    assert (dest / "fomod" / "ModuleConfig.xml").exists()
    assert not (dest / "textures" / "armor.dds").exists()


def test_extract_unsupported_format(workdir):
    bad = workdir / "x.bin"
    bad.write_bytes(b"not-an-archive")
    with pytest.raises(ValueError):
        extract(str(bad), str(workdir / "d"))


def test_diff_directories(workdir):
    old = workdir / "old"
    new = workdir / "new"
    (old / "fomod").mkdir(parents=True)
    (new / "fomod").mkdir(parents=True)
    (old / "fomod" / "ModuleConfig.xml").write_text("v1", encoding="utf-8")
    (new / "fomod" / "ModuleConfig.xml").write_text("v2", encoding="utf-8")
    (old / "A.esp").write_bytes(b"same")
    (new / "A.esp").write_bytes(b"same")
    (old / "Removed.esp").write_bytes(b"x")
    (new / "Added.esp").write_bytes(b"y")

    result = diff_directories(str(old), str(new))
    assert "Removed.esp" in result.removed
    assert "Added.esp" in result.added
    assert "fomod\\ModuleConfig.xml" in result.changed or "fomod/ModuleConfig.xml" in result.changed
    assert "A.esp" in result.unchanged


def test_diff_skip_hash(workdir):
    old = workdir / "old"
    new = workdir / "new"
    old.mkdir(); new.mkdir()
    (old / "big.bsa").write_bytes(b"aaaa")
    (new / "big.bsa").write_bytes(b"bbbb")
    result = diff_directories(str(old), str(new), skip_hash_exts={".bsa"})
    assert "big.bsa" in result.unchanged
    assert "big.bsa" not in result.changed


def test_filter_rules_default():
    rules = FilterRules()
    files = ["A.esp", "patch.pex", "fomod/ModuleConfig.xml", "textures/armor.dds", "meshes/x.nif"]
    kept, stripped = filter_files(files, rules)
    assert "A.esp" in kept
    assert "patch.pex" in kept
    assert "fomod/ModuleConfig.xml" in kept
    assert "textures/armor.dds" in stripped
    assert "meshes/x.nif" in stripped


def test_filter_rules_dir_override():
    rules = FilterRules(dir_rules={"fomod": {"keep": [".png"], "strip": []}})
    files = ["fomod/screenshot.png", "textures/armor.png"]
    kept, stripped = filter_files(files, rules)
    assert "fomod/screenshot.png" in kept
    assert "textures/armor.png" in stripped


def test_filter_presets():
    """预设套：常规/含脚本/仅插件，扩展名集合不同。"""
    assert DEFAULT_PRESET in PRESETS
    # 常规 mod：剔脚本
    r1 = FilterRules.from_preset("常规 mod")
    assert ".esp" in r1.keep_exts
    assert ".pex" not in r1.keep_exts
    # 含脚本 mod：保留脚本
    r2 = FilterRules.from_preset("含脚本 mod")
    assert ".pex" in r2.keep_exts
    assert ".psc" in r2.keep_exts
    # 仅插件：剔除脚本 + xml
    r3 = FilterRules.from_preset("仅插件（最小）")
    assert ".esp" in r3.keep_exts
    assert ".pex" not in r3.keep_exts
    assert ".xml" not in r3.keep_exts
