from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.writer.eet_xml_writer import EETWriter
from transbridge.writer.xt_xml_writer import XTWriter


def collection(*originals):
    return TranslationEntryCollection([
        TranslationEntry(
            "Quest:00000800|101~QUST:CNAM",
            "Quest:00000800|101~QUST:CNAM",
            original,
            f"Translation {original}",
            1,
            "QUST:CNAM",
            requires_original_match=True,
        )
        for original in originals
    ])


def xml_writer(kind, originals, *, editor="Quest", index=101):
    root = ET.Element("DocumentElement" if kind == "eet" else "SSTXMLRessources")
    parent = root if kind == "eet" else ET.SubElement(root, "Content")
    for text in originals:
        node = ET.SubElement(parent, "ESP" if kind == "eet" else "String")
        fields = (
            {
                "EDID": editor,
                "ID": "00000800",
                "INDEX": str(index),
                "GRUP": "QUST",
                "CHAMP": "CNAM",
                "ORIGINAL": text,
                "TRADUIT": "unchanged",
                "STATUS": "0",
            }
            if kind == "eet"
            else {"EDID": editor, "REC": "QUST:CNAM", "Source": text, "Dest": "unchanged"}
        )
        for tag, value in fields.items():
            child = ET.SubElement(node, tag)
            child.text = value
            if tag == "REC":
                child.set("id", str(index - 1))
    parser = SimpleNamespace(_tree=ET.ElementTree(root))
    return (EETWriter if kind == "eet" else XTWriter)(parser)


def translations(writer, kind):
    return [node.text for node in writer.root.findall(".//TRADUIT" if kind == "eet" else ".//Dest")]


@pytest.mark.parametrize("kind", ["eet", "xt"])
def test_legacy_xml_writes_colliding_entries_by_exact_original(kind):
    writer = xml_writer(kind, ["A", "B", " A", "A\n"])
    assert writer.apply_collection(collection("A", "B", " A", "A\n")) == 4
    assert translations(writer, kind) == ["Translation A", "Translation B", "Translation  A", "Translation A\n"]


@pytest.mark.parametrize("kind", ["eet", "xt"])
@pytest.mark.parametrize("case", ["original", "key", "index", "duplicates"])
def test_legacy_xml_rejects_unproved_flagged_targets(kind, case):
    originals = ["wrong"] if case == "original" else (["A", "A"] if case == "duplicates" else ["A"])
    writer = xml_writer(
        kind, originals, editor="Other" if case == "key" else "Quest", index=999 if case == "index" else 101
    )
    assert writer.apply_collection(collection("A")) == 0
    assert translations(writer, kind) == ["unchanged"] * len(originals)
