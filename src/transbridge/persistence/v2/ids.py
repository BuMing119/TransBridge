"""Opaque persistence identities and references."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
import re

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, order=True, slots=True)
class OpaqueId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _ID_PATTERN.fullmatch(self.value):
            raise ValueError("opaque ID must be 1-64 path-independent ASCII characters")
        if self.value in {".", ".."} or self.value.endswith("."):
            raise ValueError("opaque ID is not canonical")

    @property
    def encoded(self) -> str:
        payload = base64.urlsafe_b64encode(self.value.encode("ascii")).decode("ascii").rstrip("=")
        return f"id-{payload}"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True, slots=True)
class ProjectId(OpaqueId):
    pass


@dataclass(frozen=True, order=True, slots=True)
class VariantId(OpaqueId):
    pass


@dataclass(frozen=True, order=True, slots=True)
class SessionId(OpaqueId):
    pass


class EntityKind(StrEnum):
    PROJECT = "project"
    VARIANT = "variant"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class ProjectRef:
    identity: ProjectId

    @property
    def kind(self) -> EntityKind:
        return EntityKind.PROJECT

    @property
    def project_id(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class VariantRef:
    identity: VariantId
    project_id: ProjectId

    @property
    def kind(self) -> EntityKind:
        return EntityKind.VARIANT


@dataclass(frozen=True, slots=True)
class SessionRef:
    identity: SessionId

    @property
    def kind(self) -> EntityKind:
        return EntityKind.SESSION

    @property
    def project_id(self) -> None:
        return None


EntityRef = ProjectRef | VariantRef | SessionRef


__all__ = [
    "EntityKind",
    "EntityRef",
    "OpaqueId",
    "ProjectId",
    "ProjectRef",
    "SessionId",
    "SessionRef",
    "VariantId",
    "VariantRef",
]
