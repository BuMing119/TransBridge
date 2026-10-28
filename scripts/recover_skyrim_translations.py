"""Recover an independent local project from a Para export and ANK Strings."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.skyrim_translation_recovery.inputs import digest, project_plugins, read_export  # noqa: E402
from scripts.skyrim_translation_recovery.matching import TranslationIndex  # noqa: E402
from scripts.skyrim_translation_recovery.project import RecoverySources, publish  # noqa: E402
from transbridge.application.contracts import RequestContext  # noqa: E402


def run(args):
    if args.install_root is not None and not args.apply:
        raise ValueError("--install-root requires --apply")
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    plugins = project_plugins(args.project)
    export = args.paratranz.read_bytes()
    rows = read_export(export, {p.stem.casefold() for p in plugins})
    if not args.ank.is_dir():
        raise ValueError("--ank must name the translated Strings directory")
    if not args.language or any(not c.isalnum() for c in args.language):
        raise ValueError("Language must be a filename-safe language name")
    output.mkdir(parents=True, exist_ok=False)
    (output / "paratranz-original.zip").write_bytes(export)
    (output / "source-project.json").write_bytes(args.project.read_bytes())
    run_id = "skyrim-recovery-" + uuid4().hex
    context = RequestContext("skyrim-recovery", run_id=run_id)
    recovery = RecoverySources(output, args.ank.resolve(), args.language, TranslationIndex(rows), run_id)
    report = {
        "schema": 1,
        "run_id": run_id,
        "status": "preparing",
        "export_sha256": digest(export),
        "para_rows": len(rows),
        "para_nonempty": sum(bool(r.translation) for r in rows),
        "sources": recovery.sources,
        "counts": {},
    }
    try:
        with (output / "decisions.jsonl").open("x", encoding="utf-8") as trace:
            for plugin in plugins:
                recovery.prepare(plugin, trace, context)
        unused = [r for r in rows if r.reference not in recovery.used]
        (output / "unmatched-paratranz.json").write_text(
            json.dumps(
                [{"reference": r.reference, "plugin": r.plugin, "entry": r.payload} for r in unused],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        report.update({
            "status": "preview",
            "counts": dict(recovery.stats),
            "entries": sum(recovery.stats.values()),
            "unmatched_para_rows": len(unused),
            "unmatched_para_nonempty": sum(bool(r.translation) for r in unused),
        })
        write_report(output, report)
        if args.apply:
            report.update(publish(output, recovery, args.name, context, args.install_root))
            report["status"] = "installed" if args.install_root else "packaged"
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}", counts=dict(recovery.stats))
        raise
    finally:
        write_report(output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "sources"}, ensure_ascii=False, indent=2))
    return report


def write_report(output, report):
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True, help="Existing project record JSON (read-only)")
    parser.add_argument("--paratranz", type=Path, required=True, help="ParaTranz export ZIP")
    parser.add_argument("--ank", type=Path, required=True, help="ANK translated Strings directory")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; existing directories refused")
    parser.add_argument("--language", default="english")
    parser.add_argument("--name", default="Skyrim recovered")
    parser.add_argument("--apply", action="store_true", help="Create an importable .transbridge recovery project")
    parser.add_argument(
        "--install-root", type=Path, help="Additionally register the new project in this data directory"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    run(args)


if __name__ == "__main__":
    main()
