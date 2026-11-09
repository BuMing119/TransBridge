# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Extract minimal SSU9 facts while the user-supplied plugin is in memory."""

from dataclasses import replace

from transbridge.converter.plugin_entry_metadata import plugin_source_order

from .sst_export import (
    PLUGIN_SST_LOCATOR,
    PLUGIN_SST_MASTERS,
    PLUGIN_SST_UNAVAILABLE,
    PluginSstLocator,
    SstExportCancelled,
    SstExportError,
    _masters,
    _positions,
    _records,
    physical_sst_locator,
)


def capture_plugin_sst_facts(plugin, lookup, content, name, entries, cancellation=None):
    try:
        masters = _masters(content, name)
        pairs = plugin.extract_string_pairs_with_context(strings_lookup=lookup)
        selected = {}
        for index, entry in enumerate(entries):
            _cancel(cancellation)
            order = plugin_source_order(entry)
            if order is None or not 0 <= order < len(pairs) or pairs[order][0].string != entry.original:
                raise SstExportError("SOURCE_LOCATOR_NOT_FOUND", "插件词条缺少已验证的物理位置。")
            field_id = id(pairs[order][1])
            if field_id in selected:
                raise SstExportError("SOURCE_LOCATOR_CONFLICT", "插件物理位置对应多个词条。")
            selected[field_id] = index
        mapped = list(entries)
        for record in _records(plugin.groups):
            _cancel(cancellation)
            if any(field.type == "VMAD" for field in record.subrecords):
                raise SstExportError("SST_EXPORT_VMAD_UNSUPPORTED", "插件含尚未接入的 VMAD 属性。")
            matched = [field for field in record.subrecords if id(field) in selected]
            if not matched:
                continue
            positions = _positions(record)
            for field in matched:
                index = selected.pop(id(field))
                locator = physical_sst_locator(record, field, *positions[id(field)])
                values = PluginSstLocator.from_values(locator.values()).values()
                mapped[index] = replace(mapped[index], metadata=(*mapped[index].metadata, (PLUGIN_SST_LOCATOR, values)))
        if selected:
            raise SstExportError("SOURCE_LOCATOR_NOT_FOUND", "插件词条缺少所属记录。")
    except SstExportError as exc:
        return (PLUGIN_SST_UNAVAILABLE, exc.code), entries
    except ValueError:
        return (PLUGIN_SST_UNAVAILABLE, "SST_EXPORT_SOURCE_UNSUPPORTED"), entries
    return (PLUGIN_SST_MASTERS, masters), tuple(mapped)


def _cancel(cancellation):
    if cancellation is not None and cancellation.is_cancelled:
        raise SstExportCancelled()
