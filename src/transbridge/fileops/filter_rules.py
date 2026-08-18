"""Policy-driven FOMOD resource classification and filtering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
import json
from pathlib import PurePosixPath

RESOURCE_FILTER_POLICY_VERSION = "fomod-resource-v2"

# Binary game data is excluded by the default distribution policy. Images are
# intentionally absent: their role depends on location and XML references.
STRIP_ASSETS = {".bsa", ".dds", ".nif", ".wav", ".fuz", ".xwm", ".ogg"}
KEEP_SCRIPTS = {".pex", ".psc"}
KEEP_ESSENTIAL = {".esp", ".esm", ".esl", ".xml"}

_PLUGIN_EXTENSIONS = frozenset({".esp", ".esm", ".esl"})
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tga"})
_GAME_DATA_DIRECTORIES = frozenset({"textures", "meshes", "sound", "music", "video", "interface", "scripts", "strings"})


PRESETS = {
    "常规 mod": {"keep": set(KEEP_ESSENTIAL), "strip": set(STRIP_ASSETS)},
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


class ResourceRole(StrEnum):
    FOMOD_UI = "fomod-ui"
    PLUGIN = "plugin"
    DATA = "game-data"
    UNKNOWN = "unknown"


class FilterAction(StrEnum):
    KEEP = "keep"
    STRIP = "strip"


@dataclass(frozen=True, slots=True)
class FilterDecision:
    path: str
    role: ResourceRole
    action: FilterAction
    reason: str
    policy_version: str = RESOURCE_FILTER_POLICY_VERSION

    def __post_init__(self) -> None:
        if not self.path or not self.reason or not self.policy_version:
            raise ValueError("filter decision path, reason and policy version must not be empty")

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "role": self.role.value,
            "action": self.action.value,
            "reason": self.reason,
            "policy_version": self.policy_version,
        }


@dataclass
class FilterRules:
    keep_exts: set[str] = field(default_factory=lambda: set(PRESETS[DEFAULT_PRESET]["keep"]))
    strip_exts: set[str] = field(default_factory=lambda: set(PRESETS[DEFAULT_PRESET]["strip"]))
    dir_rules: dict = field(default_factory=dict)

    @classmethod
    def from_preset(cls, preset: str) -> FilterRules:
        selected = PRESETS.get(preset, PRESETS[DEFAULT_PRESET])
        return cls(keep_exts=set(selected["keep"]), strip_exts=set(selected["strip"]))

    @classmethod
    def from_json(cls, path: str) -> FilterRules:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        keep = set(data.get("keep", PRESETS[DEFAULT_PRESET]["keep"]))
        strip = set(data.get("strip", PRESETS[DEFAULT_PRESET]["strip"]))
        return cls(keep_exts=keep, strip_exts=strip, dir_rules=data.get("dir_rules", {}))

    def _effective(self, rel_path: str) -> tuple[set[str], set[str], str | None]:
        normalized = _normalize_path(rel_path)
        comparable = normalized.casefold()
        matched = None
        for prefix, rule in self.dir_rules.items():
            normalized_prefix = _normalize_path(prefix).rstrip("/")
            comparable_prefix = normalized_prefix.casefold()
            if comparable == comparable_prefix or comparable.startswith(f"{comparable_prefix}/"):
                if matched is None or len(normalized_prefix) > len(matched[0]):
                    matched = (normalized_prefix, rule)
        if matched:
            rule = matched[1]
            return set(rule.get("keep", [])), set(rule.get("strip", [])), matched[0]
        return self.keep_exts, self.strip_exts, None


class ResourceRoleClassifier:
    """Classify resources using directory semantics and the FOMOD reference graph."""

    def __init__(self, references: Iterable[str] = ()) -> None:
        self._references = frozenset(_normalize_path(item).casefold() for item in references)

    def classify(self, path: str) -> ResourceRole:
        normalized = _normalize_path(path)
        parts = tuple(part.casefold() for part in PurePosixPath(normalized).parts)
        suffix = PurePosixPath(normalized).suffix.casefold()
        if normalized.casefold() in self._references or (parts and parts[0] == "fomod"):
            return ResourceRole.FOMOD_UI
        if suffix in _PLUGIN_EXTENSIONS:
            return ResourceRole.PLUGIN
        if parts and parts[0] in _GAME_DATA_DIRECTORIES:
            return ResourceRole.DATA
        if suffix in STRIP_ASSETS and suffix not in _IMAGE_EXTENSIONS:
            return ResourceRole.DATA
        return ResourceRole.UNKNOWN


def classify_files(
    files: Iterable[str],
    rules: FilterRules,
    *,
    references: Iterable[str] = (),
) -> tuple[FilterDecision, ...]:
    classifier = ResourceRoleClassifier(references)
    decisions: list[FilterDecision] = []
    for path in files:
        normalized = _normalize_path(path)
        suffix = PurePosixPath(normalized).suffix.casefold()
        role = classifier.classify(normalized)
        keep, strip, directory_rule = rules._effective(normalized)
        if directory_rule is not None and suffix in keep:
            action, reason = FilterAction.KEEP, f"directory-rule:{directory_rule}:keep"
        elif directory_rule is not None and suffix in strip:
            action, reason = FilterAction.STRIP, f"directory-rule:{directory_rule}:strip"
        elif role is ResourceRole.FOMOD_UI:
            action, reason = FilterAction.KEEP, "fomod-reference-or-directory"
        elif role is ResourceRole.PLUGIN:
            action, reason = FilterAction.KEEP, "plugin-essential"
        elif role is ResourceRole.DATA and (suffix in strip or suffix in _IMAGE_EXTENSIONS):
            action, reason = FilterAction.STRIP, "game-data-policy"
        elif suffix in keep:
            action, reason = FilterAction.KEEP, "extension-keep-policy"
        elif suffix in strip and suffix not in _IMAGE_EXTENSIONS:
            action, reason = FilterAction.STRIP, "extension-strip-policy"
        else:
            action, reason = FilterAction.KEEP, "unknown-default-keep"
        decisions.append(FilterDecision(normalized, role, action, reason))
    return tuple(decisions)


def filter_files(
    files: Iterable[str],
    rules: FilterRules,
    *,
    references: Iterable[str] = (),
) -> tuple[list[str], list[str]]:
    decisions = classify_files(files, rules, references=references)
    kept = [item.path for item in decisions if item.action is FilterAction.KEEP]
    stripped = [item.path for item in decisions if item.action is FilterAction.STRIP]
    return kept, stripped


def _normalize_path(path: str) -> str:
    value = str(path).replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.strip("/")
