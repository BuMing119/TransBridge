# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Match one source revision and migrate complete translation variants."""

from dataclasses import dataclass, replace

from transbridge.application.io import Stage
from transbridge.application.io.paratranz_context_order import context_orders
from transbridge.persistence.v2.variant import SourceFingerprint, VariantSnapshot

from .provisioning_models import PreparedProjectSource

type LocalEntryKey = tuple[str, str | None]


@dataclass(frozen=True, slots=True)
class SourceChanges:
    added: frozenset[tuple[str, str | None]]
    removed: frozenset[tuple[str, str | None]]
    unchanged: frozenset[tuple[str, str | None]]
    changed: frozenset[tuple[str, str | None]]
    unverified: frozenset[tuple[str, str | None]]
    reordered: frozenset[tuple[str, str | None]]


@dataclass(frozen=True, slots=True)
class SourceMigration:
    previous: SourceFingerprint
    replacement: PreparedProjectSource
    changes: SourceChanges
    matched_keys: dict[LocalEntryKey, LocalEntryKey] | None


def compare_source(
    old_keys,
    old: PreparedProjectSource | None,
    new: PreparedProjectSource,
    *,
    matched_keys: dict[LocalEntryKey, LocalEntryKey] | None = None,
) -> SourceChanges:
    if new.hydration is None:
        raise ValueError("新版源文件未提供可比较的词条数据。")
    remapped = matched_keys or {}
    current = {(item.entry_key.local_key, item.entry_key.original): item for item in new.hydration.entries}
    if len(current) != len(new.hydration.entries):
        raise ValueError("新版源文件有重复词条键，无法可靠迁移。")
    previous = (
        {}
        if old is None or old.hydration is None
        else {remapped.get(_local_key(item), _local_key(item)): item for item in old.hydration.entries}
    )
    keys = {remapped.get(key, key) for key in old_keys} | set(previous)
    common = keys & current.keys()
    changed = set()
    unchanged = set()
    for key in common & previous.keys():
        before, after = previous[key], current[key]
        (unchanged if (before.original, before.context) == (after.original, after.context) else changed).add(key)
    old_order = (
        {}
        if old is None or old.hydration is None
        else dict(
            zip(
                (remapped.get(_local_key(item), _local_key(item)) for item in old.hydration.entries),
                context_orders(old.hydration.entries),
                strict=True,
            )
        )
    )
    new_order = dict(zip(current, context_orders(new.hydration.entries), strict=True))
    reordered = {key for key in common & old_order.keys() if old_order[key] != new_order[key]}
    return SourceChanges(
        frozenset(current.keys() - keys),
        frozenset(keys - current.keys()),
        frozenset(unchanged),
        frozenset(changed),
        frozenset(common - previous.keys()),
        frozenset(reordered),
    )


def migrate_variant(snapshot: VariantSnapshot, migrations: tuple[SourceMigration, ...]) -> VariantSnapshot:
    existing = {item.namespace: item for item in snapshot.source_fingerprints}
    old_namespaces = {item.previous.namespace for item in migrations}
    if len(old_namespaces) != len(migrations) or any(
        existing.get(item.previous.namespace) != item.previous for item in migrations
    ):
        raise ValueError("工程内翻译版本的来源不一致，无法统一更新。")
    replacements = tuple(item.replacement.baseline.fingerprint for item in migrations)
    if len({item.namespace for item in replacements}) != len(migrations):
        raise ValueError("本次配对的新版文件存在相同的来源身份，请核对所选文件。")
    if any(
        item.namespace != migration.previous.namespace and item.namespace in existing
        for migration, item in zip(migrations, replacements, strict=True)
    ):
        raise ValueError("新版来源与工程中的另一个来源身份冲突。")

    fingerprints = tuple(item for item in snapshot.source_fingerprints if item.namespace not in old_namespaces)
    old_entries = {namespace: {} for namespace in old_namespaces}
    retained = []
    for item in snapshot.entries:
        group = old_entries.get(item.entry_key.namespace)
        if group is None:
            retained.append(item)
        else:
            group[_local_key(item)] = item
    for migration in migrations:
        retained.extend(_migrate_entries(old_entries[migration.previous.namespace], migration))
    return VariantSnapshot(
        snapshot.ref,
        (*fingerprints, *replacements),
        tuple(retained),
        snapshot.revision + 1,
        snapshot.label_library,
    )


def _migrate_entries(old, migration):
    matched_keys = migration.matched_keys
    if matched_keys is not None:
        old = {matched_keys.get(key, key): item for key, item in old.items()}
    changes = migration.changes
    for baseline in migration.replacement.baseline.entries:
        key = (baseline.entry_key.local_key, baseline.entry_key.original)
        before = old.get(key)
        if before is None:
            yield baseline
            continue
        review = key in changes.changed or key in changes.unverified
        if matched_keys is not None and before.stage in {Stage.HIDDEN, Stage.LOCKED}:
            review = False
        yield replace(
            before,
            entry_key=baseline.entry_key,
            stage=Stage.QUESTIONABLE if review and before.translation else before.stage,
            revision=before.revision.next(),
        )


def _local_key(entry) -> LocalEntryKey:
    return entry.entry_key.local_key, entry.entry_key.original
