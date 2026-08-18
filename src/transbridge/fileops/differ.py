"""目录与文件差异分析：按相对路径对齐 + 内容哈希。"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path

HASH_POLICY_VERSION = "sha256-v2"


@dataclass(frozen=True, slots=True)
class HashReuseEvidence:
    source_digest: str
    target_digest: str
    policy_version: str


@dataclass
class DiffResult:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    hash_policy_version: str = HASH_POLICY_VERSION
    hash_reused: list[str] = field(default_factory=list)
    hash_reprocessed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "hash_policy_version": self.hash_policy_version,
            "hash_reused": self.hash_reused,
            "hash_reprocessed": self.hash_reprocessed,
            "summary": {
                "added": len(self.added),
                "removed": len(self.removed),
                "changed": len(self.changed),
                "unchanged": len(self.unchanged),
            },
        }


def normalize_root(d: str) -> Path:
    """向上查找 fomod/ 目录或 ModuleConfig.xml/info.xml 作为锚点，消除包裹层级差异。"""
    p = Path(d)
    if p.is_file():
        p = p.parent
    for candidate in [p, *p.parents]:
        if (candidate / "fomod").is_dir():
            return candidate
        if (candidate / "ModuleConfig.xml").exists() or (candidate / "info.xml").exists():
            return candidate
    return p


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024 * 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(root: Path) -> dict:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def diff_directories(
    old_dir: str,
    new_dir: str,
    *,
    skip_hash_exts=None,
    hash_evidence: dict[str, HashReuseEvidence] | None = None,
) -> DiffResult:
    """Compare trees without trusting extension-only hash skips.

    ``skip_hash_exts`` remains a compatibility hint. A reuse is recorded only
    when both current digests and the policy version match supplied evidence;
    missing or stale evidence is explicitly reprocessed.
    """
    old_root = normalize_root(old_dir)
    new_root = normalize_root(new_dir)
    skip = skip_hash_exts or set()

    old_files = _collect_files(old_root)
    new_files = _collect_files(new_root)

    result = DiffResult()
    all_keys = set(old_files) | set(new_files)
    for rel in sorted(all_keys):
        in_old = rel in old_files
        in_new = rel in new_files
        if in_old and not in_new:
            result.removed.append(rel)
        elif not in_old and in_new:
            result.added.append(rel)
        else:
            ext = Path(rel).suffix.lower()
            source_digest = _sha256(old_files[rel])
            target_digest = _sha256(new_files[rel])
            if ext in skip:
                evidence = (hash_evidence or {}).get(rel)
                if (
                    evidence is not None
                    and evidence.policy_version == HASH_POLICY_VERSION
                    and evidence.source_digest == source_digest
                    and evidence.target_digest == target_digest
                ):
                    result.hash_reused.append(rel)
                else:
                    result.hash_reprocessed.append(rel)
            if source_digest == target_digest:
                result.unchanged.append(rel)
            else:
                result.changed.append(rel)
    return result
