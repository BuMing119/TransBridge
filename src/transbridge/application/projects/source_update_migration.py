"""Match one source revision and migrate complete translation variants."""

from dataclasses import dataclass, replace

from transbridge.application.io import Stage
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.paratranz_context_order import context_orders
from transbridge.persistence.v2.variant import VariantSnapshot

from .provisioning import PreparedProjectSource


@dataclass(frozen=True, slots=True)
class SourceChanges:
    added: frozenset[tuple[str, str | None]]
    removed: frozenset[tuple[str, str | None]]
    unchanged: frozenset[tuple[str, str | None]]
    changed: frozenset[tuple[str, str | None]]
    unverified: frozenset[tuple[str, str | None]]
    reordered: frozenset[tuple[str, str | None]]


def compare_source(old_keys, old: PreparedProjectSource | None, new: PreparedProjectSource) -> SourceChanges:
    if new.hydration is None:
        raise ValueError("新版源文件未提供可比较的词条数据。")
    current = {(item.entry_key.local_key, item.entry_key.original): item for item in new.hydration.entries}
    if len(current) != len(new.hydration.entries):
        raise ValueError("新版源文件有重复词条键，无法可靠迁移。")
    previous = (
        {}
        if old is None or old.hydration is None
        else {(item.entry_key.local_key, item.entry_key.original): item for item in old.hydration.entries}
    )
    keys = set(old_keys) | set(previous)
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
                ((item.entry_key.local_key, item.entry_key.original) for item in old.hydration.entries),
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


def migrate_variant(
    snapshot: VariantSnapshot, namespace: SourceNamespace, new: PreparedProjectSource, changes: SourceChanges
) -> VariantSnapshot:
    old = {
        (item.entry_key.local_key, item.entry_key.original): item
        for item in snapshot.entries
        if item.entry_key.namespace == namespace
    }
    retained = [item for item in snapshot.entries if item.entry_key.namespace != namespace]
    next_namespace = new.baseline.fingerprint.namespace
    if next_namespace != namespace and any(item.entry_key.namespace == next_namespace for item in retained):
        raise ValueError("新版来源与工程中的另一个来源身份冲突。")
    for baseline in new.baseline.entries:
        key = (baseline.entry_key.local_key, baseline.entry_key.original)
        before = old.get(key)
        if before is None:
            retained.append(baseline)
            continue
        review = key in changes.changed or key in changes.unverified
        retained.append(
            replace(
                before,
                entry_key=baseline.entry_key,
                stage=Stage.QUESTIONABLE if review and before.translation else before.stage,
                revision=before.revision.next(),
            )
        )
    fingerprints = tuple(item for item in snapshot.source_fingerprints if item.namespace != namespace)
    if any(item.namespace == next_namespace for item in fingerprints):
        raise ValueError("新版来源指纹与另一个来源身份冲突。")
    return VariantSnapshot(
        snapshot.ref,
        (*fingerprints, new.baseline.fingerprint),
        tuple(retained),
        snapshot.revision + 1,
        snapshot.label_library,
    )
