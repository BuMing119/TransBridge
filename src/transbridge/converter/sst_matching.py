# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Conservative SST migration matching without relying on the old index layout."""

from collections import defaultdict
from dataclasses import replace


def target_locator(entry) -> tuple[int, str] | None:
    metadata = dict(entry.metadata)
    form_id = metadata.get("sst.form_id")
    if form_id is None and "eet.form_id" in metadata:
        try:
            form_id = int(metadata["eet.form_id"], 16)
        except (ValueError, TypeError):
            return None
    if form_id is None:
        value = entry.form_id_with_plugin
        if value:
            value = value.split("|", 1)[0]
        else:
            value = entry.id.split(":", 1)[-1].split("|", 1)[0]
        try:
            form_id = int(value, 16)
        except (TypeError, ValueError):
            return None
    # Plugin dialogue context includes a quest suffix (INFO:NAM1|quest).
    # Its physical field type is kept separately and must not be confused with
    # that display context when matching an SST record.
    descriptor = entry.dsd_type or (entry.context or "").split("|", 1)[0]
    signature = descriptor.replace(":", "").replace(" ", "")
    if len(signature) != 8:
        return None
    return form_id, signature


def update_entry(entry, sst):
    if target_locator(entry) != (sst.form_id, sst.rec) or entry.original != sst.text:
        return None
    if entry.stage != 0 or entry.translation or not sst.translated_text:
        return entry
    return replace(entry, translation=sst.translated_text, stage=1)


def apply_entries(collection, sst_entries) -> dict:
    """Use exact type and original text; ambiguous source or destination is skipped."""
    source = defaultdict(list)
    targets = defaultdict(list)
    for item in sst_entries:
        source[(item.form_id, item.rec, item.text)].append(item)
    for entry in collection:
        locator = target_locator(entry)
        if locator is not None:
            targets[(*locator, entry.original)].append(entry)
    matched = updated = 0
    for locator, entries in targets.items():
        candidates = source.get(locator, ())
        if len(entries) != 1 or len(candidates) != 1:
            continue
        entry = entries[0]
        result = update_entry(entry, candidates[0])
        if result is None:
            continue
        matched += 1
        if result is not entry:
            collection.add(result, overwrite=True)
            updated += 1
    return {"matched": matched, "updated": updated, "skipped": matched - updated}
