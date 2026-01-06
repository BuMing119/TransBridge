from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd

from sse_plugin_interface.plugin import SSEPlugin
from src.transbridge.parser.plugin_parser import PluginParser



def safe_getattr(obj: Any, names: list[str], default=None):
    """Try multiple attribute names (including name-mangled private ones)."""
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("esp", type=str, help="Path to .esp/.esm/.esl")
    ap.add_argument("--outdir", type=str, default="out", help="Output directory")
    ap.add_argument("--sample", type=int, default=50, help="How many entries to preview")
    ap.add_argument("--head", type=int, default=30, help="How many entries to export from the start (0=skip)")
    ap.add_argument("--random", type=int, default=200, help="How many random entries to export (0=skip)")
    ap.add_argument("--seed", type=int, default=42, help="Random seed for sampling")
    ap.add_argument("--skip-empty", action="store_true", help="Skip empty strings (same as parser)")
    args = ap.parse_args()

    esp_path = Path(args.esp).expanduser().resolve()
    outdir = Path(args.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # --- 1) Read plugin + extract raw strings (for plugin-level stats) ---
    plugin = SSEPlugin.from_file(esp_path)
    raw_strings = plugin.extract_strings(extract_localized=False)

    # plugin private fields (name-mangled) – 你的 SSEPlugin 源码里确实是这些字段名
    plugin_name = safe_getattr(plugin, ["_SSEPlugin__plugin_name"], esp_path.name)
    masters = safe_getattr(plugin, ["_SSEPlugin__masters"], [])
    groups = safe_getattr(plugin, ["_SSEPlugin__groups"], [])

    # --- 2) Use your PluginParser to get TranslationEntry list (for export) ---
    parser = PluginParser()
    items = parser.parse_plugin(esp_path, skip_empty=args.skip_empty)

    # --- 3) Build a dataframe of exported entries ---
    rows = []
    for it in items:
        # it.id is "editor_id:form_id"
        rows.append(
            {
                "entry_id": it.id,
                "key": it.key,
                "original": it.original,
                "translation": it.translation,
                "stage": it.stage,
            }
        )

    df = pd.DataFrame(rows)

    # --- 4) More detail dataframe from raw PluginString (optional, useful for visualization) ---
    # 这个 df 更“底层”，含 editor_id/form_id/index/type/string
    raw_rows = []
    for ps in raw_strings:
        raw_rows.append(
            {
                "editor_id": str(ps.editor_id) if ps.editor_id is not None else None,
                "form_id": ps.form_id,
                "index": ps.index,
                "type": ps.type,
                "string": ps.string,
            }
        )
    df_raw = pd.DataFrame(raw_rows)

    # --- 5) Compute stats ---
    total_raw = len(df_raw)
    empty_raw = int((df_raw["string"].fillna("").str.strip() == "").sum()) if total_raw else 0

    type_counts = (
        df_raw["type"]
        .fillna("UNKNOWN")
        .value_counts()
        .head(30)
        .to_dict()
    )

    meta = {
        "esp_path": str(esp_path),
        "plugin_name": str(plugin_name),
        "masters_count": len(masters) if masters is not None else None,
        "masters": [str(x) for x in masters] if masters else [],
        "groups_count": len(groups) if groups is not None else None,
        "raw_strings_total": total_raw,
        "raw_strings_empty_or_whitespace": empty_raw,
        "translation_entries_total": int(len(df)),
        "top_types": type_counts,
        "note": "masters/groups are read via name-mangled private attrs based on your SSEPlugin source",
    }

    # --- 6) Export files ---
    # 全量导出（可能很大）：你可以按需关闭
    df.to_csv(outdir / f"{esp_path.stem}.translation_entries.csv", index=False, encoding="utf-8-sig")
    df_raw.to_csv(outdir / f"{esp_path.stem}.plugin_strings.csv", index=False, encoding="utf-8-sig")
    (outdir / f"{esp_path.stem}.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 抽样导出（更适合快速可视化）
    if args.head > 0:
        df.head(args.head).to_csv(outdir / f"{esp_path.stem}.translation_entries.head{args.head}.csv", index=False, encoding="utf-8-sig")
    if args.random > 0 and len(df) > 0:
        df.sample(n=min(args.random, len(df)), random_state=args.seed).to_csv(
            outdir / f"{esp_path.stem}.translation_entries.rand{min(args.random, len(df))}.csv",
            index=False,
            encoding="utf-8-sig",
        )

    # --- 7) Console preview ---
    print("\n=== ESP META ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))

    print("\n=== PREVIEW: TranslationEntry (sample) ===")
    preview = df.sample(n=min(args.sample, len(df)), random_state=args.seed) if len(df) else df
    print(preview.to_string(index=False, max_colwidth=80))

    print("\n=== PREVIEW: Top type counts (raw) ===")
    print(pd.Series(type_counts).to_string())


if __name__ == "__main__":
    main()
