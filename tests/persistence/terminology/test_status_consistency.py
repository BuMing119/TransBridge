from dataclasses import replace

from tests.contracts.terminology.test_repository_contract import _build
from transbridge.persistence.terminology import SqliteTerminologyRepository, workbench_status


def test_status_retries_if_another_connection_commits_during_projection(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(tmp_path, "project-1")
    other = SqliteTerminologyRepository.open(tmp_path, "project-1")
    try:
        before = _build()
        after = replace(before, ref=replace(before.ref, build_key="new", content_digest="new-content"))
        repository.put_build(before)
        read = workbench_status.read_workbench_status
        calls = []

        def changed(*args):
            result = read(*args)
            if not calls:
                other.put_build(after)
            calls.append(result)
            return result

        monkeypatch.setattr(workbench_status, "read_workbench_status", changed)
        assert repository.workbench_status("project-1", "variant-1").build.ref == after.ref
        assert len(calls) == 2
    finally:
        other.close()
        repository.close()
