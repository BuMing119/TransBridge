# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Map captured SST translations onto proven current-project identities."""

from transbridge.application.io.identity import SourceNamespace
from transbridge.converter.sst_entries import document_entries
from transbridge.parser.xt.sst_codec import SstDocument

from ._record_import import prepare_record_candidates


def prepare_sst_candidates(task, collection, snapshot):
    """Retain source evidence; only change translation/stage on target copies.

    A physical SST ordinal is local to its original document. Across documents
    it cannot establish identity, even when the ordinal or namespace happens to
    match. Both sides must have exactly one record with this locator and text.
    """
    document = SstDocument.from_bytes(snapshot.content)
    namespace = SourceNamespace(dict(snapshot.metadata)["source_namespace"])
    sources = document_entries(document, namespace)
    if tuple(entry.to_dict() for entry in collection) != tuple(entry.to_dict() for entry in sources):
        raise ValueError("SST_IMPORT_SNAPSHOT_MISMATCH: parsed entries differ from captured SST content")

    return prepare_record_candidates(task, sources, "SST")
