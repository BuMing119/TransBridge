import json
import os

# 要处理的目录（改成你的路径）
DIR_PATH = r"C:\Users\admin\Desktop\3DNPC\划分"

for filename in os.listdir(DIR_PATH):
    # 只处理当前层的 .json 文件
    if not filename.lower().endswith(".json"):
        continue

    file_path = os.path.join(DIR_PATH, filename)

    # 确保是文件，不是目录
    if not os.path.isfile(file_path):
        continue

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        changed = 0
        for item in data:
            # translation 非空（且不是只有空格）
            if item.get("translation", "").strip():
                if item.get("stage") != 1:
                    item["stage"] = 1
                    changed += 1

        if changed > 0:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"{filename}: 修改 {changed} 条")

    except Exception as e:
        print(f"{filename}: 处理失败 -> {e}")

print("全部处理完成")
