import pandas as pd
import json
from datetime import datetime, UTC

# ========= 配置区 =========
INPUT_FILE = r"C:\Users\admin\Desktop\ANK_cleaned.xlsx"
OUTPUT_FILE = r"C:\Users\admin\Desktop\ANK_cleaned.json"

START_ID = 700000          # 起始id（避免和你现有词库冲突）
UID = 52466
PROJECT = 17696
# ==========================


now = datetime.now(UTC).isoformat(timespec="milliseconds")

df = pd.read_excel(INPUT_FILE)

result = []
current_id = START_ID

for _, row in df.iterrows():
    term = str(row["ORIGINAL"]).strip()
    translation = str(row["TRADUIT"]).strip()
    grup = str(row["GRUP"]).strip()
    skyrim_id = str(row["ID"]).strip()

    if not term or term == "nan":
        continue

    item = {
        "id": current_id,
        "createdAt": now,
        "updatedAt": now,
        "updatedBy": None,
        "pos": "noun",
        "uid": UID,
        "term": term,
        "translation": translation,
        "note": None,
        "project": PROJECT,
        "variants": [],
        "caseSensitive": False
    }

    result.append(item)
    current_id += 1

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print("转换完成，生成:", OUTPUT_FILE)
