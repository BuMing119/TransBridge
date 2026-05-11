import json
from pathlib import Path
from collections import defaultdict
from typing import Any, Dict, List, Tuple


# =================== 直接改这里 ===================
TARGET_DIR = r"C:\Users\admin\Desktop\3DNPC\划分"   # A目录：需要被更新的目标json们
SOURCE_DIR = r"C:\Users\admin\Desktop\3DNPC\备份"   # B目录：翻译来源json们
OUTPUT_DIR = r"C:\Users\admin\Desktop\3DNPC\更新"   # C目录：输出更新后的目标json（仅保存更新过的）

OVERWRITE = False  # True=强制覆盖已有翻译，False=只填充空 translation
# ==================================================


def iter_json_files(folder: str) -> List[Path]:
    p = Path(folder)
    if not p.exists():
        raise FileNotFoundError(folder)
    return sorted([x for x in p.rglob("*.json") if x.is_file()])


def load_json_any(path: Path) -> List[Dict[str, Any]]:
    """
    兼容两种常见格式：
    1) [ {...}, {...} ]  (你现在用的)
    2) { "items": [ {...}, {...} ] }
    """
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    raise ValueError(f"Unsupported json structure: {path}")


def save_json_list(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def build_translation_map(
    src_items: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """
    构建 original -> translation 映射
    若同一个 original 对应多个不同 translation，会作为冲突返回（并从mapping移除）
    """
    candidates = defaultdict(set)

    for it in src_items:
        original = (it.get("original") or "").strip()
        translation = (it.get("translation") or "").strip()
        if not original or not translation:
            continue
        candidates[original].add(translation)

    mapping: Dict[str, str] = {}
    conflicts: Dict[str, List[str]] = {}

    for original, trans_set in candidates.items():
        trans_list = list(trans_set)
        if len(trans_list) == 1:
            mapping[original] = trans_list[0]
        else:
            conflicts[original] = trans_list

    # 冲突的 original 不参与合并（避免污染）
    for orig in conflicts.keys():
        mapping.pop(orig, None)

    return mapping, conflicts


def build_global_source_map(source_files: List[Path]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """
    把 B 目录所有来源 json 合并成一个全局 original -> translation 映射。

    规则：
    - 如果同一个 original 在不同来源文件里出现且 translation 不一致，则视为“全局冲突”，该 original 彻底跳过。
    - 如果一致，则保留。
    返回：
    - global_map: 可用的 original->translation
    - global_conflicts: original -> [不同translation...]
    """
    candidates = defaultdict(set)

    for fp in source_files:
        try:
            items = load_json_any(fp)
        except Exception as e:
            print(f"[WARN] 跳过无法读取的source: {fp} ({e})")
            continue

        src_map, conflicts = build_translation_map(items)

        # 单文件内部冲突已经剔除；这里只把“单文件可靠的”加入全局候选
        for orig, tr in src_map.items():
            candidates[orig].add(tr)

    global_map: Dict[str, str] = {}
    global_conflicts: Dict[str, List[str]] = {}

    for orig, tr_set in candidates.items():
        tr_list = list(tr_set)
        if len(tr_list) == 1:
            global_map[orig] = tr_list[0]
        else:
            global_conflicts[orig] = tr_list

    # 全局冲突的 original 也不参与合并
    for orig in global_conflicts.keys():
        global_map.pop(orig, None)

    return global_map, global_conflicts


def transfer_translations(
    target_items: List[Dict[str, Any]],
    src_map: Dict[str, str],
    overwrite: bool,
) -> Dict[str, int]:
    stats = {
        "filled": 0,
        "overwritten": 0,
        "skipped_no_match": 0,
        "skipped_has_translation": 0,
    }

    for it in target_items:
        original = (it.get("original") or "").strip()
        if not original or original not in src_map:
            stats["skipped_no_match"] += 1
            continue

        src_translation = src_map[original]
        tgt_translation = (it.get("translation") or "").strip()

        if overwrite:
            if tgt_translation and tgt_translation != src_translation:
                stats["overwritten"] += 1
            else:
                stats["filled"] += 1
            it["translation"] = src_translation
        else:
            if tgt_translation:
                stats["skipped_has_translation"] += 1
                continue
            it["translation"] = src_translation
            stats["filled"] += 1

    return stats


def main():
    target_files = iter_json_files(TARGET_DIR)
    source_files = iter_json_files(SOURCE_DIR)

    print(f"[INFO] A(目标)文件数: {len(target_files)}")
    print(f"[INFO] B(来源)文件数: {len(source_files)}")

    global_map, global_conflicts = build_global_source_map(source_files)
    print(f"[INFO] 全局可用词条数: {len(global_map)}")
    if global_conflicts:
        print(f"[WARN] 全局冲突 original 数: {len(global_conflicts)}（这些将全部跳过）")

    out_dir = Path(OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_saved = 0
    grand_stats = defaultdict(int)

    for tf in target_files:
        try:
            target_items = load_json_any(tf)
        except Exception as e:
            print(f"[WARN] 跳过无法读取的target: {tf} ({e})")
            continue

        stats = transfer_translations(target_items, global_map, OVERWRITE)
        changed = (stats["filled"] + stats["overwritten"]) > 0

        if not changed:
            # 只保存更新后的目标文件：没变化就不输出
            print(f"[NOOP] 无可更新: {tf.name}")
            continue

        out_path = out_dir / tf.name
        save_json_list(out_path, target_items)
        total_saved += 1

        for k, v in stats.items():
            grand_stats[k] += v

        print(f"[SAVE] {tf.name} | filled={stats['filled']}, overwritten={stats['overwritten']}, "
              f"skip_no_match={stats['skipped_no_match']}, skip_has_trans={stats['skipped_has_translation']}")

    print("\n[DONE] 批处理完成")
    print(f"[RESULT] 输出(有更新)文件数: {total_saved}")
    print(f"[RESULT] 全局统计: {dict(grand_stats)}")
    if global_conflicts:
        # 只展示前20个，避免刷屏
        print("\n[CONFLICT SAMPLE] 全局冲突示例(前20条)：")
        for i, (orig, trs) in enumerate(list(global_conflicts.items())[:20], 1):
            print(f"  {i:02d}. {orig!r} -> {trs}")


if __name__ == "__main__":
    main()
