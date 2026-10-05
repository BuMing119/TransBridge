"""Read label membership without rebuilding unchanged sets or serializing identities."""


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
        identity = None if namespace is None else (str(namespace).strip(), local)
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
        labels[local] = membership
        if identity is not None:
            exact[identity] = membership
    return labels, previous if exact == previous else exact
