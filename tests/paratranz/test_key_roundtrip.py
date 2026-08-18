"""
验证脚本：update_file_translation 匹配词条时依赖整数 id 还是 key 字段？

【测试流程】
  步骤1：列出项目文件，自动取第一个文件
  步骤2：下载词条，取前 3 条，每条在不同步骤分配独立译文
  步骤3（带整数id）：每条词条赋予唯一译文（"带id-{index}"），携带整数 id 推送，
                      验证是否每条都拿到了对应的独立译文
  步骤4（不带整数id）：再次给每条赋予新的唯一译文（"仅key-{index}"），去掉整数 id，
                        验证是否每条都拿到了对应的独立译文
  步骤5：恢复原始译文

【运行方式】
  python tests/test_paratranz_key_roundtrip.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI

# ===================== 修改这里 =====================
TEST_PROJECT_ID = 17633
# ====================================================


def separator(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def fetch_all_strings(strings_api, project_id: int, file_id: int) -> dict[str, dict]:
    result = strings_api.list_strings(project_id, page=1, page_size=800, file=file_id)
    return {item["key"]: item for item in result.get("results", [])}


def make_temp_json(entries: list) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="transbridge_test_",
        encoding="utf-8", delete=False,
    ) as tmp:
        json.dump(entries, tmp, ensure_ascii=False, indent=2)
        return tmp.name


def push_update(files_api, project_id: int, file_id: int, entries: list) -> bool:
    tmp_path = make_temp_json(entries)
    try:
        files_api.update_file_translation(project_id, file_id, tmp_path, force=True)
        return True
    except RuntimeError as e:
        print(f"  [ERROR] 推送失败：{e}")
        return False
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def check_translations(strings_api, project_id: int, file_id: int,
                       expected: dict[str, str]) -> bool:
    """
    验证每条词条的译文是否与期望值一致。
    expected: {key: expected_translation}
    """
    time.sleep(1.5)
    current = fetch_all_strings(strings_api, project_id, file_id)
    all_ok = True
    for key, exp_trans in expected.items():
        item = current.get(key)
        if item is None:
            print(f"  [WARN] key {key!r} 不存在")
            all_ok = False
            continue
        actual = item.get("translation", "")
        ok = actual == exp_trans
        mark = "✓" if ok else "✗"
        print(f"  {mark} key={key!r}")
        print(f"       期望: {exp_trans!r}")
        print(f"       实际: {actual!r}")
        if not ok:
            all_ok = False
    return all_ok


def run_test() -> None:
    config = ParatranzConfig.create_or_load()
    if not config.token:
        print("[ERROR] 未找到 API Token")
        sys.exit(1)

    files_api   = ParatranzFilesAPI(token=config.token, config=config)
    strings_api = ParatranzStringsAPI(token=config.token, config=config)

    # ── 步骤 1：列出文件，自动选取第一个 ─────────────────────────────
    separator("步骤1：列出项目文件，自动选取第一个")

    file_list = files_api.list_files(TEST_PROJECT_ID)
    if not file_list:
        print("[ERROR] 项目中没有文件")
        sys.exit(1)

    test_file = file_list[0]
    file_id   = test_file["id"]
    print(f"选取文件：id={file_id}  name={test_file.get('name')!r}")
    print(f"总词条数：{test_file.get('total')}")

    # ── 步骤 2：下载词条，取前 3 条 ───────────────────────────────────
    separator("步骤2：下载词条，取前 3 条")

    current    = fetch_all_strings(strings_api, TEST_PROJECT_ID, file_id)
    test_items = list(current.values())[:3]

    if not test_items:
        print("[ERROR] 文件中没有词条")
        sys.exit(1)

    print(f"取前 {len(test_items)} 条测试：\n")
    for i, item in enumerate(test_items):
        print(f"  [{i}] id={item['id']}  key={item['key']!r}")
        print(f"       translation={item.get('translation')!r}  stage={item.get('stage')}")

    originals = [
        {
            "id":          item["id"],
            "key":         item["key"],
            "original":    item["original"],
            "translation": item.get("translation", ""),
            "stage":       item.get("stage", 0),
            "context":     item.get("context", ""),
        }
        for item in test_items
    ]

    # ── 步骤 3：携带整数 id 推送，每条赋予独立译文 ────────────────────
    separator("步骤3：携带整数 id 推送（每条独立译文）")

    # 每条词条拿到各自不同的译文，才能验证"是哪条词条被匹配更新了"
    entries_with_id = [
        {
            "id":          item["id"],
            "key":         item["key"],
            "original":    item["original"],
            "translation": f"【带id-{i}-验证用】",
            "stage":       1,
            "context":     item.get("context", ""),
        }
        for i, item in enumerate(test_items)
    ]
    expected_with_id = {
        item["key"]: f"【带id-{i}-验证用】"
        for i, item in enumerate(test_items)
    }

    ok3 = push_update(files_api, TEST_PROJECT_ID, file_id, entries_with_id)
    print(f"  推送结果：{'成功' if ok3 else '失败'}")

    if ok3:
        print("  验证各条词条译文：")
        matched3 = check_translations(strings_api, TEST_PROJECT_ID, file_id, expected_with_id)
        print(f"  → 带 id 更新：{'全部匹配 ✓' if matched3 else '部分或全部未匹配 ✗'}")
    else:
        matched3 = False

    # ── 步骤 4：仅用 key 推送，每条赋予新的独立译文 ───────────────────
    separator("步骤4：仅用 key 推送（不含整数 id，每条独立译文）")

    entries_without_id = [
        {
            "key":         item["key"],
            "original":    item["original"],
            "translation": f"【仅key-{i}-验证用】",
            "stage":       1,
            "context":     item.get("context", ""),
        }
        for i, item in enumerate(test_items)
    ]
    expected_without_id = {
        item["key"]: f"【仅key-{i}-验证用】"
        for i, item in enumerate(test_items)
    }

    ok4 = push_update(files_api, TEST_PROJECT_ID, file_id, entries_without_id)
    print(f"  推送结果：{'成功' if ok4 else '失败'}")

    if ok4:
        print("  验证各条词条译文：")
        matched4 = check_translations(strings_api, TEST_PROJECT_ID, file_id, expected_without_id)
        print(f"  → 不带 id 更新：{'全部匹配 ✓' if matched4 else '部分或全部未匹配 ✗'}")
    else:
        matched4 = False

    # ── 步骤 5：恢复原始状态 ─────────────────────────────────────────
    separator("步骤5：恢复原始译文")
    restored = push_update(files_api, TEST_PROJECT_ID, file_id, originals)
    print(f"  恢复结果：{'成功' if restored else '失败'}")

    # ── 最终结论 ───────────────────────────────────────────────────────
    separator("最终结论")
    print(f"  步骤3（带整数 id，每条独立译文）：{'成功 ✓' if matched3 else '失败 ✗'}")
    print(f"  步骤4（仅用 key，每条独立译文）：{'成功 ✓' if matched4 else '失败 ✗'}")
    print()

    if matched3 and matched4:
        print("结论：ParaTranz 同时支持通过整数 id 和 key 匹配词条。")
        print("建议：下载回来后用 key 匹配即可，无需保存 ParaTranz 整数 id。")
    elif matched3 and not matched4:
        print("结论：ParaTranz 仅通过整数 id 匹配词条，key 无效。")
        print("建议：上传后必须保存 ParaTranz 整数 id，下载时用整数 id 对应本地 key。")
    elif not matched3 and matched4:
        print("结论：ParaTranz 通过 key 匹配词条（整数 id 被忽略）。")
        print("建议：只需保证 key 一致即可，无需关注整数 id。")
    else:
        print("结论：两种方式均失败，请检查文件格式或权限。")


if __name__ == "__main__":
    run_test()
