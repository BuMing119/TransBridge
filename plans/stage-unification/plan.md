# Stage 状态系统统一

**对应需求**: FR2.5
**技术模块**: converter, paratranz, writer, ui, ai_translator
**状态**: ✔️ 已实现
**创建日期**: 2026-05-07

## 概述

全项目统一 TranslationEntry.stage 语义为 ParaTranz 7 级定义（0=未翻译, 1=已翻译, 2=有疑问, 3=已检查, 5=已审核, 9=已锁定, -1=已隐藏）。修正当前各模块中不一致的 stage 读写逻辑。

## 功能边界

### 范围内
- TranslationEntry.stage 全项目语义统一
- 数据源（ESP/XT/EET/Strings/DSD JSON/ParaTranz 下载）→ stage 映射修正
- Step2 UI 7 级 stage 标签 + 行色条
- EET 写回 stage≥1 修正
- AI 翻译 locked(9)/hidden(-1) 排除

### 范围外
- ParaTranz API 调用中的 stage 参数（已正确）
- EET 格式扩展（EET 只有二元状态，不扩展）
- Stage 修改后向 ParaTranz 同步

## Story 清单

| Story | 标题 | 归属 Epic | 状态 |
|-------|------|---------|------|
| Story-01 | 数据层 Stage 映射修正 | core-data-model | ✔️ |
| Story-02 | 写回 Stage 修正 | file-writing | ✔️ |
| Story-03 | UI Stage 7 级可视化 | ui-workbench | ✔️ |

---

## Story-01: 数据层 Stage 映射修正

**对应需求**: FR2.5.1, FR2.5.2
**归属 Epic**: core-data-model（追加）, paratranz-integration（无需改）
**状态**: ✔️
**详细文档**: `plans/stage-unification/stories/story-01-data-layer-stage.md`
**验收标准**:
- [ ] converter 中各数据源的 stage 赋值语义正确
- [ ] downloader 透传 ParaTranz stage 值正确
- [ ] 新增 Stage 常量定义模块供全项目引用

**实现步骤**:
1. 在 `converter/translation_entry.py` 中新增 `STAGE_*` 常量定义（STAGE_UNTRANSLATED=0, STAGE_TRANSLATED=1, STAGE_QUESTIONABLE=2, STAGE_CHECKED=3, STAGE_REVIEWED=5, STAGE_LOCKED=9, STAGE_HIDDEN=-1）和 `STAGE_LABELS` 映射 → 涉及文件: `src/transbridge/converter/translation_entry.py`
2. 修正 `translation_entry_collection.py` 中 XT/EET/Strings 导入的 stage 赋值注释（`stage=1` 语义从"机翻→已翻译"，值不变）→ 涉及文件: `src/transbridge/converter/translation_entry_collection.py`
3. 修正 `translation_entry.py` 中 `from_dsd_dict` 的 stage 赋值（`stage=1 if string else 0` 语义正确，添加注释说明）→ 涉及文件: `src/transbridge/converter/translation_entry.py`
4. 确认 `downloader.py:120` 直接透传 ParaTranz stage 值到 TranslationEntry.stage 为预期行为，添加注释 → 涉及文件: `src/transbridge/paratranz/workflow/downloader.py`

---

## Story-02: 写回 Stage 修正

**对应需求**: FR2.5.6
**归属 Epic**: file-writing（追加）
**状态**: ✔️
**详细文档**: `plans/stage-unification/stories/story-02-writer-stage-fix.md`
**验收标准**:
- [ ] EET 写回正确处理 stage>=1 的条目（stage=2 手动编辑不丢失）
- [ ] 已锁定（stage=9）强制写回译文
- [ ] 已隐藏（stage=-1）强制写回原文

**实现步骤**:
1. 修正 `eet_xml_writer.py:71` 的 status 判断：`stage == 1` → `stage >= 1 and entry.translation` → 涉及文件: `src/transbridge/writer/eet_xml_writer.py`
2. 新增 locked(9) 和 hidden(-1) 的处理：stage==9 强制写译文（status=99），stage==-1 强制写原文（status=0 且不写译文）→ 涉及文件: `src/transbridge/writer/eet_xml_writer.py`
3. 检查 `plugin_writer.py` 中是否有类似的 stage 判断问题 → 涉及文件: `src/transbridge/writer/plugin_writer.py`

---

## Story-03: UI Stage 7 级可视化

**对应需求**: FR2.5.3, FR2.5.4, FR2.5.5, FR2.5.7
**归属 Epic**: ui-workbench（追加 Story-23）
**状态**: ✔️
**详细文档**: `plans/stage-unification/stories/story-03-ui-stage-visual.md`
**验收标准**:
- [ ] 状态标签行显示 7 个 Stage 标签（计数为 0 的隐藏）
- [ ] 筛选按实际 stage 值精确匹配（不再使用 stage>=2 宽泛条件）
- [ ] 行首 3px Stage 色条（使用 _STAGE_COLORS）
- [ ] 行背景色按 stage 区分（白/绿/浅红/浅灰）
- [ ] AI 翻译排除 locked/hidden 条目

**实现步骤**:
1. 导入 `STAGE_LABELS` 常量替换当前 `_STAGE_LABELS = {0: "未翻译", 1: "有疑问", 2: "已翻译"}`；`_build_stage_tags` 显示全部 7 个 stage 标签（从 STAGE_LABELS 迭代）→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. `_apply_all_filters` 中 stage 筛选改为精确 `e.stage == stage_val` 匹配；`_on_stage_tag_clicked` 中 `_stage_filters` 存储实际 stage 值（0/1/2/3/5/9/-1）→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. `_populate_table` 中行首 Col 0 标记列后，为每行 item 设置左侧 3px 色条（通过设置 item 的背景或使用 QTableWidgetItem 的边框模拟）；行背景色：stage∈{1,2,3,5}=浅绿，stage=9=浅红 #FFEBEE，stage=-1=浅灰 #F5F5F5，stage=0=白色 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. 统计卡和 `refresh` 中的 stage 统计判断修正：`done` 改为 `e.stage >= 1 and e.translation`（不变）；`untranslated` 改为 `e.stage == 0 and not e.translation`（不变）；新增 locked/hidden 计数 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
5. `ai_translator_window.py` 和 `translator.py` 中 AI 翻译作用域增加排除 stage=9（已锁定）和 stage=-1（已隐藏）的逻辑 → 涉及文件: `src/transbridge/ai_translator/translator.py`, `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`

## 架构依赖
- 引用的 ADR：ADR-001（TranslationEntry 数据模型）
- 依赖的模块：core-data-model → file-writing → ui-workbench → ai-translation（Story 顺序即依赖顺序）

## 风险与回退方案
- **风险 1**: 现有数据中可能存在 stage=3/5/9/-1 的条目（来自 ParaTranz 下载），UI 改为 7 级后这些条目将正确显示而非隐藏
- **风险 2**: EET 写回只有二元状态，stage≥2 的条目写回为 status=99（已翻译）可能丢失精确状态信息。缓解：EET 格式本身不支持 7 级状态，这是格式限制而非 bug
