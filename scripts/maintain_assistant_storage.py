"""Preview or explicitly apply offline cleanup of unreferenced assistant attachments."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from transbridge.persistence.assistant_attachment_cleanup import AssistantAttachmentCleanup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Existing TransBridge persistence root")
    parser.add_argument(
        "--apply", action="store_true", help="Delete verified unreferenced attachments after rescanning"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Confirm all apps and other writers using this root have been stopped; required with --apply",
    )
    args = parser.parse_args(argv)
    if args.apply and not args.offline:
        parser.error("--apply requires --offline; stop all writers before applying cleanup")
    if not args.root.is_dir():
        parser.error("--root must name an existing persistence directory")
    try:
        report = AssistantAttachmentCleanup(args.root.resolve()).collect(dry_run=not args.apply, quiescent=args.offline)
    except Exception as exc:
        print(f"Attachment cleanup stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
