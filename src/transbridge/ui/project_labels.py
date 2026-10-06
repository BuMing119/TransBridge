"""Read label membership without rebuilding unchanged sets or serializing identities."""

from transbridge.application.io.identity import EntryKey


def entry_label_key(entry) -> str:
    """Keep legacy label IDs while disambiguating original-matched entries."""
    return entry.identity.serialize() if getattr(entry, "requires_original_match", False) else entry.id


def exact_label_key(identity: EntryKey) -> tuple[str, ...]:
    base = (identity.namespace.value, identity.local_key)
    return base if identity.original is None else (*base, identity.original)


def project_entry_labels(states, previous):
    labels = {}
    exact = {}
    for entry in states:
        key = entry.get("entry_key") or {}
        local = key.get("local_key")
        if local is None:
            continue
        local = str(local)
        namespace = key.get("namespace")
        entry_key = None if namespace is None else EntryKey.from_dict(dict(key))
        identity = None if entry_key is None else exact_label_key(entry_key)
        raw_labels = entry.get("labels", ())
        existing = previous.get(identity)
        if (
            existing is not None
            and len(raw_labels) == len(existing)
            and all(any(str(v) == label for v in raw_labels) for label in existing)
        ):
            membership = existing
        else:
            membership = {str(v) for v in raw_labels}
        label_key = entry_key.serialize() if entry_key is not None and entry_key.original is not None else local
        labels[label_key] = membership
        if identity is not None:
            exact[identity] = membership
    return labels, previous if exact == previous else exact
