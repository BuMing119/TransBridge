# xml_to_excel.py
# 直接在这里修改路径即可

XML_PATH = r"C:\Users\admin\Downloads\ccbgssse058-ba_steel_Partial_20260114.xml"      # ← 你的 XML 文件路径
EXCEL_PATH = r"C:\Users\admin\Downloads\ANK.xlsx"  # ← 生成的 Excel 路径


import xml.etree.ElementTree as ET
from openpyxl import Workbook


def extract_rows(root, record_tag="ESP"):
    rows = []
    all_fields = set()

    for esp in root.findall(f".//{record_tag}"):
        row = {}
        for child in list(esp):
            key = child.tag
            value = (child.text or "").strip()
            row[key] = value
            all_fields.add(key)
        rows.append(row)

    return rows, sorted(all_fields)


def write_excel(rows, headers, out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "ESP"

    # 表头
    ws.append(headers)

    # 数据行
    for r in rows:
        ws.append([r.get(h, "") for h in headers])

    # 自动列宽（简单版）
    for col, h in enumerate(headers, start=1):
        max_len = max(len(h), *(len(str(r.get(h, ""))) for r in rows))
        ws.column_dimensions[
            ws.cell(row=1, column=col).column_letter
        ].width = min(max_len + 2, 60)

    wb.save(out_path)


def main():
    tree = ET.parse(XML_PATH)
    root = tree.getroot()

    rows, headers = extract_rows(root, "ESP")

    if not rows:
        print("没有找到 <ESP> 节点")
        return

    write_excel(rows, headers, EXCEL_PATH)
    print(f"转换完成：{EXCEL_PATH}")


if __name__ == "__main__":
    main()
