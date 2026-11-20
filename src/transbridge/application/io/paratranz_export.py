# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Validate and project local ParaTranz JSON records before writing any output."""

from transbridge.application.contracts import Diagnostic

from .contracts import WriteRequest
from .paratranz_context_order import PARATRANZ_CONTEXT_ORDER_METADATA, context_orders, validate_context_order
from .paratranz_keys import export_key, split_ordered_key
from .paratranz_mapping import (
    PARATRANZ_EXTENSION_METADATA,
    ParatranzEntry,
    paratranz_record_from_entry,
)


def records_for_write(
    request: WriteRequest,
) -> tuple[list[dict[str, object]], tuple[Diagnostic, ...], tuple[int, ...]]:
    options = dict(request.options)
    preserve_extensions = options.get("preserve_extensions", True)
    if not isinstance(preserve_extensions, bool):
        return [], (Diagnostic("PARATRANZ_OPTION_INVALID", "preserve_extensions must be boolean."),), (-1,)

    include_order = options.get("context_source_order")
    if include_order is not None and not isinstance(include_order, bool):
        return [], (Diagnostic("PARATRANZ_OPTION_INVALID", "context_source_order must be boolean."),), (-1,)
    try:
        orders = options.get("context_order_values")
        if orders is not None:
            if not isinstance(orders, (tuple, list)) or len(orders) != len(request.entries):
                raise ValueError("context_order_values must match the entry count.")
            for order in orders:
                validate_context_order(order)
        else:
            orders = context_orders(request.entries)
    except (TypeError, ValueError) as exc:
        return [], (Diagnostic("PARATRANZ_CONTEXT_ORDER_INVALID", str(exc)),), (-1,)

    records: dict[int, dict[str, object]] = {}
    diagnostics: list[Diagnostic] = []
    failed: set[int] = set()
    for index, entry in enumerate(request.entries):
        try:
            key_order = _export_order(entry, orders[index], include_order)
            records[index] = paratranz_record_from_entry(
                entry, preserve_extensions=preserve_extensions, key_order=key_order
            )
            if key_order is None:
                records[index]["key"] = export_key(records[index]["key"], None)
        except (TypeError, ValueError) as exc:
            diagnostics.append(
                Diagnostic(
                    "PARATRANZ_ENTRY_WRITE_INVALID",
                    str(exc),
                    details=(
                        ("record_index", index),
                        ("key", _safe_key(entry)),
                        ("id", _safe_id(entry)),
                    ),
                )
            )
            failed.add(index)

    key_groups: dict[str, list[int]] = {}
    id_groups: dict[tuple[str, object], list[int]] = {}
    for index, record in records.items():
        transport_key, _ = split_ordered_key(record["key"])
        key_groups.setdefault(transport_key, []).append(index)
        if "id" in record:
            opaque_id = record["id"]
            id_groups.setdefault((type(opaque_id).__name__, opaque_id), []).append(index)
    for key, indices in key_groups.items():
        if len(indices) > 1:
            for index in indices:
                failed.add(index)
                diagnostics.append(
                    Diagnostic(
                        "PARATRANZ_KEY_DUPLICATE",
                        "The ParaTranz key occurs more than once in the write request.",
                        details=(
                            ("record_index", index),
                            ("key", key),
                            ("id", records[index].get("id")),
                            ("conflicting_indices", tuple(indices)),
                        ),
                    )
                )
    for opaque_key, indices in id_groups.items():
        distinct_keys = {split_ordered_key(records[index]["key"])[0] for index in indices}
        if len(distinct_keys) > 1:
            for index in indices:
                failed.add(index)
                diagnostics.append(
                    Diagnostic(
                        "PARATRANZ_ID_CONFLICT",
                        "The ParaTranz id points to different local keys in the write request.",
                        details=(
                            ("record_index", index),
                            ("key", records[index]["key"]),
                            ("id", records[index].get("id")),
                            ("external_ref", repr(opaque_key)),
                        ),
                    )
                )
    valid_records = [records[index] for index in sorted(records) if index not in failed]
    return valid_records, tuple(diagnostics), tuple(sorted(failed))


def _export_order(entry, batch_order: int, include_order: bool | None) -> int | None:
    if include_order is True:
        return batch_order
    if include_order is False:
        return entry.key_order if isinstance(entry, ParatranzEntry) else None
    if isinstance(entry, ParatranzEntry):
        return entry.display_order
    metadata = dict(getattr(entry, "metadata", ()))
    if PARATRANZ_EXTENSION_METADATA in metadata and PARATRANZ_CONTEXT_ORDER_METADATA not in metadata:
        return None  # Preserve unnumbered native JSON during a format round trip.
    return batch_order


def _safe_key(entry: object) -> str | None:
    entry_key = getattr(entry, "entry_key", None) or getattr(entry, "identity", None)
    return entry_key.local_key if hasattr(entry_key, "local_key") else None


def _safe_id(entry: object) -> str | int | None:
    for reference in getattr(entry, "external_refs", ()):
        if getattr(reference, "system", None) == "paratranz":
            return reference.opaque_id
    return None
