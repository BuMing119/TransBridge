# 文件解析

> **状态**: ✔️ Story-01~11 全部已实现
> **模块**: `src/transbridge/parser/` | `src/transbridge/converter/` | `src/transbridge/ui/`

## 概述

解析多种来源的翻译数据文件，包括 ESP/ESM/ESL 插件、EET XML、XT XML、XT SST 二进制、DSD JSON、.strings 文件，统一转换为 TranslationEntry。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | ESP/ESM/ESL 插件解析（PluginParser + SSEPluginWithContext） | ✔️ |
| Story-02 | 本地化 Strings 文件读取（松散文件 + BSA 归档） | ✔️ |
| Story-03 | 上下文提取（NPC 性别/种族/职业, INFO 说话者/情绪, DIAL 任务关联） | ✔️ |
| Story-04 | EET XML 解析 | ✔️ |
| Story-05 | XT XML 解析 | ✔️ |
| Story-06 | DSD JSON 导入 | ✔️ |
| Story-07 | Strings 文件导入 | ✔️ |
| Story-08 | FormID 转换工具 | ✔️ |
| Story-09 | XT SST 二进制解析（SSU8 + SSU9） | ✔️ |
| Story-10 | SST 迁移源集成（try_update_from_sst + apply_sst_entries + UI 入口） | ✔️ 已确认 |
| Story-11 | SST 二进制序列化器（SST_Serializer + 译文写回） | 📋 草稿 |

**详细文档**: `plans/file-parsing/stories/story-09-sst-parser.md`

## 关键文件

- `src/transbridge/parser/plugin_parser.py` — ESP/ESM 解析入口
- `src/transbridge/parser/plugin/plugin_with_context.py` — SSEPluginWithContext
- `src/transbridge/parser/plugin/plugin_string_with_context.py` — 中间结构
- `src/transbridge/parser/plugin/item.py` — NPCContext, InfoContext, DialContext
- `src/transbridge/parser/strings_file.py` — Strings 文件读写 + PluginStringsLookup
- `src/transbridge/parser/eet_parser.py` — EET XML 解析
- `src/transbridge/parser/xt_parser.py` — XT XML 解析
- `src/transbridge/parser/sst_parser.py` — XT SST 二进制解析 (NEW)
- `src/transbridge/parser/utils/fromid_trans.py` — FormID 转换

---

## Story-09: XT SST 二进制解析器

> **详细文档**: `plans/file-parsing/stories/story-09-sst-parser.md`

**对应需求**: FR1.9 XT SST 二进制解析与迁移源
**状态**: ✔️ 已实现
**创建日期**: 2026-05-08

### 验收标准

- [x] `SST_Parser.from_file()` 自动识别 SSU8/SSU9 魔数，SSU8 测试文件输出 116 条记录（第 117 条截断警告）
- [x] SSU9 文件 `hlioremi_english_chinese.sst` 正确解析 8,487 条记录，99.9% 含中文翻译
- [x] 非 SSU8/SSU9 文件给出明确 `ValueError`
- [x] UTF-16LE 解码失败时跳过该条目并记录警告，不中断整体解析
- [x] 空 SST 文件（仅 header 无条目）返回空 entries 列表
- [x] `TranslationEntry.create_from_sst_entry()` 正确映射：key=EDID:FormID，有 `translated_text` 时自动填入 translation 并设 stage=1
- [x] `scripts/sst2json.py` CLI 工具支持 `--stats`/`--pretty`/`--csv`/`-o` 四种模式
- [x] SSU8 per-EDID index 从 `field_a` 低字节提取（`(field_a & 0xFF) + 1`），116 条全部与 XT XML index 一致
- [x] SSU9 per-EDID index 从 `unk12` 低 16 位提取（`(unk12 & 0xFFFF) + 1`），8,391/8,391 与 XT XML 匹配（99.9% index 一致）

### 实现概要

实际实现超出原计划范围——新增了 SSU9 格式支持（原计划仅 SSU8），并修正了 per-EDID index 提取：

| 组件 | 说明 |
|------|------|
| `SST_Entry` | 新增 `translated_text`（SSU9 双语）、`f2`（SSU9 第二ID）、`global_seq`（SSU8 全局序号）。`index` 字段统一为 per-EDID 子索引 |
| `SST_Parser.from_file()` | 读魔数自动分发 `_parse_ssu8()` / `_parse_ssu9()` |
| `_parse_ssu8()` | 16B header + 变长记录循环。从 `field_a` 低字节提取 per-EDID index `(field_a & 0xFF) + 1`，原 `seq` 字段作为 `global_seq` |
| `_parse_ssu9()` | 模式匹配扫描记录起点 + 尾部提取中文翻译。从 `unk12` 低 16 位提取 per-EDID index `(unk12 & 0xFFFF) + 1`。str_idx=0x4000 记录过滤（39% 假阳性率） |
| `create_from_sst_entry()` | `translated_text` 非空时设为 translation，stage=1；`index` 用于生成 `edid:form_id\|index` 格式 ID |

### SSU8 vs SSU9 格式差异

| 特性 | SSU8 | SSU9 |
|------|------|------|
| 魔数 | `SSU8` | `SSU9` |
| Header | 16B 纯二进制 | 含插件名 (UTF-16LE) |
| 记录前缀 | 2B type | 4B FormID |
| 字符串长度 | 4B u32 LE | 2B u16 LE |
| 编码 | UTF-16LE | UTF-16LE |
| 多语言 | 单文件单语言 | 单文件含 English+Chinese |
| per-EDID index | `field_a` 低字节 `(field_a & 0xFF) + 1` | `unk12` 低 16 位 `(unk12 & 0xFFFF) + 1` |
| 全局序号 | `global_seq`（原 `seq` 字段） | 无 |
| EDID 后缀 | FULL/NAM1/NAM2/DATA/DESC/NAME/GOLD/SNAM/QNAM/CNAM/EDID/MODL/MODT | 同 SSU8 + DNAM/ITXT/NNAM/RNAM/SHRT |
| str_idx 过滤 | 无（所有记录直接解析） | 排除 0x4000（39% 假阳性） |
| 记录数 | 116 (测试文件) | 8,487 (HLIORemi 完整) |

### 关键文件

- `src/transbridge/parser/sst_parser.py` — SSU8 + SSU9 双格式解析器，含 `SST_Entry` dataclass（index/global_seq/f2/translated_text）
- `src/transbridge/converter/translation_entry.py` — `create_from_sst_entry()` 支持双语，使用 per-EDID index 生成 ID
- `scripts/sst2json.py` — CLI 转换工具，自动检测格式，`--pretty` 模式输出 global_seq 和 index

---

## Story-10: SST 迁移源集成

> **详细文档**: `plans/file-parsing/stories/story-10-sst-migration-source.md`
> **对应需求**: FR1.9.2 SST 迁移源集成
> **状态**: ✔️ 已实现
> **创建日期**: 2026-05-08

### 功能边界

**范围内**:
- `TranslationEntry.try_update_from_sst()` — 用 form_id + index 匹配 ESP 条目与 SST 条目，更新译文
- `TranslationEntryCollection.apply_sst_entries()` — 批量遍历 SST entries 合并到集合
- Step1 迁移源区域新增"加载 SST"按钮 + 文件对话框 + 后台解析合并

**范围外**:
- SST 格式写回/导出
- SST 上传到 ParaTranz

### 验收标准

- [ ] `try_update_from_sst()` 正确匹配 form_id + index 相同的条目，返回更新后的 TranslationEntry
- [ ] `apply_sst_entries()` 批量合并返回正确统计（匹配/更新/跳过数）
- [ ] Step1 UI SST 加载按钮可见，点击弹出 .sst 文件选择对话框
- [ ] 加载后 SST 译文合并到当前集合，统计结果反馈用户
- [ ] SSU9 的 `translated_text` 作为译文来源；SSU8 无译文仅作原文参考
- [ ] 不修改 `XT_XmlParser` 的行为（FR1.9.4）

### 实现步骤

**步骤 1: `try_update_from_sst()` 单条匹配** →
涉及文件: `src/transbridge/converter/translation_entry.py`

- 新增 `TranslationEntry` 类方法，参考 `try_update_from_xt()` 模式
- 匹配策略: **form_id + index**（SST 有 form_id，比 XT 的 edid+index 更精确）
- 从 ESP ID `{editor_id}:{form_id}|{index}~{TYPE}` 解析 form_id 和 index
- SST form_id (int) 转为 hex 字符串 `f"{sst.form_id:08X}"` 比较
- 更新条件: `stage == 0` 且 `translation` 为空 且 SST 有 `translated_text`
- 兼容无 `~TYPE` 后缀的 ID 格式

**步骤 2: `apply_sst_entries()` 批量合并** →
涉及文件: `src/transbridge/converter/translation_entry_collection.py`

- 参考现有 `apply_xt_entries()` 方法
- 遍历 SST entries，对每条调用 `try_update_from_sst()`
- 统计 matched/updated/skipped 数量
- 返回结果字典 `{matched, updated, skipped}`

**步骤 3: Step1 UI SST 加载入口** →
涉及文件: `src/transbridge/ui/workbench/step1.py`

- 在迁移源按钮区域（EET/XT/Strings 旁边）添加"加载 SST"按钮
- `QFileDialog.getOpenFileName` 选择 .sst 文件
- 后台线程: `SST_Parser.from_file()` → `apply_sst_entries()`
- 进度反馈: 解析中 → 合并中 → 完成（显示匹配/更新数）
- 按钮状态随集合加载状态动态启禁

### 架构依赖

- ADR-001: TranslationEntry 统一数据模型
- ADR-002: Collection 数据中枢
- Story-09: SST 解析器（已完成，提供 SST_Parser + SST_Entry）

---

## Story-11: SST 二进制序列化器

> **详细文档**: `plans/file-parsing/stories/story-11-sst-serializer.md`
> **对应需求**: FR1.9.5 SST 序列化写回
> **状态**: ✔️ 已实现
> **创建日期**: 2026-05-09

### 功能边界

**范围内**:
- 修改条目的 `translated_text` / `text` 字段后重新序列化
- Header 42B 原样复制（不解析、不修改）
- 每条记录重建：26B 固定头 + eng_text (UTF-16LE) + 4B chn_len + chn_text (UTF-16LE) + extra/subrecords（原样）
- 输出到新文件（默认）或原地覆盖（`overwrite=True`）
- 单条 `update_and_save()` + 批量 `update_entries()` 接口

**范围外**:
- 从零创建全新 SST 文件（无源文件模板）
- 增删记录（记录数量与源文件一致）
- 修改 header 中插件名等元数据
- 修改子记录（extra/subrecords）中的译文
- 修改 SSU8 格式 SST 文件（仅支持 SSU9）

### 验收标准

- [ ] `SST_Serializer.from_parser(sst: SST_Parser)` 从解析器实例创建序列化器，保留原始二进制数据供模板复制
- [ ] `to_bytes()` 重建完整 SST 二进制，与源文件字节级一致（未修改时）
- [ ] `update_and_save(form_id, new_text, path)` 修改指定记录的 translated_text 后写回
- [ ] `update_entries(updates: list)` 批量修改后写回
- [ ] 修改 `text`（English）后，str_len 和记录偏移同步更新
- [ ] 修改 `translated_text`（Chinese）后，chn_len 同步更新，后续数据正确偏移
- [ ] `overwrite=False` 时输出到新路径，`overwrite=True` 时覆盖原文件
- [ ] 输出文件能被 `SST_Parser.from_file()` 重新解析，条目数一致、文本正确
- [ ] 非 SSU9 文件调用时明确报错（SSU8 不支持写回）

### 实现步骤

**步骤 1: 新建 `SST_Serializer` 类骨架** →
涉及文件: `src/transbridge/parser/xt/sst_serializer.py`（新建）

- 定义 `SST_Serializer` 类，构造器接收原始二进制 `_raw_header` + `_raw_records` 列表
- `from_parser(sst, raw_data)` 工厂方法：从 SST_Parser + 原始 bytes 创建
- `to_bytes()` 方法签名

**步骤 2: 实现记录序列化** →
涉及文件: `src/transbridge/parser/xt/sst_serializer.py`

- 单条记录 → bytes：`struct.pack('<I', form_id)` + edid(8B) + unk12(4B) + f2(4B) + str_idx(2B) + str_len(2B) + pad(2B) + eng_text + 4B chn_len + chn_text + extra
- 从 `SST_Entry` 字段重建 26B 头（form_id/rec/unk12?/f2/str_idx/str_len），其中 str_len 从 eng_text 重新计算
- 注意：主记录的 unk12 和 str_idx 未存储在 SST_Entry 中，需保留原始二进制中的值

**步骤 3: 实现整体序列化与写入** →
涉及文件: `src/transbridge/parser/xt/sst_serializer.py`

- `to_bytes()`：Header + 逐条记录序列化拼接
- `save(path)`：写入文件
- `update_and_save(form_id, new_text, path)`：找到对应记录 → 修改 `translated_text` → `to_bytes()` → `save()`

**步骤 4: 实现批量更新接口** →
涉及文件: `src/transbridge/parser/xt/sst_serializer.py`

- `update_entries(updates: list[dict])`：接受 `{form_id, translated_text, text}` 列表，批量应用修改后写回

**步骤 5: 更新包导出** →
涉及文件: `src/transbridge/parser/xt/__init__.py`, `src/transbridge/parser/__init__.py`

- 导出 `SST_Serializer`

### 关键设计

- **模板重建策略**：解析时保留每条的原始二进制，修改时只替换变化的文本字段，其余原样拷贝
- **偏移自动调整**：chn_text 长度变化时，后续所有记录的偏移自动偏移（因为 to_bytes() 逐条拼接，不依赖固定偏移）
- **SSU8 不支持**：SSU8 的 trail_hash 格式未完全理解，首次仅支持 SSU9

### 架构依赖

- ADR-001: TranslationEntry 统一数据模型
- Story-09: SST 解析器（已完成，提供 SST_Parser + SST_Entry + SST_Subrecord）
