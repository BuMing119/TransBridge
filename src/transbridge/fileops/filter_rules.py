"""资源过滤规则引擎：可配置扩展名保留/剔除清单 + 预设套。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# 侵权资源扩展名（BSA/贴图/模型/声音）
STRIP_ASSETS = {".bsa", ".dds", ".png", ".jpg", ".jpeg", ".nif",
                ".wav", ".fuz", ".xwm", ".tga", ".bmp", ".ogg"}
# 可翻译脚本
KEEP_SCRIPTS = {".pex", ".psc"}
# 插件 + fomod 元数据
KEEP_ESSENTIAL = {".esp", ".esm", ".esl", ".xml"}


# 预设套：面向产出的过滤规则，供 GUI 下拉选择
PRESETS = {
    "常规 mod": {
        "keep": set(KEEP_ESSENTIAL),
        "strip": set(STRIP_ASSETS),
    },
    "含脚本 mod": {
        "keep": set(KEEP_ESSENTIAL) | set(KEEP_SCRIPTS),
        "strip": set(STRIP_ASSETS),
    },
    "仅插件（最小）": {
        "keep": {".esp", ".esm", ".esl"},
        "strip": set(STRIP_ASSETS) | set(KEEP_SCRIPTS) | {".xml"},
    },
}

DEFAULT_PRESET = "常规 mod"


@dataclass
class FilterRules:
    keep_exts: set = field(default_factory=lambda: set(PRESETS[DEFAULT_PRESET]["keep"]))
    strip_exts: set = field(default_factory=lambda: set(PRESETS[DEFAULT_PRESET]["strip"]))
    dir_rules: dict = field(default_factory=dict)

    @classmethod
    def from_preset(cls, preset: str) -> "FilterRules":
        """按预设名创建规则。未知预设回退默认。"""
        p = PRESETS.get(preset)
        if p is None:
            p = PRESETS[DEFAULT_PRESET]
        return cls(keep_exts=set(p["keep"]), strip_exts=set(p["strip"]))

    @classmethod
    def from_json(cls, path: str) -> "FilterRules":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        keep = set(data.get("keep", PRESETS[DEFAULT_PRESET]["keep"]))
        strip = set(data.get("strip", PRESETS[DEFAULT_PRESET]["strip"]))
        return cls(keep_exts=keep, strip_exts=strip, dir_rules=data.get("dir_rules", {}))

    def _effective(self, rel_path: str):
        p = rel_path.replace("\\", "/")
        matched = None
        for prefix, rule in self.dir_rules.items():
            np_ = prefix.replace("\\", "/").rstrip("/")
            if p.startswith(np_ + "/") or p == np_:
                if matched is None or len(np_) > len(matched[0]):
                    matched = (np_, rule)
        if matched:
            r = matched[1]
            return set(r.get("keep", [])), set(r.get("strip", []))
        return self.keep_exts, self.strip_exts


def filter_files(files, rules: FilterRules):
    """按规则分类文件，返回 (kept, stripped)。目录级规则优先于全局。"""
    kept = []
    stripped = []
    for f in files:
        ext = Path(f).suffix.lower()
        keep, strip = rules._effective(f)
        if ext in strip and ext not in keep:
            stripped.append(f)
        else:
            kept.append(f)
    return kept, stripped