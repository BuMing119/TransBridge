from dataclasses import replace
from itertools import count
import json
from pathlib import Path
import struct

import pytest

from tests.application.projects.test_source_update_plugin import _plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, Stage
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.models import LifecycleSave
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.config.repository import ConfigRepository
from transbridge.config.text_encoding import (
    TEXT_INPUT_ENCODING,
    TEXT_OUTPUT_ENCODING,
    TEXT_SOURCE_LANGUAGE,
    TEXT_TARGET_LANGUAGE,
    TextEncodingDefaults,
    TextEncodings,
)
from transbridge.persistence.project_store import source_updates
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.save_verification import save_matches
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.variant import VariantSnapshot


@pytest.fixture
def project(tmp_path):
    ids = count()
    defaults = TextEncodingDefaults(ConfigRepository(tmp_path / "defaults.ini"))
    services = build_persistence_v2_services(
        tmp_path / "workspace",
        id_factory=lambda: str(next(ids)),
        timestamp_factory=lambda: "2026-11-14T00:00:00+00:00",
        text_encoding_defaults=defaults,
    )
    yield (
        services,
        services.project_provisioning,
        services.project_text_encodings,
        defaults,
        RequestContext("encoding-test", run_id="encoding-test"),
    )
    services.close()


def _open(project, name, *sources):
    services, provisioning, _, _, context = project
    request = ProjectProvisioningRequest(name, additional_sources=sources)
    prepared = provisioning.prepare(request, context)
    assert prepared.is_success, prepared.diagnostics
    result = provisioning.commit(prepared.value.token, context)
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    return services.projects.path_for(active.project_ref), active


def _strings(path, string_id, text, *, length_prefixed=False, encoding="utf-8"):
    payload = text.encode(encoding) + b"\x00"
    if length_prefixed:
        payload = struct.pack("<I", len(payload)) + payload
    path.write_bytes(struct.pack("<IIII", 1, len(payload), string_id, 0) + payload)


def _source(path, format_id):
    # A request-local historical codec cannot supersede the owning Project.
    return ProjectSourceRequest(str(path), format_id, options=(("input_encoding", "cp950"),))


def _set_encodings(path, encoding, encodings, context):
    prepared = encoding.prepare(path, encodings, context)
    assert prepared.is_success, prepared.diagnostics
    assert prepared.value.sources == ()
    committed = encoding.commit(prepared.value.token, context)
    assert committed.is_success, committed.diagnostics
    return committed.value


def _target(service, path, context, format_id, *, name=None):
    targets = service.list_sources(path, context)
    assert targets.is_success, targets.diagnostics
    return next(
        target.source_id
        for target in targets.value
        if target.format_id is format_id and (name is None or target.label == name)
    )


@pytest.mark.parametrize("format_id", [FormatId.DLSTRINGS, FormatId.ILSTRINGS])
def test_saved_length_prefixed_original_preserves_embedded_nul(project, tmp_path, format_id):
    services, _, _, _, context = project
    text = "Café\x00Continuation\x00"
    source = tmp_path / f"Source.{format_id.value.split('.')[-1]}"
    _strings(source, 42, text, length_prefixed=True)
    _open(project, "Length-prefixed original", _source(source, format_id))
    source.unlink()

    reopened = services.current_project_opener.prepare_active(context)
    assert reopened.is_success, reopened.diagnostics
    assert [entry.original for entry in reopened.value.hydrations[0].entries] == [text]


def test_prepared_source_must_match_current_project_input_encoding(project, tmp_path):
    from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer

    services, _, _, defaults, context = project
    defaults.save(TextEncodings("cp1252"))
    path, active = _open(project, "Encoding authority")
    source = tmp_path / "ambiguous.esp"
    _plugin(source, [(0x800, "Café")])
    preparer = TranslationIoProjectSourcePreparer()
    wrong = preparer.prepare_source(ProjectSourceRequest(str(source)), context, role="primary", common_options=())
    before = Path(path).read_bytes()
    rejected = services.gui_project_commands.add_prepared_source(
        wrong, context, expected_variant_ref=active.formal_variant_ref
    )
    assert not rejected.is_success
    assert rejected.diagnostics[0].code == "PROJECT_SOURCE_ENCODING_CHANGED"
    assert services.project_lifecycle.active == active
    assert Path(path).read_bytes() == before
    correct = preparer.prepare_source(
        ProjectSourceRequest(str(source), options=tuple(TextEncodings("cp1252").read_options().items())),
        context,
        role="primary",
        common_options=(),
    )
    added = services.gui_project_commands.add_prepared_source(
        correct, context, expected_variant_ref=active.formal_variant_ref
    )
    assert added.is_success, added.diagnostics
    assert added.value.hydration.entries[0].original == "CafÃ©"


def test_defaults_are_copied_once_and_the_archive_preserves_settings(project, tmp_path):
    services, _, encoding, defaults, context = project
    first, _ = _open(project, "First")
    selected = TextEncodings("cp1252", "cp936", "french", "japanese")
    defaults.save(selected)
    second, active = _open(project, "Second")
    assert encoding.read(first, context).value == TextEncodings()
    assert encoding.read(second, context).value == selected
    assert encoding.active_encodings(context) == selected

    package = services.project_archive.export_project(str(tmp_path / "encoding.transbridge"), context)
    copied = services.project_archive.import_project(
        package, context, requested_name="Copied", copy_on_identity_conflict=True
    )
    assert encoding.read(copied, context).value == selected
    with project_connection(Path(second), write=True) as connection:
        connection.execute(
            "UPDATE project SET settings = json_remove(settings, ?, ?, ?, ?)",
            tuple(f"$.{key}" for key in selected.to_project_data()),
        )
    reopened = services.current_project_opener.open_path(second, context)
    assert reopened.is_success, reopened.diagnostics
    assert services.project_lifecycle.active.project_ref == active.project_ref
    assert encoding.active_encodings(context) == TextEncodings()


@pytest.mark.parametrize(
    "kept",
    [
        {},
        {TEXT_INPUT_ENCODING: "utf-8", TEXT_OUTPUT_ENCODING: "utf-8"},
        {TEXT_INPUT_ENCODING: "Windows-1252", TEXT_TARGET_LANGUAGE: "japanese"},
        {TEXT_OUTPUT_ENCODING: "cp936", TEXT_SOURCE_LANGUAGE: "russian"},
    ],
)
def test_saving_clean_legacy_project_persists_only_missing_encoding_defaults(project, tmp_path, kept):
    services, _, encoding, defaults, context = project
    source = tmp_path / "source.esp"
    _plugin(source, [(0x800, "Original")])
    path, active = _open(project, "Legacy defaults", ProjectSourceRequest(str(source)))
    row = active.variant.snapshot().entries[0]
    assert services.gui_project_commands.update_entry(row.entry_key, context, translation="译文", stage=3).is_success
    assert services.gui_project_commands.save(context).is_success
    with project_connection(Path(path), write=True) as connection:
        settings = json.loads(connection.execute("SELECT settings FROM project").fetchone()[0])
        for name in TextEncodings().to_project_data():
            settings.pop(name)
        settings.update(kept)
        connection.execute("UPDATE project SET settings = ?", (json.dumps(settings),))
    defaults.save(TextEncodings("cp950", "cp1251", "polish", "german"))
    source.unlink()  # Opening and saving must not reparse unavailable source bytes.
    assert services.current_project_opener.open_path(path, context).is_success
    active = services.project_lifecycle.active
    before = active.variant.snapshot()
    assert not active.dirty
    save = LifecycleSave.capture(active)
    with project_connection(Path(path)) as connection:
        opened = json.loads(connection.execute("SELECT settings FROM project").fetchone()[0])
        assert not save_matches(connection, save)
    assert {key: opened[key] for key in TextEncodings().to_project_data() if key in opened} == kept

    result = services.gui_project_commands.save(context)

    assert result.is_success, result.diagnostics
    expected = {**TextEncodings().to_project_data(), **kept}
    with project_connection(Path(path)) as connection:
        saved = json.loads(connection.execute("SELECT settings FROM project").fetchone()[0])
        assert save_matches(connection, save)
    assert {key: saved[key] for key in expected} == expected
    assert services.project_lifecycle.active.variant.snapshot() == before
    assert services.project_lifecycle.active.project.envelope.revision == active.project.envelope.revision
    assert not services.project_lifecycle.active.dirty
    assert encoding.active_encodings(context) == TextEncodings.from_project_data(expected)
    # Once acknowledged, an otherwise unchanged save is again a no-op.
    database = Path(path).read_bytes()
    assert services.gui_project_commands.save(context).is_success
    assert Path(path).read_bytes() == database


def test_explicit_source_updates_redecode_all_variants_and_preserve_translations(project, tmp_path):
    services, _, encoding, defaults, context = project
    defaults.save(TextEncodings("cp1252", "cp1252"))
    plugin, strings = tmp_path / "duplicate.esp", tmp_path / "messages.dlstrings"
    _plugin(plugin, [(0x800, "Café"), (0x800, "Pokémon"), (0x801, "Début"), (0x802, "Étude")])
    _strings(strings, 3, "Résumé", length_prefixed=True)
    path, active = _open(project, "Reread", _source(plugin, FormatId.PLUGIN_SSE), _source(strings, FormatId.DLSTRINGS))
    entries = active.variant.snapshot().entries
    sources = services.projects.store_for(active.project_ref.identity).sources()
    strings_namespace = next(source.namespace for source in sources if source.format_id is FormatId.DLSTRINGS)
    assert {item.entry_key.original for item in entries if item.entry_key.original} == {"CafÃ©", "PokÃ©mon"}
    staged = {"CafÃ©": ("reviewed", Stage.REVIEWED), "PokÃ©mon": ("locked", Stage.LOCKED)}
    for item in entries:
        if item.entry_key.original in staged:
            translation, stage = staged[item.entry_key.original]
        elif item.entry_key.namespace == strings_namespace:
            translation, stage = "strings", Stage.REVIEWED
        else:
            translation, stage = (
                ("hidden", Stage.HIDDEN) if "Weapon2049:" in item.entry_key.local_key else ("", Stage.REVIEWED)
            )
        assert services.gui_project_commands.update_entry(
            item.entry_key, context, translation=translation, stage=stage.value
        ).is_success
    key = next(item.entry_key for item in entries if item.entry_key.original == "CafÃ©")
    command = services.gui_project_commands.replace_labels({key: {"label"}}, {"label": {"name": "Label"}}, context)
    assert command.is_success, command.diagnostics
    assert services.gui_project_commands.save(context).is_success
    assert services.gui_project_commands.create_variant("Second", context, copy_active=True).is_success
    assert services.gui_project_commands.save(context).is_success
    plugin_bytes, strings_bytes = plugin.read_bytes(), strings.read_bytes()

    proposed = TextEncodings("utf-8", "cp936", "french")
    settings_result = _set_encodings(path, encoding, proposed, context)
    assert settings_result.backup_path is None
    snapshots = []
    updates = services.project_source_updates
    for source, format_id, changed in ((strings, FormatId.DLSTRINGS, 1), (plugin, FormatId.PLUGIN_SSE, 4)):
        preview = updates.prepare(path, _target(updates, path, context, format_id), str(source), context)
        assert preview.is_success, preview.diagnostics
        assert (preview.value.added, preview.value.removed, preview.value.changed, preview.value.variant_count) == (
            0,
            0,
            changed,
            2,
        )
        committed = updates.commit(preview.value.token, context)
        assert committed.is_success, committed.diagnostics
        snapshots.append(committed.value.backup_path.removeprefix("project-snapshot:"))
        assert not updates.commit(preview.value.token, context).is_success
    assert encoding.read(path, context).value == proposed
    assert plugin.read_bytes() == plugin_bytes and strings.read_bytes() == strings_bytes

    opened = services.current_project_opener.open_path(path, context)
    assert opened.is_success, opened.diagnostics
    assert {entry.original for source in opened.value["hydrations"] for entry in source.entries} == {
        "Café",
        "Début",
        "Étude",
        "Pokémon",
        "Résumé",
    }
    active = services.project_lifecycle.active
    for identity in active.project.envelope.data["variant_ids"]:
        ref = VariantRef(VariantId(identity), active.project_ref.identity)
        current = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref).entries
        assert {item.translation: item.stage for item in current} == {
            "reviewed": Stage.QUESTIONABLE,
            "locked": Stage.LOCKED,
            "strings": Stage.QUESTIONABLE,
            "hidden": Stage.HIDDEN,
            "": Stage.REVIEWED,
        }
        assert next(item for item in current if item.translation == "reviewed").labels == ("label",)
        history = ProjectHistory(services.projects).list(ref)
        assert {item.identity.rsplit("-", 1)[0] for item in history} == set(snapshots)

    restore_point = next(
        item for item in services.project_snapshots.list(context) if item.identity.startswith(snapshots[-1])
    )
    recovered = services.project_snapshots.restore(restore_point.identity, context)
    assert recovered.is_success, recovered.diagnostics
    assert encoding.read(path, context).value == proposed
    for identity in active.project.envelope.data["variant_ids"]:
        ref = VariantRef(VariantId(identity), active.project_ref.identity)
        current = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref).entries
        assert {item.entry_key.original: item.translation for item in current if item.entry_key.original} == {
            "CafÃ©": "reviewed",
            "PokÃ©mon": "locked",
        }


def test_source_update_can_supply_moved_input_without_changing_project_on_cancel(project, tmp_path):
    services, _, encoding, defaults, context = project
    defaults.save(TextEncodings("cp1252"))
    strings = tmp_path / "messages.strings"
    _strings(strings, 1, "Café")
    path, _ = _open(project, "Moved source", _source(strings, FormatId.STRINGS))
    selected = tmp_path / "moved.strings"
    strings.rename(selected)
    _set_encodings(path, encoding, TextEncodings(), context)
    before = Path(path).read_bytes()

    updates = services.project_source_updates
    source_id = _target(updates, path, context, FormatId.STRINGS)
    missing = updates.prepare(path, source_id, str(strings), context)
    assert not missing.is_success
    preview = updates.prepare(path, source_id, str(selected), context)
    assert preview.is_success, preview.diagnostics
    assert not updates.commit(preview.value.token, replace(context, owner_id="other")).is_success
    updates.discard(preview.value.token, context)
    assert not updates.commit(preview.value.token, context).is_success
    assert Path(path).read_bytes() == before

    preview = updates.prepare(path, source_id, str(selected), context)
    result = updates.commit(preview.value.token, context)
    assert result.is_success, result.diagnostics
    assert services.projects.store_for(services.project_lifecycle.active.project_ref.identity).sources()[
        0
    ].reference_path == str(selected)


def test_source_language_updates_decoder_and_retains_saved_localized_suffix(project, tmp_path):
    services, _, encoding, _, context = project
    plugin = tmp_path / "Localized.esp"
    _plugin(plugin, [(0x800, 1)], localized=True)
    strings = tmp_path / "Strings"
    strings.mkdir()
    # cp1250 byte 0x8C is Ś; cp1252 decodes the same byte as Œ.
    _strings(strings / "Localized_English.strings", 1, "Ś", encoding="cp1250")
    _strings(strings / "Localized_Polish.strings", 1, "Unrelated localized input")
    path, active = _open(project, "Localized source language", ProjectSourceRequest(str(plugin)))
    old = services.current_project_opener.prepare_active(context).value.hydrations[0]
    assert old.entries[0].original == "Œ"
    row = active.variant.snapshot().entries[0]
    assert services.gui_project_commands.update_entry(
        row.entry_key, context, translation="Translated", stage=Stage.REVIEWED.value
    ).is_success
    assert services.gui_project_commands.save(context).is_success

    _set_encodings(path, encoding, TextEncodings(source_language="polish"), context)
    updates = services.project_source_updates
    preview = updates.prepare(path, _target(updates, path, context, FormatId.PLUGIN_SSE), str(plugin), context)
    assert preview.is_success, preview.diagnostics
    assert (preview.value.added, preview.value.removed, preview.value.changed) == (0, 0, 1)
    committed = updates.commit(preview.value.token, context)
    assert committed.is_success, committed.diagnostics

    hydration = services.current_project_opener.prepare_active(context).value.hydrations[0]
    metadata = dict(hydration.source_snapshot.metadata)
    assert metadata["localized_language"] == "english"
    assert metadata["source_language"] == "polish"
    assert hydration.entries[0].original == "Ś"
    (updated,) = services.project_lifecycle.active.variant.snapshot().entries
    assert (updated.translation, updated.stage) == ("Translated", Stage.QUESTIONABLE)


@pytest.mark.parametrize("invalidate", ["changed_source", "edit", "cancel", "settings"])
def test_stale_or_cancelled_update_keeps_saved_sources_and_history(project, tmp_path, invalidate):
    services, _, encoding, defaults, context = project
    defaults.save(TextEncodings("cp1252"))
    first = tmp_path / "first.strings"
    _strings(first, 1, "Café")
    path, active = _open(project, "Rollback", _source(first, FormatId.STRINGS))
    _set_encodings(path, encoding, TextEncodings(), context)
    updates = services.project_source_updates
    preview = updates.prepare(
        path, _target(updates, path, context, FormatId.STRINGS, name=first.name), str(first), context
    )
    assert preview.is_success, preview.diagnostics
    if invalidate == "changed_source":
        _strings(first, 1, "Different")
    elif invalidate == "edit":
        assert services.gui_project_commands.update_entry(
            active.variant.snapshot().entries[0].entry_key, context, translation="dirty"
        ).is_success
    elif invalidate == "settings":
        _set_encodings(path, encoding, TextEncodings(source_language="french"), context)
    before = Path(path).read_bytes()
    rejected = updates.commit(preview.value.token, context, cancelled=lambda: invalidate == "cancel")
    assert not rejected.is_success
    assert Path(path).read_bytes() == before
    assert encoding.active_encodings(context) == TextEncodings(
        source_language="french" if invalidate == "settings" else "english"
    )
    assert not ProjectHistory(services.projects).list(active.formal_variant_ref)


def test_setting_changes_preserve_dirty_translations_without_source_io(project, tmp_path):
    services, _, encoding, _, context = project
    strings = tmp_path / "messages.strings"
    _strings(strings, 1, "Café")
    path, active = _open(project, "Settings only", _source(strings, FormatId.STRINGS))
    original_variant_revision = active.variant.revision
    key = active.variant.snapshot().entries[0].entry_key
    assert services.gui_project_commands.update_entry(key, context, translation="unsaved").is_success
    strings.unlink()

    proposed = TextEncodings("cp1252", "cp936", "french", "japanese")
    preview = encoding.prepare(path, proposed, context)
    assert preview.is_success and preview.value.sources == ()
    result = encoding.commit(preview.value.token, context)
    assert result.is_success, result.diagnostics
    assert result.value.backup_path is None
    active = services.project_lifecycle.active
    assert active.dirty and active.variant.snapshot().entries[0].translation == "unsaved"
    assert active.persisted_variant_revision == original_variant_revision
    assert encoding.read(path, context).value == proposed
    assert active.variant.snapshot().entries[0].entry_key == key
    assert not ProjectHistory(services.projects).list(active.formal_variant_ref)


def test_database_write_failure_rolls_back_source_revision_and_all_versions(project, tmp_path, monkeypatch):
    services, _, encoding, defaults, context = project
    defaults.save(TextEncodings("cp1252"))
    first, second = tmp_path / "first.strings", tmp_path / "second.strings"
    _strings(first, 1, "Café")
    _strings(second, 2, "Résumé")
    path, active = _open(
        project, "Publish rollback", _source(first, FormatId.STRINGS), _source(second, FormatId.STRINGS)
    )
    assert services.gui_project_commands.create_variant("Second", context, copy_active=True).is_success
    assert services.gui_project_commands.save(context).is_success
    _set_encodings(path, encoding, TextEncodings(), context)
    updates = services.project_source_updates
    preview = updates.prepare(
        path, _target(updates, path, context, FormatId.STRINGS, name=first.name), str(first), context
    )
    assert preview.is_success, preview.diagnostics
    preimage = Path(path).read_bytes()
    written = []
    write = source_updates.write_variant

    def fail_second(*args):
        written.append(args[1].ref)
        if len(written) == 2:
            raise OSError("simulated second Variant write failure")
        return write(*args)

    monkeypatch.setattr(source_updates, "write_variant", fail_second)
    result = updates.commit(preview.value.token, context)
    assert not result.is_success
    assert len(written) == 2
    assert Path(path).read_bytes() == preimage
    assert encoding.active_encodings(context) == TextEncodings()
    assert not ProjectHistory(services.projects).list(active.formal_variant_ref)
