# ESP-ESM Translator 二进制 EET 实验

日期：2026-11-08。状态：格式读写与数字缓存实验已验证；正式来源、工程重开、GUI/助手和原格式发布按 [ADR-053](../adr/053-eet-binary-io-integration.md) 与[集成计划](../../plans/eet-integration/plan.md)完成本地全流程实现与相关验证，能力保持实验性；上游程序尚未人工验收，正式 MCP 无文件读写工具。

## 格式命名与兼容入口

本实验只研究 ESP-ESM Translator EET（`.eet`）。它与 ESP-ESM Translator XML（`.xml`）、xTranslator XML（`.xml`）、xTranslator SST（`.sst`）是四种独立格式，不把 EET 解释成具有 XML 与二进制两个变体的格式。
历史 FormatId 分别为 `binary.eet`、`xml.eet`、`xml.xt`、`sst.ssu8` / `sst.ssu9`；ID 保留兼容，SSU8/SSU9 是 SST 版本。内容识别可处理扩展名误标，但不会合并格式身份。历史助手 `parse_eet` 和 `write_back(target="eet")` 兼容 ESP-ESM Translator 的两种格式；实际解析与写回 adapter 由内容及可信来源快照确定，EET 不经 XML builder。

## 数字缓存补全结果

- 已复核 TextWitoutNumber.Create、正则选项、调用方长度/字段条件及 GUI 自动计算路径。
- 已实现独立缓存模型、生成和译文修改时的同步更新，读取与写出严格校验边界。
- 四份语料的 12,654 个已有非空缓存重新生成后全部逐字节一致；48 条独立 .NET 参考结果一致。

## 已完成范围

1. 核查旧解析器与真实样本，确认类型、版本、边界、字段、哈希、数字缓存和图标。
2. 以已安装 EET4 4.35.0.0 的只读 .NET IL 对照样本，不启动程序，不使用 computer-use。
3. 新增唯一格式实现 `src/transbridge/parser/eet_binary_codec.py`，旧 `eet_binary_parser.py` 委托该实现，
   保留查询 facade 并提供完整 document。实验 CLI 位于 `scripts/eet_lab.py`。
4. 支持格式版本 3 的 Classique(1)/BDD(2)：完整读取、逐字节往返、指定物理序号的译文修改，
   包括数字缓存的新增、重建和清空。其他格式版本/类型明确拒绝。

## 证据来源

- 仓库 `tests/trans_exe/eet/BDD_WL.eet`：28,380,673 字节，TypeDT=2、Version=3、LINE 声明 92,157 条。
- 安装目录 `C:/MyProgram/MO2/tools/ESP-ESM Translator` 含 EET4.exe、MurmurHash.dll、
  其他 TypeDT=2 数据库及 TypeDT=1 会话文件；它们实际全部是 Version=3。
- EET4.exe FileVersion=4.35.0.0，SHA-256：
  `bc6c9727493db919537542ce6a484a4c0837dadd31a0e6db4cf4faceaccefaa8`。
  使用 ReflectionOnlyLoadFrom 检查 EETheader、ModLine.Write/构造函数、LoadEET、Fill_Dicos、
  TextWitoutNumber 和 WriteDico 的方法体；没有执行目标程序集的方法。
- [作者发布页](https://www.nexusmods.com/skyrimspecialedition/mods/921)的历史记录说明 EET 多次改变格式。
  本次没有找到作者公开源码或正式二进制规范，不能把本实验描述为“看了公开源码”，也不据此断言软件闭源。
- 用户原样本及安装目录保持不变，实验产物写入忽略的 `out/` 目录。

## 确认的当前 wire 布局

- 文件头：`EET_ + TypeDT:int32 LE + Version:uint32 LE`。没有 section_count 字段。
- `GAME` 后为 uint16 UTF-8 **字节长度**和游戏名，数据库常为空，会话样本为 `Skyrim SE`。
- `LINE` 后为 uint32 条目数。每条是 uint32 payload 字节长度及 payload，长度**不包含自身四字节**。
- payload 首先是七个 uint32 UTF-8 字符串：GRUP、ID、EDID、CHAMP、ORIGINAL、TRADUIT、PERSO。
  GRUP 前的数值 4 是字符串长度，不是字段计数。
- 随后：INDEX:int32、STATUS:int16、IDSTEXTE:int32、COMMENTAIRE:string。
- Version=3 随后保存：原文哈希32位、去标点原文:string、去标点原文哈希32位、
  原文数字缓存:blob、译文数字缓存:blob、可选 ICON:int32。
  常见 `FFFFFFFF` 是 ICON=-1，不是分隔符；会话样本有 33 种图标值。
- 缓存 blob：uint32 payload 长度，0 为无缓存；非空为模板:string、uint32 变量数及若干 string。
  例如模板中的 `%VAR00%` 对应缓存第一个数字。原文、译文缓存分别依赖各自文本。
- 可选 `PHRA` 后为 uint32 payload 长度；内部 uint32 数量及成对 uint16 UTF-8 字符串。
  会话样本无 PHRA，数据库有 PHRA；缺失与显式空表保持区分。

所有读取限定在所属 payload 内，验证条目数、长度、UTF-8、缓存内部边界和 EOF。
不搜索特征跳过记录，不替换非法 UTF-8，不在截断后返回部分成功。

## 哈希和变量缓存

EET 的原文哈希使用 `.Trim().ToLower().GetHashCode()`，另一项使用去标点文本的
`.ToLower().GetHashCode()`。这不是 xTranslator 的 FNV，也不是安装目录 DLL 名称所暗示的 MurmurHash。
[微软说明](https://learn.microsoft.com/en-us/dotnet/api/system.string.gethashcode)明确指出该字符串哈希
可能随运行时、位数、平台或进程变化，不能承诺不同环境下计算出相同持久数值。

Version=3 的加载器读取后丢弃两项持久哈希并重算。实验仍保留磁盘原值，只修改译文，
不伪造“通用 EET hash”算法，也不从零猜测去标点规则。

加载 v3 时调用 Fill_Dicos(false)，不会自动重算数字缓存；TRADUIT setter 本身也不更新缓存。
但 GUI 的 Analyse/Notify → ApplyAutomatics → Task.Run 路径会调用 CalculTraduitNumerique；
不能据 setter 推断 GUI 不更新。离线修改必须同步重建，不能依赖加载器修复过期缓存。

`EetRecord.original_number_cache` / `translation_number_cache` 提供模板和有序变量；原始 blob 字段保留。
`parse_number_cache`、`EetNumberCache.to_payload` 支持独立读写，`build_number_cache` 支持从文本生成。
`with_translation` 仅重建有变化的译文侧缓存，保留原文侧缓存、哈希、状态和标识；相同译文返回原对象。
文本不再含匹配项、超长、为空或属于脚本文本时，译文缓存写成零长度 blob。

### 生成规则与选项

依据 EET4 4.35.0.0 的方法 token：Create `0x06003046`、CreateWithoutRoman `0x06003047`、
Set_MyREGEXNumber `0x0600231A/1B`、CalculOriginal/TraduitNumerique `0x06002605/606`。
默认设置 `TB_NUMmaxchar=200`、`CB_NUM_Roman=True`、`CB_NUM_Roman_Except_I=True`。

- 长度按 UTF-16 单元计，默认仅处理 1..200；仅 `GRUP=SCPT` 且 `CHAMP=SCTX` 的组合排除，区分大小写。
- 阿拉伯数字正则为 `[\+\-]?\d+(\.?\d+)?`，保留正负号、小数和前导零的原字符串，不转换数值。
- 默认同时识别大写罗马数字；Except_I 仅额外排除紧跟 ASCII 空格的 `I `。
  `I am` 和 `I'` 由共同规则排除；末尾 `I`、`I,`、`I\t` 仍可能被匹配。
- 取前 100 个匹配项，按位置逆序替换成 `%VAR00%`..`%VAR99%`，变量按文本顺序保存；重复数字不合并。
- 保留原正则的零长度匹配：例如 `CIVIL` 会生成模板 `%VAR00%CIVIL` 和变量 `[""]`。
  不纠正原程序行为，不对已有 `%VARxx%` 字面文本额外转义。
- 在 UTF-16 单元上模拟 .NET 搜索和空匹配后推进；BMP 的 `\d`/词边界字符表固定为本机 .NET Framework
  4.8 的 BCL 结果，不受 Python Unicode 版本影响。补充平面的数学数字不按 BMP 数字匹配。

这些选项没有保存在 EET 文件中。`EetNumberOptions` 可显式覆盖默认长度与 Roman/Except_I；
读取/无修改往返不尝试推断选项，也不重新生成旧缓存。当前不会修改原文、重算原文哈希或去标点字段。

参考生成器 `scripts/eet_number_reference.ps1` 仅运行 .NET BCL Regex，不加载或执行 EET 程序集。
使用 Windows PowerShell 5.1 (`powershell.exe`) 生成黄金数据及 BMP 字符表，例如：

```powershell
powershell.exe -NoProfile -File scripts/eet_number_reference.ps1 -OutputPath new-reference.json -TablePath new-ranges.json
```

它禁止覆盖已有输出。运行时证据和 48 条用例存于 `tests/parser/fixtures/eet_number_cache_reference.json`；
生产字符表位于 `src/transbridge/parser/eet_number_unicode.py`，为生成的纯数据文件。

## 旧解析器修正

旧实现把 TypeDT=2 当作版本、Version=3 当作段数；将 INDEX 当 STATUS、STATUS 当 INDEX，
IDSTEXTE 读为16位，COMMENTAIRE 起点错两字节，忽略哈希、缓存和 ICON。
它还会在仅剩首条的 149 字节截断文件上返回成功，尽管头部声称有92,157条。

现在 facade 通过严格 codec 读取，`document` 保存全部字段；查询条目的 index/status/idstexte/icon
正确映射，game_data 保存真实游戏名字符串，phrases 保持原始 PHRA payload 字节。
因旧行为本身有误，不保留旧字段错位结果。正式接入使用 `binary.eet` adapter 和完整来源快照，
以来源命名空间、`eet-v1:{ordinal}`、codec 版本和记录签名校验原格式写回。GUI/助手共享应用层，
ESP-ESM Translator XML 与 ESP-ESM Translator EET 按内容分别路由；缺少新版身份的旧来源不得猜测写回。实施与验证状态以集成计划为准。

## 格式实验阶段验证

本节为正式 application 集成之前的格式实验记录；其中182/468项测试不代表本轮工程、GUI、助手和发布链路已经验收。新集成结果由[EET 集成计划](../../plans/eet-integration/plan.md)记录。

- `BDD_WL.eet`：92,157 条、43 对短语，完整字节往返一致。
- `BDD_Reconquista.eet`：92,556 条、153 对短语，一致。
- `BDD_Reconquista ANK.eet`：92,950 条、125 对短语，一致。
- `3DNPC_B675CB1D_BA3D60BA.eet`：53,847 条、无 PHRA，一致。
- 合计 **331,510 条**。外部安装目录只读，样本未复制进版本控制。
- 非空原文与译文缓存按文件合计：WL 4,038、Reconquista 3,839、ANK 3,867、会话 910；
  **12,654/12,654** 使用默认选项重新生成后与原 payload 逐字节一致。

聚焦验证：

```powershell
uv run pytest tests/parser/test_eet_binary_codec.py tests/parser/test_eet_lab_cli.py tests/parser/test_eet_parser.py -q
uv run pytest tests/parser/test_eet_number_cache.py -q
```

分别 **89 passed**、**93 passed**。覆盖独立 struct wire、所有截断位置（完整可选尾段省略除外）、损坏长度/计数、
非法 UTF-8、非空注释、32位 ID、ICON、有/无 PHRA、缓存、显式空译文、补充 Unicode、长文本和防覆盖。
数字缓存测试包括 .NET 黄金数据、UTF-16 长度边界、脚本文本过滤、空匹配、100 项截断和真实缓存重建。
测试使用 `--offline --no-sync` 和工作区专属 uv/pytest/TEMP 目录，收尾删除。

扩大回归命令：`uv run pytest tests/parser tests/writer/test_eet_xml_writer.py tests/contracts/io/test_catalog.py tests/capability/test_capability_matrix.py -q`，
**468 passed**（包含上述182项；3 条现有 SWIG DeprecationWarning）。
全库 `uv run ruff check src tests scripts/eet_lab.py`、
`uv run ruff format --check src tests scripts/eet_lab.py` 均通过；最终格式检查覆盖2413文件。
数字修改产物落盘前完整解析回读，确认仅记录19的译文及译文缓存变化，原样本字节未变；
之前的记录0修改副本和逐字节对照副本保留。
未运行全仓 pytest、真实程序 UI 或安装包测试；未提交或推送。

## 新增 3DNPC 下载语料验证

另对用户提供的 3DNPC 下载文件验证：53,847 条物理记录，未修改逐字节往返一致；
868 个原文缓存与42个译文缓存合计 **910/910**，按默认选项重新生成后全部一致。
物理记录29430的译文由“拿着，50金币。”改为“拿着，75金币。”，同步更新译文数字缓存，
并确认其他记录和字段保持。输出位于 `out/eet-3dnpc-lab-20261108/`，原始下载文件不改动。
该证据是格式及缓存验证，不代表 EET 上游 GUI 已打开、另存或应用成功，也不代替本轮正式链路 QA。

## 正式工作流合同

- 作为已有译文或独立来源导入；内容识别为 ESP-ESM Translator EET（`binary.eet`）后保留全部物理记录，不混入独立的 ESP-ESM Translator XML parser。
- 工程保存完整快照与记录绑定，重开及原路径不可用时的另存使用可信保存内容。
- GUI 单个/批量和助手写回沿用原格式、统一预检、暂存、全文件回读和发布；空译文可以明确写回。
- 原格式写回只改有效译文及其数字缓存；不把编辑阶段自动写成 EET STATUS，不重算原文哈希。
- 旧身份缺少绑定时重新导入；迁移到插件要求 FormID、记录/字段和原文唯一匹配。双方已知 EditorID 时必须 casefold 相同，只有未知时才沿用前述匹配；重开后从 metadata 保留 EditorID，歧义不覆盖。
- 当前只支持 v3 Classique/BDD；无插件→EET、无新 MCP 读写工具、不自动启动外部程序。

用户入口沿用“选择已有译文”或 EET 来源导入，文件选择后按内容识别；编辑并保存工程后可通过原格式写回选择新的 `.eet` 输出位置。助手 `write_back(target="eet")` 同样按来源格式路由，不能把二进制来源传给 XML builder。出现 `EET_REIMPORT_REQUIRED` 时重新导入原 EET，再迁移已有译文；不要修改旧 key 绕过校验。具体入口状态及本轮回归以集成计划为准。

## 正式集成验证

真实3DNPC的53,847条记录通过 `EetAdapter → HydratedWritePreflight → staging/publish → 完整回读`。
物理记录29430的译文由“拿着，50金币。”改为“拿着，75金币。”，译文数字缓存同步更新，
原来源字节未修改；其余记录和原生字段保持。该次执行耗时11.209秒，仅为单次观测，不是性能基准。
正式输出：`out/eet-integration-20261108/3DNPC_B675CB1D.eet`；
SHA-256：`e74527a570a0b25f6f086519fe8111f8a7af082affa11158b95c097ca7a7bb17`。

新增离线 ParaTranz JSON 导出→修改译文→JSON 导回→EET 写出回归通过，重复物理记录不丢失。
工程保存重开、迁移身份、GUI/助手及统一发布均为已接入的实验性流程，不限于实验 CLI。
从 `.xml` 扩展名文件识别到二进制 EET 后，按输出目录另存会派生 `.eet`，不继承错误扩展名。
最终联合测试计数、平台跳过与已用任务前源码复现的既有失败见[实施计划](../../plans/eet-integration/plan.md)。
上游 ESP-ESM Translator 的 GUI 打开、另存与应用插件仍未人工验收。

## 供人工验收的文件与使用

目录：`out/eet-lab-20261108/`。

- `BDD_WL_roundtrip.eet`：与原数据库逐字节相同，用于对照。
- `BDD_WL_patched.eet`：仅物理记录0的译文改为 `EET实验：修改后的按钮`。
  定位为 `ACTI / FULL / 030000CE / RP_DweButton01Frost`，原文 `Button`；其余记录、图标、状态、
  哈希、缓存和短语表保持不变。建议优先用这份验证修改是否显示。
- `BDD_WL_numeric_patched.eet`：仅物理记录19译文由 `芦荟叶（2）` 改为 `芦荟叶（3）`，
  译文缓存模板为 `芦荟叶（%VAR00%）`，变量由 `["2"]` 改为 `["3"]`。
  定位 `MISC / FULL / 0300016C / RP_BYOHHouseIngredientAloeVera02`，原文 `Aloe Vera Leaves (2)`；
  原文缓存和其他字段、其他记录保持不变。SHA-256：
  `9665399797ae491150d0008b5e35412800f68b6ad97366837ba88661cba0c483`。

请在 ESP-ESM Translator 的数据库加载/切换入口选择实验副本，不覆盖原 BDD_WL。
检查条目数及上述目标译文后，可另存新文件用于进一步反向对比；当前未确认实际程序打开或另存成功。

```powershell
uv run python -m scripts.eet_lab inspect tests/trans_exe/eet/BDD_WL.eet
uv run python -m scripts.eet_lab inspect out/eet-lab-20261108/BDD_WL_patched.eet --record 0
uv run python -m scripts.eet_lab roundtrip input.eet --output copy.eet
uv run python -m scripts.eet_lab patch input.eet --record 0 --translation "新的译文" --output changed.eet
uv run python -m scripts.eet_lab patch input.eet --record 0 --translation "Level III: 50" --roman-numbers off --max-number-length 200 --output changed-numeric.eet
```

默认 inspect 只显示计数/摘要，`--record` 才显示正文、原始缓存十六进制和解析后的模板/变量。
patch 的 `--roman-numbers` 支持 `exclude-i-space`（默认）、`all`、`off`；`--max-number-length` 默认200。
输出独占创建，禁止覆盖输入及已有目标。
不支持旧格式升级、插件→EET 或从零创建完整词典；正式软件原格式写回由共享 adapter 接入，
不通过实验 CLI 绕过来源、身份、预检与发布校验。
