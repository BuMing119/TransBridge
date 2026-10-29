"""Portable project bundles carry the exact saved source revision and its closure."""

from pathlib import PurePosixPath, PureWindowsPath

from transbridge.application.io.plugin_localized_sources import _snapshot_localized_sources

from .saved_project_sources import validate_saved_source
from .source_artifact_store import ProjectSourceArtifactStore

_PREFIX = "source_artifacts/"


def _filename(value):
    name = PureWindowsPath(value).name
    if not name or name in {".", ".."} or ":" in name or "/" in name or "\\" in name:
        raise ValueError("来源成果缺少可移植文件名")
    return name


def source_members(prepared, index, location):
    """Materialize only captured bytes, including BSA strings as loose companions."""
    hydration = prepared.hydration
    if hydration is None or hydration.source_snapshot.content is None:
        raise ValueError("来源成果缺少完整源文件内容")
    snapshot = hydration.source_snapshot
    resource_name = snapshot.source.display_name or snapshot.source.uri
    member = f"sources/{index}/{_filename(resource_name)}"
    yield member, snapshot.content
    seen = {member.casefold()}
    for companion, _entries in _snapshot_localized_sources(snapshot):
        if companion.content is None:
            raise ValueError("来源成果缺少本地化字符串内容")
        name = _filename(companion.source.display_name or companion.source.uri.rsplit("!/", 1)[-1])
        if PurePosixPath(name).suffix.lower() not in {".strings", ".dlstrings", ".ilstrings"}:
            raise ValueError("来源成果包含未知本地化资源")
        child = f"sources/{index}/Strings/{name}"
        if child.casefold() in seen:
            raise ValueError("来源成果包含重复本地化资源")
        seen.add(child.casefold())
        yield child, companion.content


def capture_saved_source(archive, source, index, store):
    artifact_id = source["source_artifact_id"]
    prepared = store.get(artifact_id)
    validate_saved_source(prepared, source)
    members = source_members(prepared, index, source.get("location", ""))
    member, content = next(members)
    archive.writestr(member, content)
    for child, content in members:
        archive.writestr(child, content)
    return {"source_id": source["source_id"], "member": member}


def capture_artifacts(archive, sources, store):
    written = set()
    for artifact_id in dict.fromkeys(source.get("source_artifact_id") for source in sources):
        if artifact_id is None:
            continue
        if store is None:
            raise ValueError("当前服务无法读取工程引用的来源成果")
        for relative, content in store.iter_records(artifact_id):
            if relative not in written:
                archive.writestr(_PREFIX + relative, content)
                written.add(relative)


def validate_artifacts(archive, sources, locations):
    """Validate package-owned artifacts without trusting any existing local cache."""
    declared = set()

    def read(relative):
        member = _PREFIX + relative
        declared.add(member)
        try:
            return archive.read(member)
        except KeyError as exc:
            raise ValueError("项目包缺少引用的来源成果或二进制内容") from exc

    for index, source in enumerate(sources):
        artifact_id = source.get("source_artifact_id")
        if artifact_id is None:
            continue
        prepared = ProjectSourceArtifactStore.read_records(artifact_id, read)
        validate_saved_source(prepared, source)
        # A portable file and its cached read model must describe the same input.
        source_member = locations[source["source_id"]]
        expected = list(source_members(prepared, index, source.get("location", "")))
        for generated, content in expected:
            member = (
                source_member
                if generated == expected[0][0]
                else str(PurePosixPath(source_member).parent / "Strings" / PurePosixPath(generated).name)
            )
            try:
                actual = archive.read(member)
            except KeyError as exc:
                raise ValueError("项目包缺少来源成果中的字符串文件") from exc
            if actual != content:
                raise ValueError("项目包来源文件与解析成果不一致")
    actual = {item.filename for item in archive.infolist() if not item.is_dir() and item.filename.startswith(_PREFIX)}
    if actual != declared:
        raise ValueError("项目包包含未引用的来源成果文件")


def import_artifacts(archive, sources, store):
    for artifact_id in dict.fromkeys(source.get("source_artifact_id") for source in sources):
        if artifact_id is None:
            continue
        if store is None:
            raise ValueError("当前服务无法导入来源成果")
        store.import_records(artifact_id, lambda relative: archive.read(_PREFIX + relative))


def is_artifact_member(name):
    return name.startswith(_PREFIX)
