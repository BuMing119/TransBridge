"""Synthetic plugin strings shared by dialogue index and UI regression tests."""

from types import SimpleNamespace

from transbridge.converter.translation_entry import TranslationEntry


def dialogue_entry(kind="INFO", form="00000020", *, quest="00000001", parent="00000010", order=1, index=1):
    return TranslationEntry.create_from_plugin_entry(
        SimpleNamespace(
            editor_id="Topic" if kind in {"DIAL", "INFO"} else "Quest",
            form_id=f"{form}|fixture.esp",
            type=f"{kind} {'NAM1' if kind == 'INFO' else 'FULL'}",
            string=f"{kind} text {index}",
            index=index,
            context=SimpleNamespace(
                quest=quest,
                dialogue_topic=f"{parent}|fixture.esp" if kind == "INFO" and parent else None,
            ),
        ),
        source_order=order,
    )


def dialogue_entries():
    return (
        dialogue_entry("QUST", "00000001", order=0),
        dialogue_entry("DIAL", "00000010", order=1),
        dialogue_entry(order=2),
        dialogue_entry(order=3, index=2),
        dialogue_entry("DIAL", "00000011", order=4),
        dialogue_entry(form="00000021", parent="00000011", order=5),
    )
