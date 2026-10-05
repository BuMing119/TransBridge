"""Export legacy proofreading evidence without modifying logs or project translations."""

import argparse
import json
from pathlib import Path

from transbridge.application.translation.proofread_log_recovery import recover_proofread_logs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if not args.log_dir.is_dir():
        parser.error("log_dir must be an existing directory")
    report = recover_proofread_logs(args.log_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite an existing user artifact.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report["counts"], ensure_ascii=False))
    print(f"救援文件：{args.output.resolve()}；尚未应用，需要核对项目身份和术语。")


if __name__ == "__main__":
    main()
