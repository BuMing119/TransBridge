"""Prove translation transfers against two plugin snapshots without editing a project."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.writer.plugin_source import capture_plugin_write_source

from .contracts import SourceSnapshot
from .plugin_write import _parser_from_snapshot
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation


@dataclass(frozen=True, slots=True)
class PluginTransferIssue:
    code: str
    key: str
    original: str
    message: str


@dataclass(frozen=True, slots=True)
class PluginTransferReport:
    source_count: int = 0
    matched_count: int = 0
    writable_count: int = 0
    preserved_count: int = 0
    issues: tuple[PluginTransferIssue, ...] = ()

    @property
    def summary(self) -> str:
        return (
            f"项目共 {self.source_count} 条；精确匹配 {self.matched_count} 条；"
            f"将套用 {self.writable_count} 条译文；目标保留 {self.preserved_count} 条；"
            f"提示 {len(self.issues)} 项。"
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "summary": self.summary}


def match_plugin_translations(
    source: tuple[TranslationEntry, ...],
    source_snapshot: SourceSnapshot,
    target: tuple[TranslationEntry, ...],
    target_snapshot: SourceSnapshot,
) -> tuple[tuple[TranslationEntry, ...], PluginTransferReport]:
    old = _physical_entries(source_snapshot)
    physical = _physical_entries(target_snapshot)
    old_by_locator = _index(old)
    source_by_locator = _index(source)
    physical_by_locator = _index(physical)
    target_by_locator = _index(target)
    target_keys = {entry.key for entry in physical}
    issues: list[PluginTransferIssue] = []
    proposals: dict[tuple[str, str], TranslationEntry] = {}
    matched = 0

    def issue(entry, code, message):
        issues.append(PluginTransferIssue(code, entry.key, entry.original, message))

    for entry in source:
        if entry.identity.namespace.value != dict(source_snapshot.metadata).get("source_namespace"):
            issue(entry, "TRANSFER_SOURCE_IDENTITY_MISMATCH", "词条不属于当前项目来源快照，未套用。")
            continue
        decision = DEFAULT_STAGE_POLICY.evaluate(
            entry.stage, entry.translation, StageOperation.PUBLISH, original=entry.original
        )
        if decision.blocks_publish:
            issue(entry, "TRANSFER_STAGE_BLOCKED", "词条阶段不允许发布，保留目标内容。")
            continue
        if not entry.translation or decision.publish_text == entry.original:
            issue(entry, "TRANSFER_NO_TRANSLATION", "没有可发布的新译文，保留目标内容。")
            continue
        locator = (entry.key, entry.original)
        if len(source_by_locator[locator]) != 1 or len(old_by_locator.get(locator, ())) != 1:
            issue(entry, "TRANSFER_SOURCE_AMBIGUOUS", "项目词条无法唯一对应旧来源记录，未套用。")
            continue
        candidates = target_by_locator.get(locator, ())
        physical_candidates = physical_by_locator.get(locator, ())
        if len(physical_candidates) > 1 or len(candidates) > 1:
            issue(entry, "TRANSFER_TARGET_AMBIGUOUS", "目标含相同定位和原文的多个记录，保留这些记录。")
            continue
        if not candidates:
            if entry.key in target_keys:
                issue(entry, "TRANSFER_ORIGINAL_CHANGED", "目标原文已变化或记录存在歧义，保留目标内容。")
            else:
                issue(entry, "TRANSFER_UNMATCHED", "目标没有对应定位，旧记录可能已删除或移动，未套用。")
            continue
        candidate = candidates[0]
        old_owner = _owner(old_by_locator[locator][0], source_snapshot)
        target_owner = _owner(candidate, target_snapshot)
        if old_owner is None or target_owner is None or old_owner != target_owner:
            issue(entry, "TRANSFER_RECORD_OWNER_CHANGED", "记录所属主文件无法证明一致，保留目标内容。")
            continue
        matched += 1
        proposals[locator] = replace(candidate, translation=decision.publish_text, stage=1)

    # A localized ID changes every physical reference. A skipped reference must
    # retain target content, so only wholly proved groups may be transferred.
    by_string_id: dict[int, list[TranslationEntry]] = defaultdict(list)
    for entry in physical:
        if entry.string_id is not None:
            by_string_id[entry.string_id].append(entry)
    for references in by_string_id.values():
        selected = [
            proposals[(entry.key, entry.original)] for entry in references if (entry.key, entry.original) in proposals
        ]
        if not selected:
            continue
        if len(selected) != len(references) or len({entry.translation for entry in selected}) != 1:
            for entry in selected:
                proposals.pop((entry.key, entry.original), None)
                issue(
                    entry, "TRANSFER_SHARED_STRING_CONFLICT", "共享字符串还有未匹配引用或译文冲突，整组保留目标内容。"
                )

    reported = {(item.key, item.original) for item in issues}
    for locator, entries in physical_by_locator.items():
        if len(entries) > 1:
            for entry in entries:
                issue(entry, "TRANSFER_TARGET_DUPLICATE", "目标重复定位和原文无法区分，保留该物理记录。")
        elif locator not in proposals and locator not in reported:
            issue(entries[0], "TRANSFER_TARGET_PRESERVED", "目标新增或未匹配词条，保留当前内容。")
    return tuple(proposals.values()), PluginTransferReport(
        len(source), matched, len(proposals), len(physical) - len(proposals), tuple(issues)
    )


def _physical_entries(snapshot: SourceSnapshot) -> tuple[TranslationEntry, ...]:
    parser, _entries = _parser_from_snapshot(snapshot, {})
    plugin = parser.get_plugin()
    if plugin is None:
        raise ValueError("plugin snapshot could not be parsed")
    return tuple(entry for entry, _ in capture_plugin_write_source(plugin, parser.get_strings_lookup()))


def _index(entries):
    indexed = defaultdict(list)
    for entry in entries:
        indexed[(entry.key, entry.original)].append(entry)
    return indexed


def _owner(entry: TranslationEntry, snapshot: SourceSnapshot) -> str | None:
    qualified = entry.form_id_with_plugin
    if not qualified or "|" not in qualified:
        return None
    owner = qualified.split("|", 1)[1].casefold()
    return "<self>" if owner == Path(snapshot.source.uri).name.casefold() else owner
