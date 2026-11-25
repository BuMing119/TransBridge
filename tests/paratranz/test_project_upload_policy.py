from dataclasses import replace
import json

import pytest

from transbridge.application.file_upload_modes import UploadOperation
from transbridge.application.upload_policy import POLICY_FILENAME, OperationRule, ProjectRole, UploadLayout
from transbridge.paratranz.download_paths import select_download_files
from transbridge.paratranz.project_policy import default_upload_policy, publish_policy, read_access


class MemoryProject:
    def __init__(self):
        self.uid = 101
        self.paths = {}
        self.content = b""
        self.writes = []

    def inventory(self, pid):
        return self.paths.copy()

    def get_file_content(self, pid, fid, *, dl):
        assert dl and self.paths[POLICY_FILENAME] == fid
        return self.content

    def get_project(self, pid):
        return {"id": pid, "uid": 101}

    def get_my_user(self):
        return {"id": self.uid}

    def list_members(self, pid):
        return [{"uid": 102, "permission": 3, "user": {"nickname": "管理员"}}]

    def upload_file_bytes(self, pid, name, content):
        assert name == POLICY_FILENAME and not self.paths
        self.writes.append("create")
        self.paths[name] = 7
        self.content = content

    def update_file_bytes(self, pid, fid, name, content, *, incremental):
        assert not incremental and self.paths[name] == fid
        self.writes.append("replace")
        self.content = content

    def window(self):
        return read_access(self, 42, self.uid, self.inventory(42), for_window=True)


def test_default_saved_once_then_owner_edit_replaces_removed_grants(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = MemoryProject()
    first = project.window()
    assert first.role == ProjectRole.OWNER and first.policy is None
    policy = default_upload_policy()
    next_open = publish_policy(project, 42, first, policy, lambda: None)
    assert next_open == project.window()
    assert next_open.file_id == 7 and all(next_open.allows(op) for op in UploadOperation)
    assert publish_policy(project, 42, first, policy, lambda: None) == next_open
    rules = {**policy.rules, UploadOperation.FORCE: OperationRule()}
    updated = replace(policy, layout=UploadLayout.CATEGORIZED, rules=rules)
    saved = publish_policy(project, 42, next_open, updated, lambda: None)
    assert not saved.allows_layout(False) and not saved.allows(UploadOperation.FORCE)
    assert "upload.trans_force" not in {row["key"] for row in json.loads(project.content)}
    assert project.writes == ["create", "replace"] and list(tmp_path.iterdir()) == []
    with pytest.raises(ValueError, match="变更"):
        publish_policy(project, 42, next_open, policy, lambda: None)
    project.uid = 102
    admin = project.window()
    assert admin.role == ProjectRole.ADMIN
    with pytest.raises(PermissionError, match="所有者"):
        publish_policy(project, 42, admin, policy, lambda: None)
    assert project.writes == ["create", "replace"]


@pytest.mark.parametrize("content", [b"", b" \t\n", b"[]", b"\xef\xbb\xbf[]"])
def test_empty_cloud_attachment_has_no_extra_restrictions(content):
    project = MemoryProject()
    project.paths[POLICY_FILENAME] = 7
    project.content = content
    assert project.window().policy is None
    assert project.window().allows("trans_force")


def test_malformed_cloud_policy_never_authorizes_a_write():
    project = MemoryProject()
    project.paths[POLICY_FILENAME] = 7
    project.content = b"{}"
    with pytest.raises(ValueError, match=POLICY_FILENAME):
        project.window()
    assert select_download_files(project.paths, [POLICY_FILENAME]) == ()
    assert not project.writes
