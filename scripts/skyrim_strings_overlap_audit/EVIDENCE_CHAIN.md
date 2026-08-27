# Skyrim STRINGS 文字重合证据链

[English version](EVIDENCE_CHAIN.en.md) · [脚本说明](README.md)

## 目的与结论边界

本证据链用于判断 [Nexus Mods 175184](https://www.nexusmods.com/skyrimspecialedition/mods/175184)
与 [With Light（Nexus Mods 139134）](https://www.nexusmods.com/skyrimspecialedition/mods/139134)
之间的文字重合，是否明显高于两套独立汉化通常出现的自然重合。

[ANK Terminology / FKmods（Nexus Mods 134478）](https://www.nexusmods.com/skyrimspecialedition/mods/134478)
（下称 ANK/FKmods）由其作者声明为独立完成、没有抄袭 With Light 的汉化，因此本次审计把
“ANK/FKmods 与 With Light”作为正常负对照基线。统计工具不会、也不能独立证明这项作者声明；
它只在此前提下测量正常基线与被调查组之间的差异。

统计结果支持“Nexus 175184 与 With Light 存在异常大量相同措辞、疑似未署名复用或共同底稿”的
判断，但相似度本身不能确定复用方向、授权状态或法律责任。最终认定还需要发布时间、版本历史、
授权记录和作者说明。

## 样本与统一口径

- 三套汉化均只取 240 个 `_chinese` 文件；重复内容的 `_english` 镜像不计入统计。
- 文件按插件名与 STRINGS 类型配对，词条按 UInt32 String ID 配对。
- 三组比较均使用同一脚本、同一规范化规则、同一阈值和同一 99,010 条非空对应文本。
- “完全一致”指 Unicode NFKC 规范化并移除空白后文字相同；标点和实际措辞仍被保留。
- “证据长度”指双方规范化文本均至少 6 字，用于降低短名称、固定术语自然撞译的影响。

## 证据链

### 证据一：独立汉化的正常负对照

ANK/FKmods 与 With Light 的比较结果：

- 全部有效文本：完全一致 21.66%，高度重合及以上 22.74%，平均相似度 42.10%。
- 至少 6 字：完全一致 11.20%，高度重合及以上 12.56%，平均相似度 36.66%。
- 20–79 字：完全一致 7.67%，高度重合及以上 9.49%，平均相似度 35.35%。

这组数据是本次审计的正常基线。它同时显示短文本会显著抬高总体完全一致率，因此较长文本的
分层结果更有证据价值。

### 证据二：被调查组远高于正常基线

Nexus 175184 与 With Light 的比较结果：

- 全部有效文本：完全一致 59.09%，高度重合及以上 64.09%，平均相似度 75.64%。
- 至少 6 字：完全一致 58.50%，高度重合及以上 65.01%，平均相似度 78.66%。
- 20–79 字：完全一致 60.94%，高度重合及以上 74.61%，平均相似度 86.67%。

与正常基线相比：

- 全部文本完全一致率高 37.43 个百分点，为基线的 2.73 倍。
- 全部文本高度重合及以上高 41.35 个百分点，为基线的 2.82 倍。
- 至少 6 字的完全一致率高 47.30 个百分点，为基线的 5.22 倍。
- 20–79 字的完全一致率高 53.27 个百分点，为基线的 7.95 倍。

差异不只来自人名、地名或短术语；在 20–79 字文本中，异常反而更明显。

### 证据三：第二控制组排除“ANK/FKmods 普遍与他人高度相同”

Nexus 175184 与 ANK/FKmods 的比较结果：

- 全部有效文本：完全一致 26.58%，高度重合及以上 27.56%，平均相似度 46.34%。
- 至少 6 字：完全一致 13.55%，高度重合及以上 14.82%，平均相似度 38.52%。
- 20–79 字：完全一致 5.89%，高度重合及以上 7.53%，平均相似度 35.06%。

它接近正常负对照，远低于 Nexus 175184 与 With Light 的结果。因此，被调查组的高重合不能由
“ANK/FKmods 与任何本体汉化都会得到类似高值”解释。

### 证据四：三方逐条排他分组

在三方共同覆盖的 99,010 条有效文本中：

- 三方完全相同：17,981 条；
- 仅 Nexus 175184 与 With Light 相同：40,520 条；
- 仅 Nexus 175184 与 ANK/FKmods 相同：8,335 条；
- 仅 ANK/FKmods 与 With Light 相同：3,469 条；
- 三方均不同：28,705 条。

在至少 6 字的 73,574 条文本中，仅 Nexus 175184 与 With Light 相同的有 37,010 条，而仅
ANK/FKmods 与 With Light 相同的只有 1,635 条。在 20 字及以上的 29,148 条文本中，两项分别为
15,244 条和 174 条。这组排他统计直接表明，高重合集中在 Nexus 175184 与 With Light 这一对，
并非三套汉化共同继承游戏固定短文本所致。

## 可复现步骤

先从上述三个 Nexus 页面取得各自发布的文件，再在 TransBridge 仓库根目录执行以下三组完全相同
口径的比较。示例中的 `inputs` 和 `reports` 是由复核者自行准备的本地目录，不对应发布者的机器路径：

```powershell
$env:PYTHONPATH = "src"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-134478-ank-fkmods" `
  ".\inputs\nexus-139134-with-light" `
  ".\reports\ank-fkmods-vs-with-light-baseline" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/134478" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/139134"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-175184.7z" `
  ".\inputs\nexus-139134-with-light" `
  ".\reports\nexus-175184-vs-with-light" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/175184" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/139134"

python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py `
  ".\inputs\nexus-175184.7z" `
  ".\inputs\nexus-134478-ank-fkmods" `
  ".\reports\nexus-175184-vs-ank-fkmods-control" `
  --left-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/175184" `
  --right-report-source "https://www.nexusmods.com/skyrimspecialedition/mods/134478"
```

每个输出目录都包含 `summary.md`、`summary.json`、`by_file.csv` 和 `details.csv`。其中
`details.csv` 保留逻辑文件名、String ID、双方原文和相似度，可用于逐条人工复核。

## 证据保全建议

- 保留三套输入的原始下载包或原始目录，不要在其上直接修改。
- 保留三份完整输出目录，并记录运行日期、脚本版本和输入来源。
- 对外陈述时同时提供正常负对照、被调查组和第二控制组，避免只展示单一百分比。
- 将发布时间、版本历史、页面说明、授权记录和双方作者陈述作为独立材料保存。
