"""Read the immutable snapshots written by the V2 lifecycle transaction store."""

from __future__ import annotations

import logging
import os
import re

from transbridge.application.projects.snapshots import ProjectSnapshotInfo

from .v2.filesystem import PersistenceFilesystemPort, RepositoryPaths
from .v2.ids import VariantRef
from .v2.models import SCHEMA_VERSION, SchemaValidationError, VariantDto
from .v2.schema import parse_json_bytes, validate_v2
from .v2.variant import VariantSnapshot

logger = logging.getLogger(__name__)


class ProjectSnapshotRepository:
    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        self._filesystem = filesystem
        self._paths = RepositoryPaths(root, filesystem)
        self._directory = self._paths.guard(os.path.join(root, "snapshots"))

    def list(self, ref: VariantRef) -> tuple[ProjectSnapshotInfo, ...]:
        snapshots = []
        for path in self._filesystem.list_files(self._directory):
            identity, extension = os.path.splitext(os.path.basename(path))
            if extension != ".json" or not re.fullmatch(r"[0-9a-f]{64}", identity):
                continue
            try:
                document = parse_json_bytes(self._filesystem.read_bytes(self._paths.guard(path)))
            except (OSError, SchemaValidationError) as exc:
                logger.warning("无法识别快照归属，已跳过列表项并保留原文件用于恢复: %s (%s)", path, exc)
                continue
            if document.get("project_id") != ref.project_id.value:
                continue
            variant = document.get("variant", {})
            if not isinstance(variant, dict):
                logger.warning("工程快照缺少有效版本记录，已保留原文件: %s", path)
                continue
            if variant.get("id") != ref.identity.value:
                continue
            try:
                snapshots.append(self._info(identity, ref, document))
            except ValueError as exc:
                logger.warning("历史元信息无效，已跳过列表项并保留原文件用于恢复: %s (%s)", path, exc)
        return tuple(sorted(snapshots, key=lambda item: (item.revision, item.name, item.identity), reverse=True))

    def load(self, identity: str, ref: VariantRef) -> VariantSnapshot:
        return self._read(identity, ref)[1]

    def delete(self, identity: str, ref: VariantRef) -> None:
        # Validate both the opaque snapshot identity and its current owner before
        # removing the one exact, root-confined record.
        self._read(identity, ref)
        path = self._paths.guard(os.path.join(self._directory, f"{identity}.json"))
        self._filesystem.remove(path, missing_ok=False)

    def _read(self, identity: str, ref: VariantRef) -> tuple[ProjectSnapshotInfo, VariantSnapshot]:
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("无效的快照标识")
        path = self._paths.guard(os.path.join(self._directory, f"{identity}.json"))
        document = parse_json_bytes(self._filesystem.read_bytes(path))
        info = self._info(identity, ref, document)
        dto = validate_v2(document["variant"], ref)
        if not isinstance(dto, VariantDto):
            raise ValueError("快照不是翻译版本")
        return info, VariantSnapshot.from_dto(dto, ref)

    @staticmethod
    def _info(identity: str, ref: VariantRef, document: dict) -> ProjectSnapshotInfo:
        """Validate only display metadata; restoration validates the complete payload."""
        if document.get("schema_version") != 1 or document.get("project_id") != ref.project_id.value:
            raise ValueError("快照格式不受支持，或不属于当前工程")
        name = document.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("快照缺少名称")
        variant = document.get("variant")
        if (
            not isinstance(variant, dict)
            or variant.get("schema_version") != SCHEMA_VERSION
            or variant.get("entity_type") != "variant"
            or variant.get("id") != ref.identity.value
            or type(variant.get("revision")) is not int
            or variant["revision"] < 0
        ):
            raise ValueError("快照版本元信息无效，或不属于当前翻译版本")
        return ProjectSnapshotInfo(identity, name, variant["revision"])
