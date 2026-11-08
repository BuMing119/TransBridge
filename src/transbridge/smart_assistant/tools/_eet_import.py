"""Validate captured EET evidence before mapping translations to project records."""

from transbridge.application.io.identity import SourceNamespace
from transbridge.converter.eet_entries import document_entries
from transbridge.parser.eet_binary_codec import EetDocument

from ._record_import import prepare_record_candidates


def prepare_eet_candidates(task, collection, snapshot):
    document = EetDocument.from_bytes(snapshot.content)
    namespace = SourceNamespace(dict(snapshot.metadata)["source_namespace"])
    sources = document_entries(document, namespace)
    if tuple(entry.to_dict() for entry in collection) != tuple(entry.to_dict() for entry in sources):
        raise ValueError("EET_IMPORT_SNAPSHOT_MISMATCH: parsed entries differ from captured EET content")
    return prepare_record_candidates(task, sources, "EET")
