"""Exercise the production download result projection and common merge contract."""

from transbridge.application.translation.result_merge import (
    ResultConflictPolicy,
    ResultEntryState,
    merge_result_entries,
)
from transbridge.ui.operations.batch_download_results import result_changes


def merge_download(preview, current_sources=None):
    changes = result_changes(preview)
    sources = preview.sources if current_sources is None else current_sources
    current = {
        entry.entry_key: ResultEntryState(
            entry.entry_key, entry.original, entry.translation, entry.stage, entry.context or "", entry.deleted
        )
        for source in sources
        for entry in source.local_entries
    }
    return merge_result_entries(
        {change.base.entry_key: change.base for change in changes},
        current,
        {change.incoming.entry_key: change.incoming for change in changes},
        policy=ResultConflictPolicy.DEFER,
    )
