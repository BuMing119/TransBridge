import pandas as pd

# 读取 Excel 文件
df = pd.read_excel(r"C:\Users\admin\Downloads\ANK.xlsx")

# 去除 ORIGINAL 列完全相同的重复行，保留第一条
df_unique = df.drop_duplicates(subset=["ORIGINAL"], keep="first")

# 输出查看结果
print(df_unique)

# 保存到新的 Excel 文件
df_unique.to_excel("data_dedup.xlsx", index=False)
