"""Real project contexts and isolated result storage for post-processing cases."""

from pathlib import Path
import tempfile
from types import SimpleNamespace

from tests.conftest import MockAppContext
from tests.smart_assistant.current_project_support import bind_current_project
from transbridge.persistence.task_store import TaskStore


def project_context(case, collection=None):
    if not collection:
        return MockAppContext(collection)
    context = bind_current_project(case, collection)
    source_entries = tuple(collection)
    context.project_commands._categorized_uploads = SimpleNamespace(
        result_source_texts=lambda _project, _variant, keys, _context: {
            entry.identity: (entry.original, entry.context or "") for entry in source_entries if entry.identity in keys
        }
    )
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    context.task_store = TaskStore(Path(directory.name) / "task-store-v1")
    context.project_commands.project_exists = lambda project_id: project_id == case.project.project_ref.identity.value
    context.project_commands.result_receipt = lambda _project_id, _operation_id: None
    return context
