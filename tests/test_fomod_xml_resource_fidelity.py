"""FOMOD S04 XML/resource fidelity and safe hash reuse contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from transbridge.fileops import (
    HASH_POLICY_VERSION,
    FilterAction,
    FilterRules,
    HashReuseEvidence,
    ResourceRole,
    classify_files,
    diff_directories,
)
from transbridge.fomod.pipeline import FomodPipeline
from transbridge.fomod.xml_fidelity import (
    FomodXmlError,
    XmlTextCandidate,
    parse_fomod_xml,
    patch_and_validate,
    process_fomod_xml_file,
)


def _xml(*, encoding: str = "utf-8") -> str:
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        '<config xmlns:vendor="urn:vendor" vendor:revision="7">'
        "<moduleName>Original</moduleName>"
        "<!--preserve-comment-->"
        '<vendor:extension enabled="yes"><vendor:value>opaque</vendor:value></vendor:extension>'
        '<installSteps><installStep name="Install"><image path="assets/banner.png"/>'
        "</installStep></installSteps></config>\n"
    )


def _raw_xml(encoding: str) -> bytes:
    text = _xml(encoding="utf-16" if encoding == "utf-16-le" else encoding)
    if encoding == "utf-16-le":
        return b"\xff\xfe" + text.encode("utf-16-le")
    return b"\xef\xbb\xbf" + text.encode("utf-8")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le"])
def test_patch_preserves_bom_namespaces_unknown_nodes_attributes_and_resources(encoding):
    snapshot = parse_fomod_xml(_raw_xml(encoding))
    module = next(item for item in snapshot.values if item.text == "Original")
    step = next(item for item in snapshot.values if item.text == "Install")

    output, report = patch_and_validate(
        snapshot,
        (
            XmlTextCandidate(module.locator, module.text, "Translated", "test"),
            XmlTextCandidate(step.locator, step.text, "Translated step", "test"),
        ),
    )

    parsed = parse_fomod_xml(output)
    assert parsed.encoding == encoding
    assert parsed.bom == snapshot.bom
    assert parsed.namespaces == snapshot.namespaces
    assert parsed.resource_references == ("assets/banner.png",)
    assert "preserve-comment" in output[len(parsed.bom) :].decode(parsed.encoding)
    assert len(report.changed_nodes) == 2
    assert report.preserved_resources == ("assets/banner.png",)


def test_no_change_is_byte_identical_and_invalid_xml_is_fatal():
    raw = _raw_xml("utf-8")
    output, report = patch_and_validate(parse_fomod_xml(raw), ())
    assert output == raw
    assert report.source_hash == report.output_hash
    with pytest.raises(FomodXmlError) as captured:
        parse_fomod_xml(b"<config><broken></config>")
    assert captured.value.code == "FOMOD_XML_PARSE_FAILED"


def test_lossy_lexical_construct_is_reported_and_rewrite_fails_closed():
    snapshot = parse_fomod_xml(b"<config><moduleName><![CDATA[Original]]></moduleName></config>")
    value = snapshot.values[0]
    with pytest.raises(FomodXmlError) as captured:
        patch_and_validate(
            snapshot,
            (XmlTextCandidate(value.locator, value.text, "Translated", "test"),),
        )
    assert captured.value.code == "FOMOD_XML_FIDELITY_UNSUPPORTED"


def test_namespace_declaration_order_does_not_block_pipeline_publication(tmp_path):
    for variant, module_name in (("new", "Original"), ("old", "Translated")):
        with zipfile.ZipFile(tmp_path / f"{variant}.zip", "w") as bundle:
            bundle.writestr(
                "Mod/fomod/ModuleConfig.xml",
                '<config xmlns:z="urn:z" xmlns:a="urn:a" z:k="v" a:k="v">'
                f"<moduleName>{module_name}</moduleName></config>",
            )
    output = tmp_path / "translated.zip"
    pipeline = FomodPipeline()

    pipeline.run(
        str(tmp_path / "new.zip"),
        str(output),
        old_archive=str(tmp_path / "old.zip"),
        work_dir=str(tmp_path / "workspace"),
        target_lang="zh_CN",
        ai_enabled=False,
    )

    with zipfile.ZipFile(output) as bundle:
        snapshot = parse_fomod_xml(bundle.read("fomod/ModuleConfig.xml"))
    assert dict(snapshot.namespaces) == {"z": "urn:z", "a": "urn:a"}
    assert [value.text for value in snapshot.values] == ["Translated"]
    assert any(artifact.kind == "published-archive" for artifact in pipeline.last_report.artifacts)


def test_namespace_binding_changes_still_block_translation(monkeypatch):
    from transbridge.fomod import xml_fidelity

    snapshot = parse_fomod_xml(_raw_xml("utf-8"))
    original_serialize = xml_fidelity._serialize

    def corrupt_namespace(source, root):
        return original_serialize(source, root).replace(b"urn:vendor", b"urn:changed")

    monkeypatch.setattr(xml_fidelity, "_serialize", corrupt_namespace)
    value = snapshot.values[0]

    with pytest.raises(FomodXmlError) as captured:
        patch_and_validate(snapshot, (XmlTextCandidate(value.locator, value.text, "Translated", "test"),))

    assert captured.value.code == "FOMOD_XML_NAMESPACE_FIDELITY_FAILED"


def test_atomic_xml_failure_leaves_original_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "ModuleConfig.xml"
    path.write_bytes(_raw_xml("utf-8"))
    original = path.read_bytes()

    class Llm:
        def chat(self, _messages):
            return "Translated"

    def reject_replace(_source, _target):
        raise PermissionError("locked")

    monkeypatch.setattr("transbridge.fomod.xml_fidelity.os.replace", reject_replace)
    with pytest.raises(PermissionError, match="locked"):
        process_fomod_xml_file(
            path,
            old_path=None,
            llm=Llm(),
            target_locale="zh_CN",
        )
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".ModuleConfig.xml.*.tmp")) == []


def test_reference_graph_keeps_fomod_images_and_unknown_defaults_to_keep():
    files = (
        "fomod/images/banner.png",
        "assets/banner.png",
        "textures/images/banner.png",
        "screenshots/banner.png",
        "same-name/banner.png",
        "root-texture.dds",
        "A.esp",
        "meshes/a.nif",
    )
    decisions = {item.path: item for item in classify_files(files, FilterRules(), references=("assets/banner.png",))}

    assert decisions["fomod/images/banner.png"].role is ResourceRole.FOMOD_UI
    assert decisions["assets/banner.png"].action is FilterAction.KEEP
    assert decisions["textures/images/banner.png"].action is FilterAction.STRIP
    assert decisions["screenshots/banner.png"].reason == "unknown-default-keep"
    assert decisions["same-name/banner.png"].action is FilterAction.KEEP
    assert decisions["root-texture.dds"].action is FilterAction.STRIP
    assert decisions["A.esp"].role is ResourceRole.PLUGIN
    assert decisions["meshes/a.nif"].action is FilterAction.STRIP


def test_hash_skip_requires_current_digest_and_policy_match(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "large.bsa").write_bytes(b"same")
    (new / "large.bsa").write_bytes(b"same")
    digest = hashlib.sha256(b"same").hexdigest()

    hit = diff_directories(
        str(old),
        str(new),
        skip_hash_exts={".bsa"},
        hash_evidence={"large.bsa": HashReuseEvidence(digest, digest, HASH_POLICY_VERSION)},
    )
    assert hit.hash_reused == ["large.bsa"]

    stale = diff_directories(
        str(old),
        str(new),
        skip_hash_exts={".bsa"},
        hash_evidence={"large.bsa": HashReuseEvidence(digest, digest, "old-policy")},
    )
    assert stale.hash_reprocessed == ["large.bsa"]

    (new / "large.bsa").write_bytes(b"changed")
    mismatch = diff_directories(
        str(old),
        str(new),
        skip_hash_exts={".bsa"},
        hash_evidence={"large.bsa": HashReuseEvidence(digest, digest, HASH_POLICY_VERSION)},
    )
    assert mismatch.changed == ["large.bsa"]
    assert mismatch.hash_reprocessed == ["large.bsa"]


def test_invalid_xml_blocks_pipeline_publication(tmp_path):
    archive = tmp_path / "invalid.zip"
    output = tmp_path / "output.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Mod/fomod/ModuleConfig.xml", "<config><broken></config>")

    pipeline = FomodPipeline()
    with pytest.raises(RuntimeError, match="FOMOD_XML_PARSE_FAILED"):
        pipeline.run(
            str(archive),
            str(output),
            work_dir=str(tmp_path / "workspace"),
            target_lang="zh_CN",
            ai_enabled=False,
        )
    assert not output.exists()
    report = pipeline.last_report
    assert report is not None
    fidelity = next(
        (artifact for artifact in report.artifacts if artifact.artifact_id == "xml_fidelity_report"),
        None,
    )
    assert fidelity is None


def test_real_pipeline_preserves_ui_and_unknown_images_but_strips_game_data(tmp_path):
    archive = tmp_path / "resources.zip"
    output = tmp_path / "output.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "Mod/fomod/ModuleConfig.xml",
            '<config><moduleName>Example</moduleName><image path="images/banner.png"/></config>',
        )
        bundle.writestr("Mod/fomod/images/banner.png", b"ui")
        bundle.writestr("Mod/textures/banner.png", b"game-data")
        bundle.writestr("Mod/screenshots/banner.png", b"unknown")

    pipeline = FomodPipeline()
    pipeline.run(
        str(archive),
        str(output),
        work_dir=str(tmp_path / "workspace"),
        target_lang="zh_CN",
        ai_enabled=False,
    )

    with zipfile.ZipFile(output) as bundle:
        names = set(bundle.namelist())
    assert "fomod/images/banner.png" in names
    assert "screenshots/banner.png" in names
    assert "textures/banner.png" not in names
    report = pipeline.last_report
    assert report is not None
    manifest_ref = next(item for item in report.artifacts if item.artifact_id == "filter_manifest")
    manifest = json.loads(Path(manifest_ref.location).read_text(encoding="utf-8"))
    decisions = {item["path"]: item for item in manifest["decisions"]}
    assert decisions["fomod/images/banner.png"]["reason"] == "fomod-reference-or-directory"
    assert decisions["textures/banner.png"]["reason"] == "game-data-policy"
    assert decisions["screenshots/banner.png"]["reason"] == "unknown-default-keep"


def test_filter_manifest_contains_versioned_reasoned_decisions(tmp_path):
    payload = classify_files(("fomod/a.png", "textures/a.png"), FilterRules())
    serialized = json.loads(json.dumps([item.to_dict() for item in payload]))
    assert all(item["policy_version"] == "fomod-resource-v2" for item in serialized)
    assert all(item["reason"] for item in serialized)
