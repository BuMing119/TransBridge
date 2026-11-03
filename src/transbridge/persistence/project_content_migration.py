"""Close missing source content at the existing Project migration boundary."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import PureWindowsPath
import secrets

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.io import FormatId
from transbridge.application.projects.provisioning_models import ProjectSourceRequest
from transbridge.application.projects.source_content import authoritative_baseline_sources, source_content_identity
from transbridge.application.projects.source_supplement_mapping import supplement_rows, supplement_variants

from .project_content_candidates import ProjectContentCandidates
from .project_source_inputs import load_project_source_inputs
from .v2.models import ProjectDto


class ProjectContentMigration:
    """Recover a complete candidate, then publish all sources and Variants once."""

    def __init__(self, root, filesystem, store, preparer, artifacts):
        self._root, self._filesystem = root, filesystem
        self._store, self._preparer, self._artifacts = store, preparer, artifacts

    def repair(self, project_ref, context):
        expected = self._store.read(project_ref)
        project, variants = expected.project, expected.variants
        data = dict(project.envelope.data)
        sources = [dict(source) for source in data.get("sources", ())]
        required = authoritative_baseline_sources(tuple(sources), tuple(data.get("source_relations", ())))
        candidates = ProjectContentCandidates(self._root, self._filesystem, self._artifacts, project_ref)
        missing_ids = {
            item.source_id
            for variant in variants
            for item in load_project_source_inputs(project, variant, self._artifacts, context).missing_inputs
        }
        failed_ids = set(missing_ids)
        failures = []
        required_ids = {source.get("source_id") for source in required}
        for source in tuple(sources):
            # A source may be present while one Variant still has uncovered rows.
            # Only this repair stage examines all Variants; normal opens use their
            # already-captured source and never enumerate recovery candidates.
            authoritative = source.get("source_id") in required_ids
            if authoritative:
                if source.get("source_id") not in missing_ids:
                    continue
            elif source.get("content_state") == "captured":
                try:
                    self._preparer.load_saved_source(source)
                    continue
                except (OSError, ValueError, DomainError) as exc:
                    candidates.unavailable(source.get("source_artifact_id"), exc)
            try:
                if authoritative:
                    namespace, fingerprint = _source_identity(
                        source, expected.variants, len(required), candidates.snapshots()
                    )
                else:
                    namespace, fingerprint = source_content_identity(source), source.get("fingerprint")
                    if fingerprint is None:
                        raise ValueError("折叠来源缺少原始指纹，无法确定其完整保存内容。")
                prepared, rows = self._recover(
                    source,
                    namespace,
                    fingerprint,
                    variants if authoritative else (),
                    candidates,
                    context,
                    additional_namespaces=("legacy:v1",) if authoritative and len(required) == 1 else (),
                )
                if authoritative:
                    variants = supplement_variants(variants, prepared, rows, namespace)
                descriptor = prepared.to_dict()
                updated = {
                    **source,
                    "fingerprint": prepared.baseline.fingerprint.sha256,
                    "source_artifact_id": descriptor["source_artifact_id"],
                    "content_state": "captured",
                    "format_options": descriptor.get("format_options", {}),
                    "legacy": {**source.get("legacy", {}), "namespace": prepared.baseline.fingerprint.namespace.value},
                }
                sources[sources.index(source)] = updated
            except (OSError, ValueError, DomainError) as exc:
                if isinstance(exc, DomainError) and exc.category in {ErrorCategory.INTERNAL, ErrorCategory.CANCELLED}:
                    raise
                failed_ids.add(source.get("source_id"))
                failures.append({
                    "source_id": source.get("source_id"),
                    "location": source.get("location"),
                    "reason": str(exc),
                })
        project = ProjectDto(
            replace(project.envelope, revision=project.envelope.revision + 1, data={**data, "sources": sources})
        )
        # Unregistered namespaces and unmapped saved entries are still missing.
        # Preserve their translations and refuse to certify a partial Project.
        for variant in variants:
            missing = load_project_source_inputs(project, variant, self._artifacts, context).missing_inputs
            failures.extend({"variant_id": variant.ref.identity.value, **item.to_dict()} for item in missing)
        if failures:
            labels = list(
                dict.fromkeys(
                    str(
                        source.get("display_name")
                        or PureWindowsPath(
                            source.get("location") or (source.get("legacy") or {}).get("path") or ""
                        ).name
                        or source.get("source_id")
                    )
                    for source in sources
                    if source.get("source_id") in failed_ids
                )
            )
            if not labels:
                labels = list(
                    dict.fromkeys(
                        str(item.get("display_name") or item.get("namespace") or "未登记来源") for item in failures
                    )
                )
            raise DomainError(
                ErrorCategory.PREREQUISITE,
                "PROJECT_CONTENT_REPAIR_FAILED",
                "自动恢复未能找齐来源内容（" + "、".join(labels[:8]) + "），工程未打开，本次打开已废弃。"
                "全部译文、工程原件和已有备份均保留，请恢复缺失输入后重试。",
                details={"missing_sources": failures, "unavailable_candidates": candidates.failures},
            )
        # supplement_variants may run for many sources. Advance each persisted
        # revision once and commit the complete aggregate in one existing journal.
        revisions = {item.ref: item.revision for item in expected.variants}
        variants = tuple(replace(item, revision=revisions[item.ref] + 1) for item in variants)
        return self._store.commit(
            expected,
            project,
            variants,
            "content-migration-" + secrets.token_hex(16),
            backup_metadata={"operation": "project-content-migration"},
        )

    def _recover(self, source, namespace, fingerprint, variants, candidates, context, *, additional_namespaces=()):
        exact, supported = [], []
        for identity, prepared in candidates.saved():
            descriptor = prepared.to_dict()
            descriptor.update(source_artifact_id=identity, content_state="captured")
            prepared = replace(prepared, descriptor=tuple(descriptor.items()))
            try:
                match = _match(
                    source,
                    namespace,
                    fingerprint,
                    variants,
                    prepared,
                    additional_namespaces=additional_namespaces,
                )
            except (OSError, ValueError, DomainError) as exc:
                candidates.unavailable(identity, exc)
                continue
            if match is not None:
                (exact if prepared.baseline.fingerprint.sha256 == fingerprint else supported).append((prepared, match))
        if exact:
            return exact[0]
        if supported:
            return _unique_supported(supported)
        references = candidates.references()
        paths = []
        original_path = source.get("location") or (source.get("legacy") or {}).get("path")
        for reference in (*references, source):
            if reference.get("format_id") != source["format_id"]:
                continue
            # Other registrations only contribute a declared path when their
            # recorded revision or content namespace identifies this source.
            if reference is not source and (fingerprint is None or reference.get("fingerprint") != fingerprint):
                continue
            path = reference.get("location") or (reference.get("legacy") or {}).get("path")
            if (
                reference is not source
                and path
                and original_path
                and os.path.normcase(path) == os.path.normcase(original_path)
            ):
                continue
            if path and path not in paths:
                paths.append(path)
        for path in paths:
            options = dict(source.get("format_options") or {})
            if namespace is not None:
                options["source_namespace"] = namespace
            try:
                prepared = self._preparer.prepare_recovery_source(
                    ProjectSourceRequest(
                        path,
                        FormatId(source["format_id"]),
                        expected_fingerprint=fingerprint,
                        options=tuple(options.items()),
                    ),
                    context,
                    role="primary",
                    common_options=(),
                )
                rows = _match(
                    source,
                    namespace,
                    fingerprint,
                    variants,
                    prepared,
                    additional_namespaces=additional_namespaces,
                )
                if rows is not None:
                    if prepared.baseline.fingerprint.sha256 == fingerprint:
                        return prepared, rows
                    supported.append((prepared, rows))
            except (OSError, ValueError, DomainError) as exc:
                candidates.unavailable(path, exc)
        if supported:
            return _unique_supported(supported)
        raise ValueError("已有成果、备份引用与原路径均无可验证且完整对应的来源内容。")


def _source_identity(source, variants, source_count, snapshots=()):
    namespace = source_content_identity(source)
    fingerprint = source.get("fingerprint")
    # Some old registries called a reference path a namespace. The immutable
    # Variant identity is better evidence than that obsolete path convention.
    locations = {source.get("location"), (source.get("legacy") or {}).get("path")}
    if namespace in locations and not dict(source.get("format_options") or {}).get("source_namespace"):
        namespace = None
    if namespace is None:
        identities = {
            item.namespace.value
            for variant in (*variants, *snapshots)
            for item in variant.source_fingerprints
            if fingerprint is not None and item.sha256 == fingerprint or fingerprint is None and source_count == 1
        }
        namespace = next(iter(identities)) if len(identities) == 1 else None
        if namespace is None and source_count == 1:
            qualified = identities - {"legacy:v1"}
            namespace = next(iter(qualified)) if len(qualified) == 1 else None
    if namespace is None:
        raise ValueError("旧来源缺少唯一的来源身份，无法自动对应保存译文。")
    fingerprints = {
        item.sha256
        for variant in variants
        for item in variant.source_fingerprints
        if item.namespace.value == namespace and item.sha256 is not None
    }
    if fingerprint is not None:
        fingerprints.add(fingerprint)
    if len(fingerprints) > 1:
        raise ValueError("保存的版本与来源登记指纹不一致，无法自动确定原始版本。")
    if not fingerprints:
        historical = {
            item.sha256
            for snapshot in snapshots
            for item in snapshot.source_fingerprints
            if item.namespace.value == namespace and item.sha256 is not None
        }
        if len(historical) == 1:
            fingerprints.update(historical)
    return namespace, next(iter(fingerprints)) if fingerprints else None


def _match(source, namespace, fingerprint, variants, prepared, *, additional_namespaces=()):
    if prepared.hydration is None or prepared.to_dict().get("format_id") != source["format_id"]:
        return None
    options = dict(source.get("format_options") or {})
    expected_options = dict(prepared.to_dict().get("format_options") or {})
    for value in (options, expected_options):
        value.pop("source_namespace", None)
        value.setdefault("skip_empty", False)
        if source["format_id"] == FormatId.PLUGIN_SSE.value:
            value.setdefault("discover_sibling_strings", True)
    if options.get("skip_empty") is True and expected_options.get("skip_empty") is False:
        options["skip_empty"] = False
    if options != expected_options:
        return None
    if fingerprint is not None and prepared.baseline.fingerprint.sha256 != fingerprint:
        return None
    if fingerprint is None and prepared.baseline.fingerprint.namespace.value != namespace:
        return None
    rows, _ = supplement_rows(variants, prepared, namespace, additional_namespaces=additional_namespaces)
    live_keys = {
        row.entry_key
        for variant in variants
        for row in variant.entries
        if row.entry_key.namespace.value in {namespace, *additional_namespaces} and not row.tombstone
    }
    if any(row.matched_key is None for row in rows if row.old_key in live_keys):
        return None
    if fingerprint is None:
        # The candidate has the persisted namespace or the explicitly recorded
        # original path; the complete one-to-one mapping supplies the remaining
        # evidence. No unrelated, unhashed path reaches this matcher.
        if not rows:
            return None
    return rows


def _unique_supported(candidates):
    identities = {prepared.baseline.fingerprint for prepared, _rows in candidates}
    if len(identities) != 1:
        raise ValueError("多个来源候选均可对应旧词条，但内容版本不同，已拒绝猜测。")
    return candidates[0]
