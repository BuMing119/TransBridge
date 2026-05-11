#!/usr/bin/env python3
"""SST → JSON 转换工具（支持 SSU8 / SSU9）

用法:
    python scripts/sst2json.py <sst文件路径> [--output <输出路径>] [--csv] [--pretty] [--stats]

示例:
    python scripts/sst2json.py foo.sst                     # 输出 JSON 到 stdout
    python scripts/sst2json.py foo.sst -o foo.json         # 保存到文件
    python scripts/sst2json.py foo.sst --pretty -o foo.json
    python scripts/sst2json.py foo.sst --csv -o foo.csv
    python scripts/sst2json.py foo.sst --stats             # 仅显示统计信息
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.transbridge.parser.xt import SST_Parser
from src.transbridge.converter.translation_entry import TranslationEntry


def main():
    parser = argparse.ArgumentParser(
        description="将 xTranslator SST 二进制文件转换为 JSON/CSV（支持 SSU8/SSU9）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s foo.sst                     # 输出 JSON 到 stdout
  %(prog)s foo.sst -o foo.json         # 保存到文件
  %(prog)s foo.sst --pretty            # 美化输出
  %(prog)s foo.sst --csv -o foo.csv    # 导出为 CSV
  %(prog)s foo.sst --stats             # 仅显示统计信息
        """,
    )
    parser.add_argument("sst_path", help="SST 文件路径")
    parser.add_argument("-o", "--output", help="输出文件路径（默认输出到 stdout）")
    parser.add_argument("--csv", action="store_true", help="导出为 CSV 格式")
    parser.add_argument("--pretty", action="store_true", help="美化输出，包含 TranslationEntry")
    parser.add_argument("--stats", action="store_true", help="仅显示统计信息")
    args = parser.parse_args()

    try:
        sst = SST_Parser.from_file(args.sst_path)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"错误: 文件不存在 — {e}", file=sys.stderr)
        sys.exit(1)

    # Detect format
    data = Path(args.sst_path).read_bytes()
    magic = data[:4].decode("ascii", errors="replace")
    fmt = "SSU9" if magic == "SSU9" else "SSU8"

    # --stats mode
    if args.stats:
        edid_counts = Counter(e.rec for e in sst.entries)
        with_chn = sum(1 for e in sst.entries if e.translated_text)
        print(f"格式: {fmt}")
        print(f"条目数: {len(sst)}")
        if fmt == "SSU9":
            print(f"含中文: {with_chn} ({with_chn / len(sst) * 100:.1f}%)")
        print(f"EDID 类型: {len(edid_counts)}")
        print()
        print("EDID 分布:")
        for edid, count in edid_counts.most_common(30):
            print(f"  {edid:<12}: {count:>6}")
        print()
        print("前 10 条记录:")
        for i, e in enumerate(sst.entries[:10]):
            extra = f" translated={e.translated_text[:40]!r}" if e.translated_text else ""
            print(f"  [{i}] edid={e.rec} form_id=0x{e.form_id:08X} idx={e.index} glob={e.global_seq} text={e.text[:60]!r}{extra}")
        return

    # Build output
    if args.pretty:
        entries_data = []
        for e in sst.entries:
            te = TranslationEntry.create_from_sst_entry(e)
            entry = {
                "edid": e.rec,
                "form_id": f"0x{e.form_id:08X}",
                "text": e.text,
                "translated_text": e.translated_text,
                "translation_entry": te.to_dict(),
            }
            if fmt == "SSU8":
                entry["index"] = e.index
                entry["global_seq"] = e.global_seq
                entry["extra"] = f"0x{e.extra:04X}"
                entry["trail_hash"] = e.trail_hash.hex()
            elif fmt == "SSU9":
                entry["f2"] = f"0x{e.f2:08X}"
                if e.subrecords:
                    entry["subrecords"] = [
                        {
                            "form_id": f"0x{s.form_id:08X}",
                            "rec": s.rec,
                            "unk12": f"0x{s.unk12:08X}",
                            "f2": f"0x{s.f2:08X}",
                            "str_idx": s.str_idx,
                            "texts": list(s.texts),
                        }
                        for s in e.subrecords
                    ]
            entries_data.append(entry)
        output = {
            "source": str(Path(args.sst_path).resolve()),
            "format": fmt,
            "entry_count": len(sst),
            "entries": entries_data,
        }
    else:
        entries_data = []
        for e in sst.entries:
            entry = {
                "edid": e.rec,
                "form_id": f"0x{e.form_id:08X}",
                "text": e.text,
                "translated_text": e.translated_text,
            }
            if fmt == "SSU9" and e.subrecords:
                entry["subrecords"] = [
                    {
                        "form_id": f"0x{s.form_id:08X}",
                        "rec": s.rec,
                        "unk12": f"0x{s.unk12:08X}",
                        "f2": f"0x{s.f2:08X}",
                        "str_idx": s.str_idx,
                        "texts": list(s.texts),
                    }
                    for s in e.subrecords
                ]
            entries_data.append(entry)
        output = {
            "source": str(Path(args.sst_path).resolve()),
            "format": fmt,
            "entry_count": len(sst),
            "entries": entries_data,
        }

    if args.csv:
        out_path = args.output or str(PROJECT_ROOT / "sst_output.csv")
        sst.to_csv_file(out_path)
        print(f"CSV 已保存到: {out_path}")
    else:
        json_str = json.dumps(output, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(json_str, encoding="utf-8")
            print(f"JSON 已保存到: {args.output}")
        else:
            print(json_str)


if __name__ == "__main__":
    main()
