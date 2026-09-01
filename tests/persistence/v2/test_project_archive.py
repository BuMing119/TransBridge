from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import stat
import struct
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_snapshots import ProjectSnapshotRepository
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectId, VariantId, VariantRef


@pytest.fixture
def services_factory(tmp_path):
    opened = []

    def build(name, *, filesystem=None):
        services = build_persistence_v2_services(
            tmp_path / name,
            id_factory=lambda: uuid4().hex,
            timestamp_factory=lambda: "2026-08-31T00:00:00+08:00",
            filesystem=filesystem,
        )
        opened.append(services)
        return services

    yield build
    for services in reversed(opened):
        services.close()


def _ok(result):
    assert result.is_success, result.diagnostics
    return result.value


def _file_state(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in Path(root).rglob("*") if path.is_file()}


def _bundle_content(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("archive.json"))
        members = [
            (info.filename, archive.read(info)) for info in archive.infolist() if info.filename != "archive.json"
        ]
    return manifest, members


def _write_bundle(path, manifest, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("archive.json", json.dumps(manifest, ensure_ascii=False))
        for name, payload in members:
            if isinstance(name, str) and "\\" in name:
                # ZipInfo normally rewrites Windows separators; retain the hostile wire name.
                info = zipfile.ZipInfo()
                info.filename = name
                name = info
            archive.writestr(name, payload)
    return path


@pytest.fixture
def exported_project(tmp_path, services_factory):
    source = tmp_path / "source.xml"
    fixture = Path(__file__).parents[2] / "contracts" / "io" / "fixtures" / "xt-small.xml"
    source.write_bytes(fixture.read_bytes())
    services = services_factory("export")
    context = RequestContext("gui", run_id="archive")
    _ok(
        services.gui_project_commands.create_project(
            ProjectProvisioningRequest("可移植工程", source=ProjectSourceRequest(str(source), FormatId.XML_XT)), context
        )
    )
    active = services.project_lifecycle.active
    project_ref, first_ref = active.project_ref, active.formal_variant_ref
    key = active.variant.snapshot().entries[0].entry_key
    commands = services.gui_project_commands
    _ok(commands.update_entry(key, context, translation="初版译文", stage=3))
    _ok(commands.replace_labels({key: {"reviewed"}}, {"reviewed": {"name": "已审核", "color": "#123456"}}, context))
    _ok(commands.save(context))
    _ok(commands.save_snapshot("初版快照", context))
    _ok(commands.create_variant("润色分支", context, copy_active=True))
    second_ref = services.project_lifecycle.active.formal_variant_ref
    _ok(commands.update_entry(key, context, translation="润色译文", stage=1))
    _ok(commands.save(context))
    _ok(commands.save_snapshot("润色快照", context))
    bundle = tmp_path / "project.transbridge"
    services.project_archive.export_project(str(bundle), context)
    return SimpleNamespace(
        services=services,
        context=context,
        bundle=bundle,
        source=source,
        project_ref=project_ref,
        variant_refs=(first_ref, second_ref),
        key=key,
    )


def test_roundtrip_keeps_all_variants_snapshots_and_relocates_sources_without_activation(
    tmp_path,
    services_factory,
    exported_project,
):
    source = exported_project
    target = services_factory("import")
    expected = {ref: source.services.variants.read_snapshot(ref).envelope.to_dict() for ref in source.variant_refs}
    source_snapshots = ProjectSnapshotRepository(source.services.root, source.services.filesystem)
    expected_snapshots = {ref: source_snapshots.list(ref) for ref in source.variant_refs}
    source_bytes = source.source.read_bytes()
    source.source.unlink()

    path = target.project_archive.import_project(str(source.bundle), source.context)

    assert path == target.projects.path_for(source.project_ref)
    assert target.project_lifecycle.active is None
    assert target.project_projection.snapshot() is None
    assert not (Path(target.root) / "active-project.json").exists()
    project = target.projects.read_snapshot(source.project_ref).envelope.data
    relocated = Path(project["sources"][0]["location"])
    assert relocated.is_relative_to(Path(target.root) / "projects" / source.project_ref.identity.encoded / "sources")
    assert relocated.read_bytes() == source_bytes
    assert project["sources"][0]["legacy"]["path"] == str(relocated)
    for ref in source.variant_refs:
        assert target.variants.read_snapshot(ref).envelope.to_dict() == expected[ref]
        repository = ProjectSnapshotRepository(target.root, target.filesystem)
        assert repository.list(ref) == expected_snapshots[ref]
        for snapshot in repository.list(ref):
            assert repository.load(snapshot.identity, ref) == source_snapshots.load(snapshot.identity, ref)
    catalog = json.loads((Path(target.root) / "project-catalog.json").read_bytes())
    assert set(catalog["projects"]) == {source.project_ref.identity.value}
    prepared = target.current_project_opener.prepare_path(path, source.context)
    assert _ok(prepared).recovery is None
    _ok(target.current_project_opener.activate(prepared.value, source.context))
    assert target.project_lifecycle.active.variant.snapshot().entries[0].translation == "润色译文"
    assert not target.project_lifecycle.active.dirty
    for ref in source.variant_refs:
        _ok(target.gui_project_commands.switch_v2(source.project_ref, ref, source.context))
        snapshot = target.project_lifecycle.active.variant.snapshot()
        assert snapshot.to_dto().envelope.to_dict() == expected[ref]


def _localized_plugin(path):
    def field(kind, payload):
        return struct.pack("<4sH", kind.encode(), len(payload)) + payload

    def record(kind, identifier, payload, flags=0):
        return struct.pack("<4sIIIIHH", kind.encode(), len(payload), flags, identifier, 0, 44, 0) + payload

    weapon = record("WEAP", 0x800, field("EDID", b"Weapon\0") + field("FULL", struct.pack("<I", 1)))
    path.write_bytes(
        record("TES4", 0, field("HEDR", struct.pack("<fII", 1.7, 1, 0x801)), 0x80)
        + struct.pack("<4sI4sIHHHH", b"GRUP", len(weapon) + 24, b"WEAP", 0, 0, 0, 0, 0)
        + weapon
    )
    strings = path.parent / "Strings"
    strings.mkdir()
    for identifier, suffix in enumerate(("strings", "dlstrings", "ilstrings"), 1):
        text = b"Portable sword\0"
        payload = text if suffix == "strings" else struct.pack("<I", len(text)) + text
        (strings / f"{path.stem}_English.{suffix}").write_bytes(
            struct.pack("<IIII", 1, len(payload), identifier, 0) + payload
        )
    (strings / "OtherMod_english.strings").write_bytes(b"not this plugin")
    return strings


def test_plugin_strings_and_sqlite_wal_assets_survive_portable_roundtrip(tmp_path, services_factory):
    source = tmp_path / "Portable.esp"
    strings = _localized_plugin(source)
    services = services_factory("plugin-export")
    context = RequestContext("gui", run_id="plugin-archive")
    _ok(
        services.gui_project_commands.create_project(
            ProjectProvisioningRequest("插件工程", source=ProjectSourceRequest(str(source))), context
        )
    )
    project_ref = services.project_lifecycle.active.project_ref
    owned = Path(services.root) / "projects" / project_ref.identity.encoded
    asset = owned / "terminology.sqlite3"
    (owned / "notes.txt").write_bytes(b"project-owned")
    (owned / "staging").mkdir()
    (owned / "staging" / "unfinished.txt").write_bytes(b"do not export")
    (Path(services.root) / "global-secret.txt").write_bytes(b"not project owned")
    bundle = tmp_path / "plugin.transbridge"
    with closing(sqlite3.connect(asset)) as database:
        assert database.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        database.execute("PRAGMA wal_autocheckpoint=0")
        database.execute("CREATE TABLE terms (original TEXT, translation TEXT)")
        database.commit()
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database.execute("INSERT INTO terms VALUES (?, ?)", ("Sword", "长剑"))
        database.commit()
        assert Path(f"{asset}-wal").stat().st_size > 0

        services.project_archive.export_project(str(bundle), context)
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            assert "assets/terminology.sqlite3" in names
            assert "assets/notes.txt" in names
            assert not any("staging" in name or "OtherMod" in name or "global-secret" in name for name in names)
            assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in names)
            assert not any(name.startswith("assets/variants/") for name in names)

        target = services_factory("plugin-import")
        target_path = target.project_archive.import_project(str(bundle), context)
        target_owned = Path(target.root) / "projects" / project_ref.identity.encoded
        with closing(sqlite3.connect(target_owned / "terminology.sqlite3")) as restored:
            assert restored.execute("PRAGMA quick_check").fetchone() == ("ok",)
            assert restored.execute("SELECT * FROM terms").fetchall() == [("Sword", "长剑")]
        relocated = Path(target.projects.read_snapshot(project_ref).envelope.data["sources"][0]["location"])
        for sidecar in strings.glob("Portable_*"):
            assert (relocated.parent / "Strings" / sidecar.name).read_bytes() == sidecar.read_bytes()
        source.unlink()
        for sidecar in strings.iterdir():
            sidecar.unlink()
        prepared = target.current_project_opener.prepare_path(target_path, context)
        assert _ok(prepared).recovery is None
        _ok(target.current_project_opener.activate(prepared.value, context))
        assert target.project_lifecycle.active.variant.snapshot().entries


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/outside.txt",
        "C:/outside.txt",
        "assets\\outside.txt",
        "assets/../outside.txt",
        "assets/./note.txt",
        "assets//note.txt",
        "assets/CON",
        "assets/note:stream",
        "assets/trailing.",
        "assets/trailing ",
        "assets/sources/alias.txt",
        "assets/SOURCES/alias.txt",
        "assets/variants/forged.json",
        "assets/Variants/forged.json",
        "undeclared.txt",
    ],
)
def test_rejects_unsafe_or_reserved_members_before_writing(tmp_path, services_factory, exported_project, name):
    manifest, members = _bundle_content(exported_project.bundle)
    bundle = _write_bundle(tmp_path / "bad-path.transbridge", manifest, [*members, (name, b"unsafe")])
    target = services_factory("target")
    before = _file_state(target.root)

    with pytest.raises(ValueError):
        target.project_archive.import_project(str(bundle), exported_project.context)

    assert _file_state(target.root) == before
    assert not (tmp_path / "outside.txt").exists()


def test_rejects_symlink_and_case_colliding_members(tmp_path, services_factory, exported_project):
    manifest, members = _bundle_content(exported_project.bundle)
    symlink = zipfile.ZipInfo("assets/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    target = services_factory("target")
    before = _file_state(target.root)
    for index, additions in enumerate(([(symlink, b"../../outside")], [("assets/note", b"a"), ("assets/NOTE", b"b")])):
        bundle = _write_bundle(tmp_path / f"bad-member-{index}.transbridge", manifest, [*members, *additions])
        with pytest.raises(ValueError):
            target.project_archive.import_project(str(bundle), exported_project.context)
        assert _file_state(target.root) == before


@pytest.mark.parametrize(
    "corruption",
    [
        "variant-missing",
        "variant-duplicate",
        "variant-owner",
        "snapshot-owner",
        "snapshot-variant-owner",
        "source-missing",
        "source-duplicate",
        "source-bytes",
        "source-fingerprint",
    ],
)
def test_rejects_incomplete_duplicate_foreign_and_tampered_payloads(
    tmp_path,
    services_factory,
    exported_project,
    corruption,
):
    manifest, members = _bundle_content(exported_project.bundle)
    if corruption == "variant-missing":
        manifest["variants"].pop()
    elif corruption == "variant-duplicate":
        manifest["variants"].append(deepcopy(manifest["variants"][0]))
    elif corruption == "variant-owner":
        manifest["variants"][0]["data"]["project_id"] = uuid4().hex
    elif corruption == "snapshot-owner":
        manifest["snapshots"][0]["project_id"] = uuid4().hex
    elif corruption == "snapshot-variant-owner":
        manifest["snapshots"][0]["variant"]["data"]["project_id"] = uuid4().hex
    elif corruption == "source-missing":
        manifest["sources"].clear()
    elif corruption == "source-duplicate":
        manifest["sources"].append(deepcopy(manifest["sources"][0]))
    elif corruption == "source-bytes":
        source_member = manifest["sources"][0]["member"]
        members = [(name, b"tampered" if name == source_member else payload) for name, payload in members]
    else:
        manifest["project"]["data"]["sources"][0]["fingerprint"] = "0" * 64
    bundle = _write_bundle(tmp_path / "tampered.transbridge", manifest, members)
    target = services_factory("target")
    before = _file_state(target.root)

    with pytest.raises((ValueError, RuntimeError)):
        target.project_archive.import_project(str(bundle), exported_project.context)

    assert _file_state(target.root) == before
    assert target.project_lifecycle.active is None


@pytest.mark.parametrize("conflict", ["identity", "name"])
def test_existing_project_identity_or_name_never_overwritten(services_factory, exported_project, conflict):
    source = exported_project
    target = services_factory("target")
    if conflict == "identity":
        target.project_archive.import_project(str(source.bundle), source.context)
    else:
        _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("可移植工程"), source.context))
    before = _file_state(target.root)
    active = target.project_lifecycle.active

    with pytest.raises(ValueError):
        target.project_archive.import_project(str(source.bundle), source.context)

    assert _file_state(target.root) == before
    assert target.project_lifecycle.active is active


def test_identity_conflict_can_be_imported_as_a_reidentified_copy(services_factory, exported_project):
    source = exported_project
    target = services_factory("target-copy")
    original_path = target.project_archive.import_project(str(source.bundle), source.context)
    before_original = Path(original_path).read_bytes()

    inspection = target.project_archive.inspect_import(str(source.bundle), source.context)
    copied_path = target.project_archive.import_project(
        str(source.bundle),
        source.context,
        requested_name="可移植工程副本",
        copy_on_identity_conflict=True,
    )

    assert inspection.identity_conflict
    assert inspection.name_conflict
    assert copied_path != original_path
    assert Path(original_path).read_bytes() == before_original
    copied_document = json.loads(Path(copied_path).read_bytes())
    assert copied_document["id"] != source.project_ref.identity.value
    assert copied_document["data"]["name"] == "可移植工程副本"
    assert set(copied_document["data"]["variant_ids"]).isdisjoint({ref.identity.value for ref in source.variant_refs})
    copied_project_id = copied_document["id"]
    for variant_id in copied_document["data"]["variant_ids"]:
        ref = VariantRef(VariantId(variant_id), ProjectId(copied_project_id))
        assert target.variants.read_snapshot(ref).envelope.data["project_id"] == copied_project_id
        for snapshot in ProjectSnapshotRepository(target.root, target.filesystem).list(ref):
            assert ProjectSnapshotRepository(target.root, target.filesystem).load(snapshot.identity, ref).ref == ref


def test_same_name_conflict_can_be_renamed_without_changing_archive_identity(services_factory, exported_project):
    target = services_factory("target-rename")
    _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("可移植工程"), exported_project.context))

    inspection = target.project_archive.inspect_import(str(exported_project.bundle), exported_project.context)
    imported = target.project_archive.import_project(
        str(exported_project.bundle),
        exported_project.context,
        requested_name="恢复的可移植工程",
    )

    assert not inspection.identity_conflict
    assert inspection.name_conflict
    document = json.loads(Path(imported).read_bytes())
    assert document["id"] == exported_project.project_ref.identity.value
    assert document["data"]["name"] == "恢复的可移植工程"


def test_catalog_only_project_identity_is_not_silently_reassigned(services_factory, exported_project):
    target = services_factory("target")
    root = Path(target.root)
    root.mkdir(exist_ok=True)
    catalog = {
        "schema_version": 1,
        "projects": {
            exported_project.project_ref.identity.value: {"name": "待恢复的工程", "name_key": "待恢复的工程"},
        },
    }
    (root / "project-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    before = _file_state(root)

    with pytest.raises(ValueError):
        target.project_archive.import_project(str(exported_project.bundle), exported_project.context)

    assert _file_state(root) == before


def test_import_publishes_catalog_without_switching_an_existing_active_project(services_factory, exported_project):
    target = services_factory("target")
    context = exported_project.context
    _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("正在编辑的工程"), context))
    active = target.project_lifecycle.active
    pointer = (Path(target.root) / "active-project.json").read_bytes()
    projection = target.project_projection.snapshot().to_dict()

    imported = target.project_archive.import_project(str(exported_project.bundle), context)

    assert Path(imported).is_file()
    assert target.project_lifecycle.active is active
    assert (Path(target.root) / "active-project.json").read_bytes() == pointer
    assert target.project_projection.snapshot().to_dict() == projection
    catalog = json.loads((Path(target.root) / "project-catalog.json").read_bytes())["projects"]
    assert set(catalog) == {active.project_ref.identity.value, exported_project.project_ref.identity.value}


def test_existing_project_asset_is_preserved_after_rejecting_import(tmp_path, services_factory, exported_project):
    manifest, members = _bundle_content(exported_project.bundle)
    bundle = _write_bundle(tmp_path / "asset.transbridge", manifest, [*members, ("assets/notes.txt", b"incoming")])
    target = services_factory("target")
    owned = Path(target.root) / "projects" / exported_project.project_ref.identity.encoded
    owned.mkdir(parents=True)
    (owned / "notes.txt").write_bytes(b"existing asset")
    before = _file_state(target.root)

    with pytest.raises(ValueError, match="覆盖"):
        target.project_archive.import_project(str(bundle), exported_project.context)

    assert _file_state(target.root) == before


class _FailingFilesystem(OsPersistenceFilesystem):
    fail_destination = None
    after_replace = False
    triggered = False

    def replace(self, source, destination):
        if self.fail_destination == Path(destination) and not self.triggered:
            self.triggered = True
            if self.after_replace:
                super().replace(source, destination)
            raise OSError("injected archive publication failure")
        super().replace(source, destination)


@pytest.mark.parametrize("stage", ["asset", "variant", "snapshot", "project", "catalog"])
@pytest.mark.parametrize("after_replace", [False, True])
def test_publication_failure_rolls_back_every_new_record_and_preserves_active_project(
    services_factory,
    exported_project,
    stage,
    after_replace,
):
    source = exported_project
    filesystem = _FailingFilesystem()
    target = services_factory("target", filesystem=filesystem)
    _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("已存在的工程"), source.context))
    active = target.project_lifecycle.active
    root = Path(target.root)
    manifest, _members = _bundle_content(source.bundle)
    if stage == "asset":
        destination = root / "projects" / source.project_ref.identity.encoded / manifest["sources"][0]["member"]
    elif stage == "variant":
        destination = Path(target.variants.path_for(source.variant_refs[-1]))
    elif stage == "snapshot":
        repository = ProjectSnapshotRepository(source.services.root, source.services.filesystem)
        destination = root / "snapshots" / f"{repository.list(source.variant_refs[-1])[0].identity}.json"
    elif stage == "project":
        destination = Path(target.projects.path_for(source.project_ref))
    else:
        destination = root / "project-catalog.json"
    filesystem.fail_destination = destination
    filesystem.after_replace = after_replace
    before = _file_state(root)

    with pytest.raises((OSError, RuntimeError)):
        target.project_archive.import_project(str(source.bundle), source.context)

    assert filesystem.triggered
    assert _file_state(root) == before
    assert target.project_lifecycle.active is active
    assert not active.dirty


@pytest.mark.parametrize("after_replace", [False, True])
def test_catalog_failure_on_first_import_leaves_no_partial_project(services_factory, exported_project, after_replace):
    filesystem = _FailingFilesystem()
    target = services_factory("target", filesystem=filesystem)
    filesystem.fail_destination = Path(target.root) / "project-catalog.json"
    filesystem.after_replace = after_replace
    before = _file_state(target.root)

    with pytest.raises(OSError, match="injected"):
        target.project_archive.import_project(str(exported_project.bundle), exported_project.context)

    assert filesystem.triggered
    assert _file_state(target.root) == before
    assert target.project_lifecycle.active is None


def test_changed_source_or_dirty_project_does_not_replace_an_existing_archive(tmp_path, exported_project):
    source = exported_project
    destination = tmp_path / "keep.transbridge"
    destination.write_bytes(b"previous bundle")
    source.source.write_bytes(b"changed source")
    with pytest.raises(ValueError, match="来源文件已改变"):
        source.services.project_archive.export_project(str(destination), source.context)
    assert destination.read_bytes() == b"previous bundle"
    _ok(source.services.gui_project_commands.update_entry(source.key, source.context, translation="未保存"))
    with pytest.raises(ValueError, match="保存"):
        source.services.project_archive.export_project(str(destination), source.context)
    assert destination.read_bytes() == b"previous bundle"
    assert not list(tmp_path.glob(".transbridge-export-*"))
