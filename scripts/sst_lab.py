"""Reproducible, opt-in SST experiments; run with ``python -m scripts.sst_lab``.

This tool never overwrites an existing output. It does not enable product SST writing.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from transbridge.parser.xt.sst_codec import SstDocument, SstRecord, sst_hash


def demo_document(magic: bytes = b"SSU9") -> SstDocument:
    """Synthetic vocabulary for opening in xTranslator without user translation data."""
    examples = (
        (0, "WEAP", "FULL", "SST Lab Sword", "实验长剑"),
        (1, "BOOK", "DESC", "SST Lab Book\r\nSecond line", "实验书籍\r\n第二行"),
        (2, "INFO", "NAM1", "SST Lab Dialogue", "实验对白"),
    )
    records = tuple(
        SstRecord(
            list_index=list_index,
            string_id=ordinal + 1,
            form_id=0x800 + ordinal,
            record_type=record_type,
            field_type=field_type,
            index=0,
            index_max=0,
            edid_hash=sst_hash(f"SstLab{ordinal}"),
            collab_id=0,
            params=1,
            original=original,
            translation=translation,
        )
        for ordinal, (list_index, record_type, field_type, original, translation) in enumerate(examples)
    )
    return SstDocument(
        magic=magic,
        flag=0,
        masters=("TransBridgeSstLab.esp",) if magic == b"SSU9" else (),
        labels=(),
        records=records,
    )


def _write_new(path: Path, data: bytes) -> None:
    # Encode and validate before creating a file. Exclusive creation protects sources and existing outputs.
    SstDocument.from_bytes(data)
    with path.open("xb") as stream:
        stream.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Strictly decode and summarize a file")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--record", type=int, help="Show one complete record by zero-based ordinal")
    for name in ("roundtrip", "patch"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--output", required=True, type=Path)
        if name == "patch":
            command.add_argument("--record", required=True, type=int, help="Zero-based record ordinal")
            command.add_argument("--translation", required=True)
    demo = commands.add_parser("create-demo", help="Create a synthetic three-record vocabulary")
    demo.add_argument("--format", choices=("SSU8", "SSU9"), default="SSU9")
    demo.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create-demo":
            document = demo_document(args.format.encode("ascii"))
            source = document.to_bytes()
        else:
            source = args.source.read_bytes()
            document = SstDocument.from_bytes(source)
        record_index = getattr(args, "record", None)
        if record_index is not None and not 0 <= record_index < len(document.records):
            raise ValueError(f"Record ordinal {record_index} is outside 0..{len(document.records) - 1}")
        if args.command == "patch":
            records = list(document.records)
            records[args.record] = replace(records[args.record], translation=args.translation)
            document = replace(document, records=tuple(records))
        output = document.to_bytes()
        identical = source == output
        if args.command == "roundtrip" and not identical:
            raise ValueError("Roundtrip changed bytes; no output was written")
        result = {
            "format": document.magic.decode("ascii"),
            "records": len(document.records),
            "lists": dict(sorted(Counter(record.list_index for record in document.records).items())),
            "masters": len(document.masters),
            "labels": len(document.labels),
            "bytes": len(output),
            "sha256": hashlib.sha256(output).hexdigest(),
            "byte_identical": identical,
        }
        if args.command == "inspect" and record_index is not None:
            result["record"] = asdict(document.records[record_index])
        if args.command != "inspect":
            _write_new(args.output, output)
            result["output"] = str(args.output.resolve())
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, f"SST experiment failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
