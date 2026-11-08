"""Map bilingual record translations onto unique physical target identities."""

from collections import defaultdict
from dataclasses import replace

from transbridge.converter.eet_matching import known_editor_id
from transbridge.converter.sst_matching import target_locator


def prepare_record_candidates(task, sources, label):
    source_matches, target_matches = defaultdict(list), defaultdict(list)
    for entry in sources:
        locator = target_locator(entry)
        if locator is not None:
            source_matches[(*locator, entry.original)].append(entry)
    for entry in task.base.values():
        locator = target_locator(entry)
        if locator is not None:
            target_matches[(*locator, entry.original)].append(entry)

    candidates, unmapped = [], []
    ignored_empty = 0
    if not any(entry.translation for entry in sources):
        raise ValueError(f"MIGRATION_{label}_NO_TRANSLATIONS: {label} 中没有非空译文，未修改当前版本。")
    for entry in sources:
        if not entry.translation:
            ignored_empty += 1
            continue
        physical_locator = target_locator(entry)
        if physical_locator is None:
            unmapped.append({**entry.to_dict(), "reason": f"{label.lower()}_unknown_locator"})
            continue
        locator = (*physical_locator, entry.original)
        targets = target_matches[locator]
        if label == "EET" and entry.editor_id:
            targets = [
                target
                for target in targets
                if not known_editor_id(target) or known_editor_id(target) == entry.editor_id.casefold()
            ]
        if len(source_matches[locator]) != 1 or len(targets) != 1:
            unmapped.append({**entry.to_dict(), "reason": f"{label.lower()}_no_unique_original_match"})
            continue
        candidates.append(replace(targets[0], translation=entry.translation, stage=entry.stage))

    task.record.update(
        raw_entries=[entry.to_dict() for entry in sources],
        parse_unmapped=unmapped,
    )
    task.record[f"{label.lower()}_ignored_empty"] = ignored_empty
    if unmapped:
        task.record["parse_outcome"] = "partial"
    return tuple(candidates)
