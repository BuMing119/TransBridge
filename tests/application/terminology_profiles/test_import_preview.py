from __future__ import annotations

from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportService,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.application.terminology_profiles.import_preview import ImportPreviewOutcome, import_preview_rows


def test_preview_rows_cover_all_mappings_and_excluded_sources_without_changing_import():
    def decision(identity, original, target, scope=TermScope()):
        return TermDecision(identity, "p", "v", original, original.casefold(), target, scope, DecisionStatus.ADOPTED)

    base = EffectiveTerminologySnapshot(
        "p",
        "v",
        EffectiveSnapshotStatus.READY,
        version_id="base",
        content_digest="digest",
        decisions=(
            decision("a", "Adopted", "原译"),
            decision("k", "Kept", "保留"),
            decision("c", "Conflict", "默认"),
            decision("sp", "Scoped", "项目译名"),
            decision("sl", "Scoped", "插件译名", TermScope.plugin("Demo.esp")),
        ),
    )
    source = TerminologySourceSnapshot.capture(
        "json",
        "Source",
        tuple(
            TerminologySourceEntry(original, target)
            for original, target in (
                (" adopted ", "新译"),
                ("Conflict", "甲"),
                ("CONFLICT", "乙"),
                ("Scoped", "来源译名"),
                ("Only", "独有甲"),
                ("ONLY", "独有乙"),
                ("Only", "独有甲"),
            )
        ),
    )
    importer = TerminologyProfileImportService(TerminologyProfileService(InMemoryTerminologyProfileRepository()))
    preview = importer.preview("p", "v", base, source)
    digest = preview.content.content_digest
    rows = import_preview_rows(preview)
    assert len(rows) == preview.base_mapping_count + preview.source_only_term_count == 6
    assert len({row.row_id for row in rows}) == 6
    by_name = {row.original: row for row in rows if row.scope_kind == "project"}
    assert by_name["Adopted"].outcome is ImportPreviewOutcome.ADOPTED
    assert by_name["Adopted"].translation == "新译"
    assert by_name["Kept"].outcome is ImportPreviewOutcome.KEPT
    assert by_name["Conflict"].outcome is ImportPreviewOutcome.SOURCE_CONFLICT
    assert by_name["Conflict"].source_translations == ("乙", "甲")
    scoped = [row for row in rows if row.outcome is ImportPreviewOutcome.SCOPE_AMBIGUOUS]
    assert len(scoped) == 2
    assert {row.translation for row in scoped} == {"项目译名", "插件译名"}
    assert all(set(row.base_scopes) == {"project", "plugin:Demo.esp"} for row in scoped)
    source_only = rows[-1]
    assert source_only.outcome is ImportPreviewOutcome.SOURCE_ONLY
    assert source_only.source_translations == ("独有乙", "独有甲")
    assert source_only.translation == ""
    assert source_only.scope_kind is None
    assert preview.content.content_digest == digest
    result = importer.create_and_publish("p", "v", "方案", preview)
    assert len(result.published.content.mappings) == 5
    assert all(item.original.casefold() != "only" for item in result.published.content.mappings)
