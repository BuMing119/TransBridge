"""Atomic repository for named, portable AI workflow profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from transbridge.application.translation.custom_workflow_profile import (
    CustomWorkflowProfile,
    CustomWorkflowProfileDocument,
    WorkflowProfileValidationError,
)

from .paths import get_data_dir

_MAX_DOCUMENT_BYTES = 2 * 1024 * 1024


class AiWorkflowProfileRepository:
    """Own the all-or-nothing JSON aggregate for custom workflow profiles."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else Path(get_data_dir()) / "ai_workflow_profiles.json"
        self._lock = threading.RLock()

    def load(self) -> CustomWorkflowProfileDocument:
        with self._lock:
            if not self.path.exists():
                return CustomWorkflowProfileDocument.empty()
            return self._read_document(self.path)

    def profiles(self) -> tuple[CustomWorkflowProfile, ...]:
        return self.load().profiles

    def selected(self) -> CustomWorkflowProfile | None:
        return self.load().selected_profile

    def save(self, document: CustomWorkflowProfileDocument) -> CustomWorkflowProfileDocument:
        """Revalidate and atomically publish one complete aggregate."""

        validated = CustomWorkflowProfileDocument.from_dict(document.to_dict())
        payload = _encode(validated)
        with self._lock:
            self._atomic_write(payload, self.path)
        return validated

    def import_file(self, source: str | os.PathLike[str]) -> CustomWorkflowProfileDocument:
        """Validate the entire import before changing the repository."""

        candidate = self.parse_file(source)
        return self.save(candidate)

    @classmethod
    def parse_file(cls, source: str | os.PathLike[str]) -> CustomWorkflowProfileDocument:
        """Parse and validate an import without changing repository state."""

        return cls._read_document(Path(source))

    def export_file(
        self,
        destination: str | os.PathLike[str],
        *,
        profile_id: str | None = None,
    ) -> Path:
        """Export the aggregate, or one selected profile, through the same envelope."""

        document = self.load()
        if profile_id is not None:
            profile = document.get(profile_id)
            if profile is None:
                raise WorkflowProfileValidationError(f"unknown profile id: {profile_id}")
            document = CustomWorkflowProfileDocument(selected_profile_id=profile.id, profiles=(profile,))
        target = Path(destination)
        self._atomic_write(_encode(document), target)
        return target

    def upsert(self, profile: CustomWorkflowProfile, *, select: bool = False) -> CustomWorkflowProfileDocument:
        """Insert or replace by id while preserving aggregate uniqueness."""

        with self._lock:
            document = self.load()
            profiles = tuple(item for item in document.profiles if item.id != profile.id) + (profile,)
            selected = profile.id if select or document.selected_profile_id is None else document.selected_profile_id
            return self.save(CustomWorkflowProfileDocument(selected_profile_id=selected, profiles=profiles))

    def delete(self, profile_id: str) -> CustomWorkflowProfileDocument:
        with self._lock:
            document = self.load()
            profiles = tuple(profile for profile in document.profiles if profile.id != profile_id)
            if len(profiles) == len(document.profiles):
                raise WorkflowProfileValidationError(f"unknown profile id: {profile_id}")
            selected = document.selected_profile_id
            if selected == profile_id:
                selected = profiles[0].id if profiles else None
            return self.save(CustomWorkflowProfileDocument(selected_profile_id=selected, profiles=profiles))

    def rename(self, profile_id: str, name: str) -> CustomWorkflowProfileDocument:
        with self._lock:
            document = self.load()
            current = document.get(profile_id)
            if current is None:
                raise WorkflowProfileValidationError(f"unknown profile id: {profile_id}")
            replacement = CustomWorkflowProfile.create(
                name,
                profile_id=current.id,
                description=current.description,
                base_mode=current.base_mode,
                strategy=current.strategy,
                workflow=current.workflow,
                limits=current.limits,
                mixed=current.mixed,
            )
            profiles = tuple(replacement if profile.id == profile_id else profile for profile in document.profiles)
            return self.save(CustomWorkflowProfileDocument(document.selected_profile_id, profiles))

    def select(self, profile_id: str | None) -> CustomWorkflowProfileDocument:
        with self._lock:
            document = self.load()
            if profile_id is not None and document.get(profile_id) is None:
                raise WorkflowProfileValidationError(f"unknown profile id: {profile_id}")
            return self.save(CustomWorkflowProfileDocument(profile_id, document.profiles))

    @staticmethod
    def _read_document(path: Path) -> CustomWorkflowProfileDocument:
        try:
            size = path.stat().st_size
            if size > _MAX_DOCUMENT_BYTES:
                raise WorkflowProfileValidationError("workflow profile document exceeds 2 MiB")
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_json_constant)
        except WorkflowProfileValidationError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorkflowProfileValidationError(f"cannot read workflow profile document: {exc}") from exc
        return CustomWorkflowProfileDocument.from_dict(payload)

    @staticmethod
    def _atomic_write(payload: bytes, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def default_ai_workflow_profile_repository() -> AiWorkflowProfileRepository:
    return AiWorkflowProfileRepository()


def _encode(document: CustomWorkflowProfileDocument) -> bytes:
    return (
        json.dumps(document.to_dict(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowProfileValidationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise WorkflowProfileValidationError(f"non-finite JSON value is not allowed: {value}")


__all__ = ["AiWorkflowProfileRepository", "default_ai_workflow_profile_repository"]
