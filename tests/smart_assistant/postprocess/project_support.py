"""Real project contexts and isolated result storage for post-processing cases."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from tests.conftest import MockAppContext
from tests.smart_assistant.current_project_support import bind_current_project


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
    location = patch.object(context.project_commands, "task_result_directory", return_value=Path(directory.name))
    location.start()
    case.addCleanup(location.stop)
    return context
