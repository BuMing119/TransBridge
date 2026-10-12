"""Freeze transfer template files and enforce their lease through final commit."""

from dataclasses import dataclass, replace
import os
from pathlib import Path

from .contracts import SourceSnapshot
from .identity import EntryKey, SourceNamespace
from .plugin_write import _parser_from_snapshot, _snapshot_localized_sources, plugin_artifact_paths
from .publish import ConflictPolicy, FileFingerprint, OsPublishFilesystem


@dataclass(frozen=True, slots=True)
class TransferFileLease:
    path: str
    fingerprint: FileFingerprint
    template: bool
    requested_path: str | None = None


def canonical(path: str) -> str:
    return os.path.normcase(OsPublishFilesystem().canonicalize(path))


def freeze_template(snapshot: SourceSnapshot) -> tuple[TransferFileLease, ...]:
    fs = OsPublishFilesystem()
    paths = {canonical(snapshot.source.uri): snapshot}
    for companion, _entries in _snapshot_localized_sources(snapshot):
        paths[canonical(companion.source.uri)] = companion
    plugin = Path(snapshot.source.uri)
    language = str(dict(snapshot.metadata).get("localized_language", "english"))
    candidates = set(paths)
    requested = {path: item.source.uri for path, item in paths.items()}
    requested.update(
        (canonical(str(path)), str(path))
        for path in (
            plugin.parent / "Strings" / f"{plugin.stem}_{language.capitalize()}.{suffix}"
            for suffix in ("strings", "dlstrings", "ilstrings")
        )
    )
    candidates.update(requested)
    leases = []
    for path in sorted(candidates):
        fingerprint = fs.fingerprint(path)
        captured = paths.get(path)
        if captured is not None and fingerprint.sha256 != captured.sha256:
            raise FileExistsError("目标插件或字符串文件在读取期间变化，请重新预检。")
        if captured is None and fingerprint.exists:
            raise FileExistsError("目标字符串文件集合在读取期间变化，请重新预检。")
        leases.append(TransferFileLease(path, fingerprint, True, requested[path]))
    return tuple(leases)


def target_entries_from_snapshot(snapshot: SourceSnapshot):
    """Discard parser read timing: the matching entries come from the leased bytes."""
    _parser, entries = _parser_from_snapshot(snapshot, {})
    namespace = SourceNamespace(dict(snapshot.metadata)["source_namespace"])
    return tuple(replace(entry, entry_key=EntryKey(namespace, entry.key, entry.identity.original)) for entry in entries)


def protect_project_source(source, template, output_paths, conflict_policy) -> None:
    source_paths = {canonical(path) for path in plugin_artifact_paths(source, source.source.uri)}
    template_paths = {canonical(path) for path in plugin_artifact_paths(template, template.source.uri)}
    same_plugin = canonical(source.source.uri) == canonical(template.source.uri)
    for path in output_paths:
        resolved = canonical(path)
        if resolved in source_paths and not (
            same_plugin and resolved in template_paths and conflict_policy is ConflictPolicy.EXPLICIT_OVERWRITE
        ):
            raise ValueError("输出会覆盖原项目来源；请选择新输出，或显式选择同一模板并确认覆盖。")


def verify_leases(leases: tuple[TransferFileLease, ...]) -> None:
    fs = OsPublishFilesystem()
    for lease in leases:
        if (
            canonical(lease.requested_path or lease.path) != lease.path
            or fs.fingerprint(lease.path) != lease.fingerprint
        ):
            label = "目标模板" if lease.template else "输出文件"
            raise FileExistsError(f"{label}在预检后变化，请重新预检：{lease.path}")


class TransferCommitGuard:
    def __init__(self, delegate, leases: tuple[TransferFileLease, ...]) -> None:
        self._delegate = delegate
        self._leases = leases
        self.conflict: str | None = None

    def commit(self, run_id, mutation):
        def guarded():
            try:
                verify_leases(self._leases)
            except (OSError, ValueError) as exc:
                self.conflict = str(exc)
                raise
            mutation()

        return self._delegate.commit(run_id, guarded)
