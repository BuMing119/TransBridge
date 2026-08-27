# Skyrim STRINGS 中文文本重合审计

[双语审计结论](AUDIT_CONCLUSION.md) · [English evidence document](README.en.md) ·
[完整证据链](EVIDENCE_CHAIN.md) · [English evidence chain](EVIDENCE_CHAIN.en.md)

本目录提供一个一次性、可复现的审计脚本，用来比较两套《上古卷轴 5》本地化
`.strings`、`.dlstrings`、`.ilstrings` 文件的字面重合程度。脚本只读输入，按逻辑文件名和
String ID 对齐词条，不使用语义模型，也不会把“意思相近”误算为“措辞相同”。

## 调查对象与风险标记

- 调查对象：[Unofficial Chinese Translation for SAE（Nexus Mods 175184）](https://www.nexusmods.com/skyrimspecialedition/mods/175184)
- 独立基线：[重光ank（Nexus Mods 134478）](https://www.nexusmods.com/skyrimspecialedition/mods/134478)
- 对照来源：[With Light（Nexus Mods 139134）](https://www.nexusmods.com/skyrimspecialedition/mods/139134)
- 风险标记：**疑似抄袭或未署名复用，需结合授权链、发布时间和作者说明进一步核实。**

该标记来自异常高的可复现文字重合，而不是对法律责任的裁判。文本统计能够证明译文之间存在
显著继承或共同底稿关系，但仅凭相似度不能确定复用方向，也不能确认复用是否获得授权。

## 2026-08-27 审计结果

三套汉化各取 240 个 `_chinese` 文件，并排除内容重复的 `_english` 镜像文件：

- Nexus 175184 与 With Light：99,010 条有效对应文本中，完全一致 59.09%，高度重合及以上
  64.09%，平均字面相似度 75.64%；至少 6 字的文本仍有 58.50% 完全一致。
- Nexus 175184 与重光ank：完全一致 26.58%，平均字面相似度 46.34%；至少 6 字的文本
  完全一致 13.55%。
- 重光ank 与 With Light：完全一致 21.66%，平均字面相似度 42.10%；至少 6 字的文本
  完全一致 11.20%。

三方共同覆盖的 99,010 条有效文本中：

- 三方完全相同：17,981 条；
- 仅 Nexus 175184 与 With Light 相同：40,520 条；
- 仅 Nexus 175184 与重光ank 相同：8,335 条；
- 仅重光ank 与 With Light 相同：3,469 条；
- 三方均不同：28,705 条。

尤其在 20–79 字的词条中，Nexus 175184 与 With Light 的完全一致率为 60.94%，高度重合及以上
为 74.61%；重光ank 与 With Light 的对应数字仅为 7.67% 和 9.49%。因此，Nexus 175184 与
With Light 的关系明显超出“翻译同一原文”或“短术语自然撞译”通常能够解释的范围。

## 比较方法

1. 输入可以是目录或 `.7z` 压缩包。
2. 默认只选择文件名以 `_chinese` 结尾的三类 STRINGS 文件。
3. 去掉语言后缀后，按插件名和文件类型配对文件，再按 UInt32 String ID 配对词条。
4. 文本执行 Unicode NFKC 规范化并移除空白，标点和文字内容保持不变。
5. 使用字符 n-gram 多重集 Dice 系数衡量连续字面片段重合。
6. 同时报告完全一致、高度/中度/低度重合、短文本和至少 6 字的证据文本。

## 运行

在 TransBridge 仓库根目录执行：

```powershell
$env:PYTHONPATH = "src"
python scripts\skyrim_strings_overlap_audit\compare_strings_similarity.py `
  "对照汉化目录或.7z" `
  "待审计汉化目录或.7z" `
  "报告输出目录"
```

输出内容：

- `summary.md`：便于阅读的总体结果；
- `summary.json`：总体和逐文件机器可读统计；
- `by_file.csv`：按逻辑 STRINGS 文件汇总；
- `details.csv`：按 String ID 展开的双方文本和相似度。

CSV 使用 UTF-8 BOM，便于在 Excel 中直接查看中文。完整参数可运行：

```powershell
python scripts\skyrim_strings_overlap_audit\compare_strings_similarity.py --help
```

需要全英文的 CLI、控制台输出和报告时，使用：

```powershell
python scripts\skyrim_strings_overlap_audit\compare_strings_similarity_en.py --help
```

## 使用边界

- 高重合是进一步核查来源、署名和授权的证据，不等同于自动完成法律或事实认定。
- 短名称、人名、地名和固定术语容易自然相同，应优先查看至少 6 字、20 字以上及逐条明细。
- 判断继承方向还需要发布时间、版本历史、作者声明或其他外部证据。
- 本目录遵循仓库根目录的 [LICENSE](../../LICENSE)。
