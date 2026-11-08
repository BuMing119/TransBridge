"""Opt-in EET binary experiments; run with ``python -m scripts.eet_lab``.

Inspect, round-trip, or change one translation without overwriting existing files.
The application uses the same codec with snapshot binding and guarded publication.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.parser.eet_number_cache import EetNumberOptions


def _write_new(path: Path, data: bytes) -> None:
    # Verify the complete payload before exclusive creation, including self-output.
    EetDocument.from_bytes(data)
    with path.open("xb") as stream:
        stream.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Strictly decode and summarize an EET binary")
    inspect.add_argument("source", type=Path)
    inspect.add_argument("--record", type=int, help="Show one complete record by zero-based ordinal")
    for name in ("roundtrip", "patch"):
        command = commands.add_parser(name)
        command.add_argument("source", type=Path)
        command.add_argument("--output", required=True, type=Path)
        if name == "patch":
            command.add_argument("--record", required=True, type=int, help="Zero-based record ordinal")
            command.add_argument("--translation", required=True)
            command.add_argument("--max-number-length", type=int, default=200, help="Cache limit in UTF-16 units")
            command.add_argument(
                "--roman-numbers", choices=("exclude-i-space", "all", "off"), default="exclude-i-space"
            )
    args = parser.parse_args(argv)
    try:
        source = args.source.read_bytes()
        document = EetDocument.from_bytes(source)
        ordinal = getattr(args, "record", None)
        if ordinal is not None and not 0 <= ordinal < len(document.records):
            raise ValueError(f"Record ordinal {ordinal} is outside 0..{len(document.records) - 1}")
        if args.command == "patch":
            number_options = EetNumberOptions(
                max_utf16_units=args.max_number_length,
                roman=args.roman_numbers != "off",
                except_i_space=args.roman_numbers == "exclude-i-space",
            )
            document = document.with_translation(ordinal, args.translation, number_options=number_options)
        output = document.to_bytes()
        identical = source == output
        if args.command == "roundtrip" and not identical:
            raise ValueError("Roundtrip changed bytes; no output was written")
        result = {
            "format": "EET",
            "type_dt": document.type_dt,
            "version": document.version,
            "game": document.game,
            "records": len(document.records),
            "phrases": len(document.phrases or ()),
            "phrase_section_present": document.phrases is not None,
            "records_with_original_variables": sum(bool(record.original_variables) for record in document.records),
            "records_with_translation_variables": sum(
                bool(record.translation_variables) for record in document.records
            ),
            "bytes": len(output),
            "sha256": hashlib.sha256(output).hexdigest(),
            "byte_identical": identical,
        }
        if args.command == "inspect" and ordinal is not None:
            result["record"] = {
                key: value.hex() if isinstance(value, bytes) else value
                for key, value in asdict(document.records[ordinal]).items()
            }
            record = document.records[ordinal]
            result["number_caches"] = {
                "original": asdict(record.original_number_cache) if record.original_number_cache else None,
                "translation": asdict(record.translation_number_cache) if record.translation_number_cache else None,
            }
        if args.command == "patch":
            result["number_options"] = asdict(number_options)
        if args.command != "inspect":
            _write_new(args.output, output)
            result["output"] = str(args.output.resolve())
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, f"EET experiment failed: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
