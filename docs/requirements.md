# TransBridge 需求文档

> 本文档为回顾性需求文档，基于已实现的 v0.11+ 代码归纳整理。

---

## 1. 项目定义

TransBridge 是一款面向 SSE (Skyrim Special Edition) Mod 翻译工作者的桌面端本地化工具，核心目标是打通「插件解析 → 翻译管理 → 平台同步 → AI 辅助翻译 → 文件写回」的完整工作流。

## 2. 用户角色

| 角色 | 描述 |
|------|------|
| Mod 翻译者 | 主要用户。使用工具解析 Mod 插件、管理翻译条目、上传/下载 ParaTranz、使用 AI 辅助翻译 |
| ParaTranz 项目管理员 | 管理 ParaTranz 项目配置、API Token、成员权限 |

## 3. 功能需求

### FR1: 插件文件解析

**FR1.1 ESP/ESM/ESL 解析**: 系统 SHALL 解析 Bethesda 插件文件（ESP/ESM/ESL），提取所有可翻译字符串及其上下文信息。

**FR1.2 批量解析**: 系统 SHALL 支持同时选择多个 ESP 文件进行批量解析，每个文件独立管理。

**FR1.3 本地化字符串支持**: 系统 SHALL 支持读取本地化插件的 `.strings`、`.dlstrings`、`.ilstrings` 文件（松散文件及 BSA 归档）。

**FR1.4 上下文提取**: 系统 SHALL 为 NPC（性别/种族/职业）、INFO（说话者/情绪/任务关联）、DIAL（任务关联）提取额外上下文信息。

**FR1.5 EET XML 解析**: 系统 SHALL 解析 EET (Elder-scrolls-Enhanced-Translator) 格式的 XML 翻译文件。

**FR1.6 XT XML 解析**: 系统 SHALL 解析 XT (xTranslator) 格式的 XML 翻译文件。

**FR1.7 JSON 导入**: 系统 SHALL 支持从 DSD 格式 JSON 文件导入翻译条目。

**FR1.8 Strings 文件导入**: 系统 SHALL 支持从 `.strings` 文件导入翻译条目。

**FR1.9 XT SST 二进制解析与迁移源** — *2026-05-08 | 状态: 待方案 | 优先级: P1*: 系统 SHALL 解析 XT (xTranslator) 的 SST 二进制文件（`SSU8` / `SSU9` 格式），提取 EDID、FormID、字符串文本、译文、子记录关联数据等字段，转为 TranslationEntry 统一格式。SST 文件作为新的迁移源类型（与现有 EET/XT XML/Strings 并列），用于将 SST 中的译文追加合并到当前集合。

  - **FR1.9.1 二进制解析**: 解析器 SHALL 识别 `SSU8` / `SSU9` 魔数，按记录结构逐条解析。SSU8: 记录类型(2B) + EDID(8B) + Field_A(4B) + FormID(4B) + 字符串长度(4B LE) + UTF-16LE 字符串(N B) + 尾部长度(4B LE) + 尾部数据(M B) + 序号(4B LE) + 额外ID(2B LE)。SSU9: FormID(4B) + EDID(8B) + unk12(4B) + f2(4B) + str_idx(2B) + str_len(2B) + pad(2B) + UTF-16LE English(N B) + chn_len(4B) + UTF-16LE Chinese(N B) + extra/subrecords。解析失败时 SHALL 跳过异常条目并记录警告。
  - **FR1.9.2 迁移源集成**: SST 迁移源 SHALL 在 Step2 迁移源按钮区域提供加载入口。加载后 SST 条目与当前集合按匹配键（FormID + index）合并译文。
  - **FR1.9.3 格式校验**: 解析器 SHALL 校验魔数（非 `SSU8`/`SSU9` 则拒绝）、条目完整性（截断条目跳过并警告）、UTF-16LE 解码（失败则跳过并警告）、空文件（仅 header 无条目则返回空集合）。
  - **FR1.9.4 兼容性**: 新增解析器 SHALL NOT 修改现有 `XT_XmlParser` 的行为。SST 和 XML 两种格式独立解析，互不影响。
  - **FR1.9.5 SST 序列化写回** — *2026-05-09 | 状态: 待方案 | 优先级: P1*: 系统 SHALL 支持将修改后的译文序列化回 SST 二进制格式。基于已解析的 SST 文件作为模板，修改条目的 `translated_text` / `text` 字段后，重建完整 SST 文件（Header 原样复制，记录重新序列化时更新 chn_len + chn_text，extra/subrecords 原样保留）。支持输出到新文件（默认）或原地覆盖（`--in-place`），提供单条 `update_entry()` 和批量 `update_entries()` 接口。不增删记录，不从零创建新 SST 文件。

### FR2: 翻译条目管理

**FR2.1 统一数据模型**: 系统 SHALL 将所有来源（ESP/EET/XT XML/XT SST/JSON/Strings）的条目统一为 TranslationEntry 格式。

**FR2.2 条目预览与筛选**: 系统 SHALL 提供条目列表预览，支持按翻译阶段（stage）、分类（context category）、关键词筛选。

**FR2.3 多选操作**: 系统 SHALL 支持条目的多选（checkbox），可选择性操作部分条目。

**FR2.4 迁移源追加**: 系统 SHALL 支持为已加载的集合追加 EET/XT/Strings 迁移源，合并译文。

**FR2.5 Stage 状态系统统一** — *2026-05-07 | 状态: 已实现 | 优先级: P0*: 系统 SHALL 全项目统一使用 ParaTranz 平台的 7 级 stage 定义（0=未翻译、1=已翻译、2=有疑问、3=已检查、5=已审核、9=已锁定、-1=已隐藏）。TranslationEntry.stage 的语义 SHALL 对齐 ParaTranz，所有模块（converter/parser/writer/ui/ai_translator/paratranz）统一修正。

  - **FR2.5.1 Stage 语义定义**: TranslationEntry.stage SHALL 使用 ParaTranz 值——0=未翻译、1=已翻译、2=有疑问、3=已检查、5=已审核（未开启二次校对的项目审核词条时直接设为此状态）、9=已锁定（仅管理员可解锁，词条强制按译文导出）、-1=已隐藏（词条强制按原文导出）。内部不再使用自行定义的 stage 语义。
  - **FR2.5.2 数据源映射**: 各数据源的 stage SHALL 映射到 ParaTranz 值——ESP 解析后初始化为 stage=0（未翻译）；XT/EET/Strings 导入有译文时设为 stage=1（已翻译）；DSD JSON 导入有译文时设为 stage=1；ParaTranz 下载直接保留 API 返回的 stage 值。EET 的二元状态（status=99/traduit）映射为 stage=1。
  - **FR2.5.3 UI 状态标签**: Step2 的状态筛选标签 SHALL 显示全部 7 个 stage 标签（计数为 0 的隐藏），按实际 stage 值精确筛选。替代当前错误的 3 状态简化映射（{0:未翻译, 1:有疑问, 2:已翻译}）。
  - **FR2.5.4 Stage 色条**: 每行 SHALL 在行首显示 3px 宽的 Stage 色条，颜色使用已有的 `_STAGE_COLORS` 映射（0灰/1蓝/2橙/3青/5绿/9红/-1深灰）。行背景色：stage=0=白色，stage∈{1,2,3,5}=浅绿 #E8F5E9，stage=9=浅红 #FFEBEE，stage=-1=浅灰 #F5F5F5。
  - **FR2.5.5 Stage 与标记独立**: Stage 状态标签和标记标签（★/?/✓）SHALL 为两行独立的筛选标签，AND 叠加。Stage 反映平台客观状态，标记反映译者主观工作标签。
  - **FR2.5.6 写回修正**: EET/XT/ESP 写回时 SHALL 正确处理所有 stage 值——有译文（stage≥1）的条目写回为已翻译状态，已锁定（stage=9）强制写回译文，已隐藏（stage=-1）强制写回原文。
  - **FR2.5.7 AI 翻译适配**: AI 翻译的"未翻译"筛选 SHALL 使用 `stage == 0`（语义不变，值不变）。overwrite 模式下跳过 stage=9（已锁定）和 stage=-1（已隐藏）。

### FR3: ParaTranz 平台集成

**FR3.1 项目管理**: 系统 SHALL 支持查看全部项目和"我参与的"项目列表，新建项目。

**FR3.2 文件上传**: 系统 SHALL 支持将翻译条目分类导出并上传至 ParaTranz，支持多种上传模式（仅更新原文 / 导入译文-安全模式 / 强制覆盖译文）。

**FR3.3 文件下载**: 系统 SHALL 支持从 ParaTranz 下载翻译文件并合并到本地集合，支持分割文件的自动检测与合并。

**FR3.4 分类上传**: 系统 SHALL 按 context 分类（NPC_/INFO/BOOK 等）拆分文件上传，上传前弹出文件选择对话框。

**FR3.5 冲突检测**: 系统 SHALL 在上传前检测同名文件冲突，提供交互式冲突解决对话框。

**FR3.6 导出工件**: 系统 SHALL 支持触发 ParaTranz 导出并下载导出结果（zip）。

**FR3.7 术语管理**: 系统 SHALL 支持从 ParaTranz 同步术语库到本地。

### FR4: 文件写回

**FR4.1 ESP 写回**: 系统 SHALL 支持将译文写回 ESP/ESM 插件（inline 和 localised 两种模式）。

**FR4.2 EET XML 写回**: 系统 SHALL 支持更新已有 EET XML 文件或新建 EET XML 文件。

**FR4.3 XT XML 写回**: 系统 SHALL 支持更新已有 XT XML 文件或新建 XT XML 文件。

**FR4.4 本地化字符串输出**: 系统 SHALL 支持纯本地化模式（仅输出 `.strings` 文件，不修改 ESP）。

**FR4.5 批量写回**: 系统 SHALL 支持批量选择多个插件同时写回。

### FR5: AI 自动翻译

**FR5.1 多轮翻译策略**: 系统 SHALL 采用三轮策略：Round1 命名实体 → Round2 对话（按 quest 分组）→ Round3 长文本。

**FR5.2 术语库管理**: 系统 SHALL 维护多来源术语库（手动录入 / 自动提取 / ParaTranz 同步 / JSON 导入 / Excel 导入），按优先级合并。

**FR5.3 向量语义检索**: 系统 SHALL 支持基于 FAISS 的向量语义术语检索（可选），提供两阶段召回（精确匹配 + 语义检索）。

**FR5.4 LLM 客户端抽象**: 系统 SHALL 支持 OpenAI 兼容 API 和 Anthropic API，具备流式输出和连接取消能力。

**FR5.5 断点续传**: 系统 SHALL 在翻译过程中保存进度断点，中断后可恢复，正常完成后自动清理。

**FR5.6 暂停/停止控制**: 系统 SHALL 支持翻译过程中的暂停和停止操作，通过关闭 HTTP 连接立即中断。

**FR5.7 流式增量写回**: 系统 SHALL 在流式响应过程中实时解析并写回已完成的条目。

**FR5.8 批量翻译**: 系统 SHALL 支持跨多个插件的批量翻译，共享 in-flight 术语缓存。

**FR5.9 Prompt 模板**: 系统 SHALL 使用 TOML 模板文件管理 Prompt，支持按游戏和语言配置。

**FR5.10 AI 翻译作用域选择器** — *2026-05-07 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 将 AI 翻译窗口的作用域选择从固定的 3 个 RadioButton（全部/筛选可见/选中词条）升级为组合式选择器，支持按翻译状态、标记、分类三维度自由组合。翻译模式和润色模式共用同一选择器，自动调整默认值。与主表标记系统（FR7.10）完全解耦。

  - **FR5.10.1 三维度组合选择**: 作用域选择 SHALL 提供翻译状态（未翻译/已翻译/有疑问）、标记（★待处理/?有疑问/✓已确认/不限）、分类（对话/人名/地名等/不限）三个选择维度，每维度支持多选标签。三维度 AND 叠加。
  - **FR5.10.2 快捷预设**: SHALL 保留「全部未翻译」（翻译模式默认）「已翻译条目」（润色模式默认）「当前主表视图」作为快捷预设按钮。
  - **FR5.10.3 翻译/润色自动适应**: 切换到翻译模式时，状态维度 SHALL 默认选中「未翻译」；切换到润色模式时，默认选中「已翻译」。用户可手动调整。
  - **FR5.10.4 覆盖策略**: 作用域选择器中 SHALL 包含「覆盖已有译文」复选框（翻译模式默认不勾选，润色模式默认勾选）。
  - **FR5.10.5 条目数量预估**: 选择作用域后 SHALL 实时显示匹配条目数量和预计批次信息。
  - **FR5.10.6 与主表解耦**: 作用域选择 SHALL 不读取 `Step2PreviewWidget.get_selected_entries()`，不依赖主表标记或筛选状态（「当前主表视图」快捷预设除外）。

**FR5.11 AI翻译混合模式** — *2026-05-09 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 在 AI 翻译窗口中提供「混合模式」，允许用户在一次任务中同时执行翻译和润色两种操作，突破当前全局翻译/润色二选一的限制。混合模式下，用户在作用域选择器中通过新增的「动作」维度为条目分配翻译或润色动作。

  - **FR5.11.1 三模式制**: AI 翻译窗口顶部 SHALL 提供三个 RadioButton：翻译 / 润色 / 混合。选「翻译」或「润色」时保持现有行为不变（全局单一动作）。选「混合」时作用域选择器新增「动作」维度标签行（翻译 / 润色 / 跳过），允许用户为当前筛选范围指定动作。翻译模式和润色模式完全向后兼容。
  - **FR5.11.2 动作维度**: 混合模式下的「动作」维度 SHALL 提供三个标签：翻译（走 AutoTranslator 三轮翻译 + 可选后处理）、润色（走 LLMPolisher）、跳过（不做任何操作）。「动作」维度与现有的翻译状态/标记/分类三维度 AND 叠加，用户可自由组合（例如：分类=对话 + 动作=润色，标记=待处理 + 动作=翻译）。
  - **FR5.11.3 智能默认分配**: 切换到混合模式时，系统 SHALL 自动设置默认动作——翻译状态=未翻译 → 动作=翻译，翻译状态=已翻译 → 动作=润色，翻译状态=有疑问 → 动作=跳过。用户可手动调整任一维度的标签选择和动作分配。
  - **FR5.11.4 可配置执行顺序**: 混合模式下 SHALL 提供执行顺序选项（下拉或 RadioButton）：串行（先翻译后润色）或并行（翻译和润色同时执行）。串行模式下翻译产出可直接作为润色输入（如同一 ID 被同时分配翻译+润色）。并行模式下两个流水线独立执行，共享 `max_concurrent` 配额。
  - **FR5.11.5 统一进度窗口**: 混合执行时 SHALL 显示统一进度窗口，同时展示翻译和润色两个子进度条及各自的状态统计（成功/失败/跳过）。暂停/停止操作 SHALL 同时作用于两个流水线。
  - **FR5.11.6 合并报告**: 混合执行完成后 SHALL 生成一份合并报告，报告中明确区分「翻译部分」和「润色部分」的统计数据和条目详情。Excel 报告 SHALL 为单个文件含翻译 sheet 和润色 sheet。
  - **FR5.11.7 后处理润色冲突处理**: 混合模式下，翻译流水线的后处理配置中「润色」阶段（阶段2b）SHALL 自动禁用（置灰+提示「混合模式下由独立润色流水线处理」）。后处理的其他阶段（检测/修复/裁决）SHALL 保留正常功能。
  - **FR5.11.8 独立失败隔离**: 混合执行中翻译部分失败 SHALL NOT 阻断润色部分执行（反之亦然）。报告 SHALL 标注各部分的执行状态（✅成功 / ⚠ 部分失败 / ❌ 失败）。串行模式下翻译完全失败时润色仍正常执行（仅无翻译产出作为输入）。
  - **FR5.11.9 空作用域处理**: 混合模式下筛选后翻译条目数为 0 时 SHALL 跳过翻译仅执行润色（反之亦然）。两部分均为 0 时 SHALL 提示用户「当前筛选条件下无匹配条目，请调整作用域」。

**FR6.1 五阶段流程**: 系统 SHALL 执行检测 → 修复 → 润色 → 裁决 → 执行的五阶段后处理流水线。

**FR6.2 一致性检查**: 系统 SHALL 检测术语一致性、风格一致性等翻译质量问题。

**FR6.3 格式验证**: 系统 SHALL 验证译文格式（特殊标签、占位符等）的正确性。

**FR6.4 质量门禁**: 系统 SHALL 基于可配置阈值判定译文是否通过质量检查。

**FR6.5 LLM 修复**: 系统 SHALL 对检测到问题的条目使用 LLM 进行针对性修复。

**FR6.6 LLM 润色**: 系统 SHALL 支持独立启用的 LLM 润色阶段（全部/仅通过/仅问题三种范围，轻微/适中/深度三种级别）。

**FR6.7 LLM 裁决**: 系统 SHALL 对修复和润色结果进行最终裁决（pass/reject/pending），支持严格模式。

**FR6.8 后处理报告**: 系统 SHALL 在后处理完成后自动生成结构化 Excel 报告（Summary/Entries/Issues/Refinements/Arbitrations 五个 Sheet）。

**FR6.10 AI翻译/润色结果报告系统** — *2026-05-09 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 在 AI 翻译或润色完成后，生成结构化结果报告（应用内交互式对话框 + 自动生成 Excel 文件），替代当前的纯文本 QMessageBox 弹窗。报告 SHALL 覆盖翻译模式（含完整五阶段后处理数据）和润色模式（润色专属数据），支持单次和批量两种场景。

  - **FR6.10.1 应用内报告对话框**: 完成翻译/润色后 SHALL 弹出多 Tab 报告对话框——Tab1 汇总（统计卡片：总数/成功/失败/跳过、后处理检测/修复/润色/裁决分布）、Tab2 条目详情（可筛选可排序表格：原文/译文/最终译文/阶段/裁决结果/信心度）、Tab3 问题明细（问题类型/严重度/描述/建议）。翻译模式和润色模式 SHALL 使用不同的 Tab 和字段结构（翻译模式=全流水线数据，润色模式=润色专属数据如接受/拒绝/变更摘要）。
  - **FR6.10.2 报告对话框交互**: 报告条目表 SHALL 支持按裁决结果/状态/问题严重度筛选，支持按信心度排序。双击条目行 SHALL 自动跳转到主窗口 Step2 表格并定位到对应条目（通过 entry_id 匹配）。对话框提供「打开 Excel」和「关闭」按钮。
  - **FR6.10.3 Excel 自动生成**: 报告完成后 SHALL 自动生成 Excel 文件（`.xlsx`）到 `data/ai_translator/{esp_stem}/reports/` 目录。文件命名：`{esp_stem}_{mode}_report_{YYYYMMDD_HHMMSS}.xlsx`（mode = translate/polish）。翻译模式 Excel 包含 Summary/Entries/Issues/Refinements/Arbitrations 五个 Sheet；润色模式 Excel 包含 Summary/Entries/Polish 三个 Sheet。自动清理保留最近 20 份报告。
  - **FR6.10.4 报告生成后端**: 系统 SHALL 实现 `ReportGenerator` 类（`src/transbridge/ai_translator/post_processor/report_generator.py`），负责聚合翻译/后处理/润色结果数据，生成 Excel 文件并返回文件路径。数据源包括 `TranslationResult`、`PostProcessResult`、润色 `PolishResult` 字典。
  - **FR6.10.5 批量模式报告**: 批量翻译/润色完成后 SHALL 为每个插件独立生成报告（每个插件一份 Excel）。应用内 SHALL 先展示跨插件汇总弹窗（列出每个插件的完成状态和关键统计），点击单个插件可打开该插件的详细报告对话框。
  - **FR6.10.6 润色报告独立于预览**: 润色预览对话框（`_PolishPreviewDialog`）SHALL 保留不变，用于逐条接受/拒绝决策。润色报告在预览确认后生成，展示最终结果汇总（接受数/拒绝数/失败数/变更统计），不与预览合并。
  - **FR6.10.7 历史报告查看**: 系统 SHALL 在 AI 翻译窗口或工具面板中提供「历史报告」入口，列出过往生成的报告文件（按时间排序），双击使用系统默认程序打开 Excel 文件。
  - **FR6.10.8 异常处理**: 后处理未启用时报告 SHALL 仅含翻译汇总（无后处理/问题页）。翻译完全失败时仍弹出报告，汇总显示全失败。Excel 写入失败时 SHALL 提示错误但不阻断对话框展示。批量中某插件完全失败时标记状态但不影响其他插件报告生成。条目在报告生成后已被删除时跳转提示"条目不存在"。

**FR6.9 独立润色入口** — *2026-05-06 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 在 AI 翻译窗口中提供「润色模式」，允许用户跳过翻译阶段，对已翻译的选中条目直接执行 LLM 润色（调用 LLMPolisher）。

  - **FR6.9.1 模式切换**: AI 翻译窗口顶部 SHALL 提供模式切换（翻译模式 / 润色模式）。润色模式下，翻译范围选项替换为「润色选中已翻译条目」。无译文条目自动跳过。
  - **FR6.9.2 配置复用**: 润色参数（强度 light/moderate/aggressive、范围）SHALL 复用后处理标签页的现有润色配置控件。
  - **FR6.9.3 预览确认模式**: 系统 SHALL 新增配置项「润色后预览确认」。勾选时，润色完成后弹出预览窗口，逐条展示原文/原译文/润色结果，用户可逐条接受或拒绝；不勾选时润色结果直接写入条目。
  - **FR6.9.4 进度反馈**: 润色过程 SHALL 显示进度窗口，支持暂停和停止操作。
  - **FR6.9.5 异常处理**: 选中条目均无译文时 SHALL 提示用户；LLM 调用失败时 SHALL 保留原译文并提示失败原因。

**FR7.1 三步工作台**: 系统 SHALL 提供三步工作流界面：Step1 源文件解析 → Step2 词条预览与选择 → Step3 操作执行。

**FR7.2 集合统计面板**: 系统 SHALL 在左侧显示当前集合的分类树形统计。

**FR7.3 ParaTranz 管理面板**: 系统 SHALL 提供多标签页的项目管理界面（概览/文件/词条/术语/成员/历史/贡献/导出/讨论）。

**FR7.4 AI 翻译浮动窗口**: 系统 SHALL 提供 AI 翻译配置窗口，三标签页布局（LLM 与模型 / 术语库 / 后处理）。

**FR7.5 API 状态指示器**: 系统 SHALL 在状态栏显示 API 请求状态指示器（绿点/转圈/红点）。

**FR7.6 全局错误处理**: 系统 SHALL 集中处理 HTTP 401/403 错误，避免各组件重复弹窗。

**FR7.7 文件菜单统一入口** — *2026-05-06 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 将 Step1（集合管理+文件解析）和 Step3（上传/下载/写回）的所有操作入口统一迁移到主窗口「文件」菜单中。输入型控件（文件路径选择、参数配置）通过「菜单项 → 弹出对话框」模式交互；操作按钮直接映射为菜单项。迁移后右侧工作台仅保留 Step2（词条预览与筛选），进度反馈嵌入 Step2 区域。菜单项 SHALL 根据当前集合和项目状态动态启用/禁用。

  - **FR7.7.1 集合管理菜单**: 新建集合、导入 JSON、移除集合、切换当前集合通过文件菜单或子菜单操作。
  - **FR7.7.2 文件解析菜单**: 选择 ESP/EET/XT/已翻译插件/Strings 目录通过菜单项弹出文件对话框；解析来源模式、跳过空串等参数通过配置对话框设置；解析执行通过菜单项触发。
  - **FR7.7.3 操作菜单**: 上传 ParaTranz、下载合并、写回 ESP/EET/XT 及其批量变体作为菜单项，状态随集合/项目动态变化。
  - **FR7.7.4 进度反馈**: 解析和上传/下载/写回操作的进度条和状态文字嵌入 Step2 面板区域。
  - **FR7.7.5 布局简化**: 原 Step1 和 Step3 面板移除，右侧工作台仅保留 Step2 词条预览面板（全宽）。左侧集合统计面板保留（后续 FR7.8 优化）。

**FR7.8 工作台分类筛选优化** — *2026-05-06 | 状态: 已实现 | 优先级: P2*: 系统 SHALL 在 FR7.7 基础上进一步精简工作台 UI：去除冗余的 Step2 GroupBox 标题，移除左侧 CollectionStatsPanel，将分类统计功能以可交互标签组形式嵌入 Step2 表格上方，支持点击分类标签直接筛选词条表格。

  - **FR7.8.1 去除冗余标题**: 「步骤2：解析结果预览」GroupBox 标题移除。当前右侧工作台仅剩 Step2 单一组件，标题不再提供信息价值。
  - **FR7.8.2 移除左侧统计面板**: CollectionStatsPanel 完全移除，释放约 240px 水平空间给词条表格。原面板中的总条数/已译数等摘要信息由 Step2 四格统计卡（总词条/已有译文/迁移/未翻译）覆盖，不丢失。
  - **FR7.8.3 分类筛选标签组**: 在 Step2 表格上方（四格统计卡与筛选栏之间）新增分类标签行。标签显示格式为「分类名 + 数量」（如「对话 1,234」「书名 56」）。标签交互模式：单选切换——点击某标签高亮该分类并过滤表格；再次点击同一标签取消选中恢复全部；包含「全部」标签（始终显示，显示总数）用于恢复未筛选状态。无集合时整个标签行隐藏。
  - **FR7.8.4 与现有筛选栏联动**: Step2 现有的翻译状态下拉和类型下拉保留不动。分类标签与下拉筛选独立工作——标签选中后表格显示同时满足标签分类和下拉条件的词条。

**FR7.9 工作台交互统一化** — *2026-05-07 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 将 Step2 词条预览面板重构为单一表格视图，所有筛选、搜索、多选、编辑操作均在同一界面完成，消除当前"主表 + 详情弹窗"的割裂式交互。本需求替代 FR7.8 中的单选标签和弹窗筛选交互模式。

  - **FR7.9.1 多选分类标签**: 分类标签 SHALL 支持多选（同时选中多个分类），标签上的数字 SHALL 随其他标签选中状态联动更新（显示当前筛选条件下的交集数量）。点击已选中标签取消该分类筛选。修改自 FR7.8.3 的单选切换交互。
  - **FR7.9.2 翻译状态标签**: 翻译阶段筛选 SHALL 由下拉框改为可点击标签（如「未翻译 123」「已翻译 456」），与分类标签风格统一。支持多选。替代 FR7.8.4 中的翻译状态下拉。
  - **FR7.9.3 主表搜索栏**: Key、原文、译文文本搜索框 SHALL 嵌入主表顶部（分类/状态标签行下方），输入后实时过滤表格。替代原 _EntryDetailDialog 中的搜索功能。
  - **FR7.9.4 行内编辑**: 双击译文单元格 SHALL 进入行内编辑模式，用户可直接修改译文。编辑完成后（回车或焦点离开）保存到 TranslationEntry。原文不可编辑。
  - **FR7.9.5 多选机制** — *已被 FR7.10 替代*: 原要求移除复选框改用 Ctrl/Shift 行选，实际体验中选中状态在筛选后丢失。已由 FR7.10 标记系统替代。
  - **FR7.9.6 移除详情弹窗**: _EntryDetailDialog 类 SHALL 完全移除。其功能已被主表搜索栏（文本筛选）、多选标签（分类筛选）、行内编辑（查看/编辑译文）、表格行选（多选）完全覆盖。

**FR7.10 工作台标记与可视化系统** — *2026-05-07 | 状态: 已被 FR7.11 替代 | 优先级: P1*: 系统 SHALL 将复选框选中机制升级为三态标记列（★待处理/?有疑问/✓已确认），配合行背景色按翻译状态区分和标记筛选/聚焦功能，使 Step2 从筛选表格升级为翻译审校工作台。

  - **FR7.10.1 三态标记列**: 表格第 0 列 SHALL 为标记列，显示三态标记图标（无标记 / ★待处理 / ?有疑问 / ✓已确认）。点击该列循环切换：无→★→?→✓→无。标记存储在 `_entry_marks: dict[str, str]`（entry.id → mark_type），在筛选/搜索/切换集合间持久化（会话内）。
  - **FR7.10.2 行背景色**: 表格行 SHALL 按翻译阶段着色：未翻译（stage=0 且无译文）= 白色默认，已翻译（有译文）= 浅绿 #E8F5E9，有疑问（stage=1）= 浅黄 #FFF8E1。
  - **FR7.10.3 标记筛选标签**: 工具栏或标签行 SHALL 新增「★待处理」「?有疑问」「✓已确认」可点击标签按钮，筛选对应标记的条目。与分类筛选、状态筛选、文本搜索 AND 叠加。
  - **FR7.10.4 聚焦开关**: SHALL 提供「只看已标记」切换按钮（👁 图标），一键过滤出所有有标记的条目（无论标记类型）。无标记条目时按钮禁用。
  - **FR7.10.5 标记计数**: 底部状态栏 SHALL 显示各标记类型的计数（"★ N / ? N / ✓ N | 显示 M 条（共 K 条）"）。
  - **FR7.10.6 与 AI 翻译解耦**: `get_selected_entries()` SHALL 保持返回 ★ 标记条目（向后兼容）。AI 翻译窗口的作用域选择（翻译/润色哪些条目）由 AI 翻译面板自行处理，不耦合到标记系统。

**FR7.13 Agent 框架全面升级** — *2026-05-10 | 状态: 已方案（Phase 1 已实现，Phase 2 待方案）| 优先级: P1*: 系统 SHALL 将 smart_assistant 从带工具的 LLM 对话面板升级为完整的翻译 Agent 框架，分两阶段实施。Phase 1（已实现，5 Story，QA 通过）覆盖 Skill 系统、文件上传、长期记忆、Reflexion 自纠错。Phase 2（待方案，分三批实施）覆盖多 Agent 协作、安全护栏、Graph 编排、可观测性、MCP Server 五个能力。

  **Phase 1（已实现 — 2026-05-10 QA 通过）**:

  - **FR7.13.1 Skill 系统**: 系统 SHALL 提供用户可自定义的能力模块（Skill）管理功能。每个 Skill 包含名称、描述、触发条件、Prompt 模板、关联工具列表。用户可创建/编辑/启用/禁用 Skill，agent 在推理过程中按需匹配和调用。Skill 模型参考 bm-* 系列：声明式定义，热加载，可组合。
  - **FR7.13.2 文件上传与知识注入**: 系统 SHALL 支持用户上传外部文件作为 agent 的参考知识源。支持格式：文本类（Excel .xlsx、CSV、Markdown .md、纯文本 .txt、JSON）、二进制类（PDF、Word .docx）、ParaTranz 导出格式。上传后系统解析文件内容，构建可被 agent 在翻译/校对/术语查询时引用的知识索引。典型场景：用户上传纠错表 → agent 翻译时自动对照修正；上传风格指南 → agent 润色时参考规范。
  - **FR7.13.3 长期记忆**: 系统 SHALL 提供跨会话持久化的长期记忆能力，包含两个维度：(a) 翻译上下文记忆 — 用户偏好、术语决策、纠错历史、翻译风格选择等，下次翻译时自动加载相关记忆；(b) 全量对话历史 — 完整的对话记录可回溯。记忆存储基于向量嵌入（复用已有 FAISS 基础设施），支持语义检索 + 精确匹配两阶段召回。记忆数据存储在项目目录下，随项目切换。
  - **FR7.13.4 Reflexion 自纠错**: 系统 SHALL 在工具执行失败时自动触发自纠错机制。LLM 分析失败原因（错误消息/异常类型），调整参数或换策略，自动重试（最多 N 次，默认 3 次）。重试耗尽仍失败则反馈用户并继续 ReAct 循环。自纠错仅作用于工具调用层，不改变正常 LLM 响应流程。纠错过程对用户透明（显示"正在重试…"状态）。

  **Phase 2（待方案 — 分三批实施）**:

  **第一批（P0 — 核心能力）**:

  - **FR7.13.6 多 Agent 协作**: 系统 SHALL 支持多个专业 Agent 并行协作，由编排 Agent（Orchestrator Agent）分配任务和汇总结果。Agent 类型包括但不限于翻译 Agent、校对 Agent、术语 Agent——系统 SHALL 支持用户自定义 Agent 类型及其关联的工具集和 Skill。同一类型的 Agent SHALL 支持多实例并行（如同时翻译多个项目），实例间资源隔离（各自独立的工具命名空间和执行上下文）。编排 Agent SHALL 负责任务分解、Agent 调度、结果汇总和冲突裁决。
    - **FR7.13.6.1 Agent 定义与注册**: 系统 SHALL 提供 Agent 定义格式（名称、角色、关联工具集、关联 Skill、System Prompt）。Agent 注册到 AgentRegistry，支持运行时启用/禁用。预置翻译 Agent（关联翻译/术语查询工具）、校对 Agent（关联校对/一致性检查工具）、编排 Agent（关联任务分解/结果汇总工具）。
    - **FR7.13.6.2 任务分解与调度**: 编排 Agent SHALL 将用户请求分解为子任务（如"翻译 Dragonborn 插件 + 校对 + 润色"分解为 3 个子任务），按依赖关系调度到对应 Agent 执行。无依赖的子任务 SHALL 并行执行（利用 ExecutionEngine 的层级并行能力），有依赖的子任务 SHALL 在前置任务完成后串行执行。
    - **FR7.13.6.3 Agent 间通信**: Agent 间 SHALL 通过编排 Agent 间接通信，不直接点对点通信。子 Agent 的输出经编排 Agent 汇总后作为下游 Agent 的输入。编排 Agent SHALL 负责格式转换和数据映射。
    - **FR7.13.6.4 ToolRegistry 命名空间**: ToolRegistry SHALL 支持 namespace 机制（如 `register(spec, namespace="translator")`），编排 Agent 可查看全部工具，执行 Agent 仅可见其命名空间内的工具。防止工具误用（如校对 Agent 不应直接修改译文）。
    - **FR7.13.6.5 多项目并行**: 同一类型 Agent SHALL 支持创建多个实例，每个实例绑定到不同项目上下文（项目路径、术语库、记忆存储）。实例间通过独立的 ThreadPoolExecutor worker 运行，共享 max_workers 配额。
    - **FR7.13.6.6 异常隔离**: 单个 Agent 执行失败 SHALL NOT 阻断其他 Agent。编排 Agent 在汇总时 SHALL 标记失败子任务及其错误信息，并决定是否重试、跳过或终止整个任务。

  - **FR7.13.8 安全护栏**: 系统 SHALL 实现三层安全机制——工具调用权限分级、敏感操作确认、输入输出内容校验。护栏在 ExecutionEngine 的工具执行路径上以中间件模式注入（复用 Phase 1 RetryHandler 的注入模式），不改变工具本身的实现逻辑。
    - **FR7.13.8.1 权限分级**: 所有工具 SHALL 声明权限级别——只读（read，如术语查询/知识检索/状态查看）、读写（write，如翻译条目修改/标签编辑/记忆写入）、管理（admin，如写回 ESP/EET/XT、Skill 删除、记忆清除）。ToolSpec SHALL 增加 `permission: str` 字段。
    - **FR7.13.8.2 敏感操作确认**: 管理级（admin）工具调用 SHALL 在 ExecutionEngine 执行前暂停，通过 `step_requires_confirmation` 信号弹窗要求用户确认。用户确认后继续执行，拒绝后跳过该步骤并向 LLM 反馈被拒原因。写入级（write）工具可通过配置选择是否需要确认（默认不需要）。只读级（read）工具不需要确认。
    - **FR7.13.8.3 输入校验**: 工具调用前 SHALL 校验输入参数——参数类型检查、字符串长度限制、注入攻击模式检测（SQL/XSS/命令注入特征）。校验失败 SHALL 拒绝执行并返回错误给 LLM，不进入工具执行体。
    - **FR7.13.8.4 输出校验**: 工具执行后 SHALL 校验输出——返回值类型检查、数据大小限制（防止返回超大结果撑爆上下文窗口）、敏感信息检测（防止 API key 等泄露到工具输出中）。
    - **FR7.13.8.5 护栏配置**: 护栏行为 SHALL 可配置——是否启用敏感操作确认（默认启用）、输入输出大小限制（默认 100KB）、权限违规时的处理策略（拒绝 + 告知 LLM / 仅警告 / 静默跳过）。
    - **FR7.13.8.6 护栏日志**: 所有权限拒绝和校验失败 SHALL 记录到护栏日志，包含时间、工具名、触发规则、输入摘要。护栏日志在可观测性面板中展示。

  **第二批（P1 — 基础设施增强）**:

  - **FR7.13.7 Graph 编排**: 推理流程 SHALL 从当前的 DAG 拓扑排序执行升级为有状态图编排，支持条件分支、循环、人机协同节点。Graph 引擎基于现有 ExecutionEngine 扩展实现（自研轻量方案，零新依赖），ExecutionEngine 从 DAG 执行器演进为有状态图执行器。定义 `GraphExecutor` 抽象基类，当前实现为 `StatefulDAGExecutor`，预留未来替换接口。
    - **FR7.13.7.1 图模型**: 图 SHALL 由节点（Node）和边（Edge）组成。节点类型——ActionNode（工具调用，现有 step 模型）、ConditionNode（条件分支，基于上一步结果决定下一节点）、LoopNode（循环，包含子图 + max_iterations + 退出条件）、HumanConfirmNode（人机协同确认，暂停等待用户决策）。边类型——固定边（always）、条件边（基于 NodeResult 的条件表达式）、回边（loop 内部回到循环起点）。
    - **FR7.13.7.2 条件分支**: ConditionNode SHALL 评估条件表达式（如 `result.data["quality_score"] < 0.7`），根据 true/false 路由到不同下游节点。条件表达式支持访问上一步的 StepResult 字段（success/message/data）。
    - **FR7.13.7.3 循环控制**: LoopNode SHALL 包含子图（sub_nodes）和 loop_config（max_iterations、exit_condition）。每轮迭代后检查退出条件（如 `result.data.get("all_passed") == True`），满足则跳出循环。max_iterations 硬上限防止死循环（默认 10）。循环内支持嵌套条件分支，不支持嵌套循环（Phase 2 限制）。
    - **FR7.13.7.4 人机协同**: HumanConfirmNode SHALL 在到达时暂停图执行，通过 `step_requires_decision` 信号向 UI 发送确认请求（含提示文本和选项列表），等待用户通过 `provide_decision(step_id, choice)` 响应后继续。暂停 SHALL NOT 阻塞 UI 线程（执行线程通过 QEventLoop local loop 等待）。支持可配置超时（默认 300 秒），超时采用默认策略（配置为 continue/skip/abort）。
    - **FR7.13.7.5 状态持久化**: 图执行状态 SHALL 支持序列化与恢复。Checkpoint 包含——当前节点位置（node_id）、已完成节点的 StepResult 列表、图状态字典（可 JSON 序列化）。Checkpoint 在每层执行后自动保存，异常中断后可从最近 checkpoint 恢复（跳过已完成节点）。Checkpoint 数据约束：`data` 字段仅允许 dict/list/str/int/float/bool/None 类型。
    - **FR7.13.7.6 与现有系统的兼容**: `execute(steps)` 接口 SHALL 保持向后兼容（steps 为简单 dict 列表时视为线性 DAG）。新图模型通过 `execute_graph(graph: GraphSpec)` 接口暴露。现有 ChatWidget 和 PlanCard 的调用方式不变。

  - **FR7.13.9 可观测性**: 系统 SHALL 提供对话追踪、工具调用链记录和 token 消耗统计三个观测维度。观测数据通过现有 ExecutionEngine 的 pyqtSignal 管道收集，在智能助手面板中以可交互形式展示。
    - **FR7.13.9.1 ReAct 步骤追踪**: 每轮 ReAct 循环（LLM 推理 → 工具调用 → 结果反馈 → LLM 推理）SHALL 记录——轮次编号、LLM 输入 token 数、LLM 输出内容摘要、本轮调用的工具列表及各自耗时和结果状态。追踪数据在对话结束后可导出为结构化文件。
    - **FR7.13.9.2 工具调用链**: 每个工具调用 SHALL 记录——调用时间戳、工具名称、输入参数（截断至 500 字符）、输出结果摘要（截断至 500 字符）、执行耗时（ms）、成功/失败状态、重试次数（如触发 Reflexion）。调用链数据在 single conversation 内聚合，支持按时间线展开/折叠。
    - **FR7.13.9.3 Token 消耗统计**: 系统 SHALL 在对话级别和会话级别分别统计 token 消耗——输入 token 总数、输出 token 总数、按模型分组统计。对话结束时在消息区域底部显示本轮 token 消耗摘要。会话级别统计在智能助手面板状态栏持久显示。
    - **FR7.13.9.4 观测数据存储**: 观测数据 SHALL 存储在项目目录下（`data/projects/{project}/{variant}/observability/`）。对话追踪和调用链以对话 ID 为文件名存储为 JSON。Token 统计以会话 ID 聚合。历史数据保留最近 30 天，过期自动清理。
    - **FR7.13.9.5 观测面板 UI**: 智能助手面板 SHALL 提供「观测」Tab 或侧栏——Token 使用仪表盘（今日/本周/本月）、最近工具调用列表（可展开查看详情）、对话轮次时间线。观测面板为只读展示，不影响对话流程。

  **第三批（P2 — 可选扩展）**:

  - **FR7.13.5 MCP Server 协议**: 系统 SHALL 将 ToolRegistry 中注册的工具暴露为 MCP (Model Context Protocol) 兼容的 Server 端点，支持外部 MCP Client 通过标准协议发现和调用 TransBridge 工具。MCP Server 作为本地 JSON-RPC 服务运行，默认仅监听 localhost。
    - **FR7.13.5.1 工具发现**: MCP Server SHALL 实现 `tools/list` 方法，返回 ToolRegistry 中所有已注册工具的列表（名称、描述、参数 schema）。参数 schema SHALL 从 ToolSpec 的 args 定义自动生成 JSON Schema。
    - **FR7.13.5.2 工具调用**: MCP Server SHALL 实现 `tools/call` 方法，接收工具名称和参数，通过 ToolSpec.execute() 执行并返回结果。执行上下文（ctx）SHALL 使用 MCP 会话关联的 AppContext。
    - **FR7.13.5.3 传输协议**: MCP Server SHALL 支持 stdio 传输（标准输入输出流，供本地 MCP Client 通过子进程方式接入）。HTTP/SSE 传输作为可选扩展（P3）。
    - **FR7.13.5.4 安全约束**: MCP 暴露的工具 SHALL 遵循 FR7.13.8 安全护栏的权限分级。管理级（admin）工具在 MCP 通道中默认不暴露，需显式配置白名单。写入级（write）工具在 MCP 通道中可配置需确认或静默拒绝。
    - **FR7.13.5.5 MCP 配置**: MCP Server 的启用/禁用、监听端口（HTTP 模式）、暴露工具白名单 SHALL 通过 LLMConfig INI 文件的 `[mcp]` section 配置。

  **Phase 1 异常场景**:
  - Skill 定义文件格式错误 → 跳过该 Skill 并提示用户，不影响其他 Skill 加载
  - 上传文件无法解析（损坏/加密/格式不支持）→ 提示用户转换格式或手动处理
  - 向量索引损坏 → 自动重建，重建期间降级为精确匹配
  - 自纠错重试全部失败 → 反馈用户原始错误信息，不阻塞后续 ReAct 循环

  **Phase 2 异常场景**:
  - Agent 执行失败 → 编排 Agent 标记失败，其他 Agent 继续，汇总时提示用户
  - 人机协同节点超时（默认 300s）→ 按配置采用默认策略（继续/跳过/终止）
  - 权限不足调用被拒 → 护栏返回拒绝原因，LLM 可调整策略或向用户说明
  - Checkpoint 序列化失败（data 含不可序列化对象）→ 跳过该 data 字段，写入警告日志，不阻断执行
  - 观测数据写入失败 → 不影响核心对话功能，静默降级（仅内存保留）
  - MCP 连接断开 → 自动清理会话，不影响本地工具和对话
  - Token 统计因 API 响应不完整而缺失 → 标记为估算值，不阻断对话

**FR7.12 SmartAssistant 代码分层** — *2026-05-10 | 状态: 已实现 | 优先级: P2*: 系统 SHALL 将智能助手（SmartAssistant）的后端业务逻辑与 UI 界面代码分离到独立的包中。当前所有组件（13 个文件）全部位于 `src/transbridge/ui/tools/smart_assistant/` 目录下，业务逻辑与 UI 混在一起。重构后，后端组件迁移至新建的 `src/transbridge/smart_assistant/` 包，UI 组件保留在原目录，跨包 import 同步更新。

  - **FR7.12.1 后端包建立**: SHALL 新建 `src/transbridge/smart_assistant/` 包（含 `__init__.py`），容纳 6 个后端组件：`conversation_manager.py`（对话管理）、`chat_worker.py`（LLM 调用线程）、`execution_engine.py`（DAG 执行引擎，含 `StepResult` 数据类）、`tool_registry.py`（工具注册表 + 6 个 v1 工具实现）、`context_builder.py`（上下文构建器）、`prompts.py`（System Prompt 模板）。后端组件 SHALL NOT 依赖 UI 组件。
  - **FR7.12.2 UI 包精简**: `src/transbridge/ui/tools/smart_assistant/` SHALL 仅保留 6 个界面组件：`panel.py`（DockWidget 面板）、`chat_widget.py`（聊天区域 + 双模式循环控制）、`message_bubble.py`（消息气泡）、`quick_actions.py`（快捷指令面板）、`tool_card.py`（ToolCard + BatchToolCard）、`plan_card.py`（计划确认卡片）。`__init__.py` SHALL 保持 `SmartAssistantPanel` 导出不变。
  - **FR7.12.3 跨包导入更新**: 搬迁后 `chat_widget.py` 的 4 处后端 import（`conversation_manager`/`chat_worker`/`execution_engine`/`tool_card`→后两者为 UI 保持不动）和 `plan_card.py` 的 1 处后端 import（`execution_engine.StepResult`）SHALL 更新为 `from src.transbridge.smart_assistant.xxx import ...` 的绝对导入。`prompts.py` 对 `tool_registry` 的 import SHALL 更新为包内相对导入。`main_window.py` 的 import 路径 SHALL 保持不变。
  - **FR7.12.4 异常处理**: 搬迁后 import 错误 → 启动时 ImportError，需逐文件验证；循环导入 → 依赖方向为 UI→后端（单向），不应出现循环。

**FR7.11 自定义标签系统** — *2026-05-07 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 用用户自定义的多标签系统替代 FR7.10 的固定三态标记（★/?/✓）。用户可创建任意数量和名称的标签（带颜色），每个条目可打上多个标签，通过右键菜单分配。本需求替代 FR7.10。

  - **FR7.11.1 标签库管理**: 系统 SHALL 提供标签库管理功能——工具栏「管理标签」按钮弹出标签管理对话框，支持创建/编辑/删除标签。每个标签有名称和颜色属性。右键菜单底部「+ 新建标签…」可快速创建。
  - **FR7.11.2 右键菜单分配**: 右键点击条目行 SHALL 弹出标签列表菜单，显示所有已创建的标签（勾选表示已分配）。勾选/取消勾选切换该标签的分配。菜单底部有「管理标签…」和「+ 新建标签…」入口。
  - **FR7.11.3 多标签支持**: 每个条目 SHALL 可同时拥有多个标签。标签数据存储在 `_entry_labels: dict[str, set[str]]`（entry_id → set[label_id]）。标签库存储在 `_label_library: dict[str, dict]`（label_id → {name, color}）。
  - **FR7.11.4 彩色圆点显示**: 标签列 SHALL 显示彩色圆点（每个标签一个圆点，标签色填充）。无标签时列空白。多个圆点紧凑排列。鼠标悬停圆点区域 SHALL 显示 tooltip 列出所有标签名。
  - **FR7.11.5 动态标签筛选**: 标签筛选行 SHALL 动态显示标签库中的标签按钮（非固定三态），点击筛选对应标签条目。计数为 0 的标签隐藏。与分类/状态/搜索 AND 叠加。
  - **FR7.11.6 聚焦开关**: SHALL 保留「只看已标记」聚焦按钮，改为过滤出所有有标签的条目（`_entry_labels` 非空）。无标签条目时按钮禁用。

### FR8: 项目持久化与翻译版本管理

**FR8.1 项目模型** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 引入「项目」作为翻译工程的顶层管理单元。一个项目代表一个 Mod 的完整翻译工作（如"Dragonborn Translation"），可包含多个源文件集合（ESP/EET/XT/Strings），以及多个翻译版本（Variant）。当项目包含多种格式源文件时，SHALL 以 ESP 插件的 key 格式为主格式。数据模型为三层结构：项目(Project) → 版本(Variant) → 源文件翻译数据。

**FR8.2 工作区文件** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 使用 `workspace.json` 管理全局状态——项目列表、当前活跃项目和版本、源文件列表、筛选状态、UI 布局。启动时自动恢复上次工作状态（含版本选择）。workspace.json 需扩展 `active_variant` 和项目下 `variants` 列表字段。

**FR8.3 源文件存储** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 每个版本的翻译数据 SHALL 存储在 `data/projects/{project_name}/{variant_name}/` 目录下。当前状态保存为 `current.json`，内容包括：`labels`（entry_id → [label_id] 映射）、`translations`（entry_id → translation 映射）、元数据（版本名称、创建时间、复制来源）。历史快照存储在 `snapshots/{snapshot_name}.json`。源文件解析结果（key/original/context）不在存储范围内——每次启动时重新解析源文件，仅恢复译文和标签数据。

**FR8.4 自动恢复** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 启动时 SHALL 自动读取 `workspace.json`，恢复上次活跃的项目和版本，重新解析源文件，加载对应版本的 `current.json`（译文和标签数据），恢复筛选状态。若 `current.json` 不存在或源文件哈希变更，SHALL 初始化空白版本状态并提示用户。

**FR8.5 快照操作** — *2026-05-08 | 状态: 已实现 | 优先级: P2*: 用户 SHALL 可以在版本内「另存为快照」将当前译文和标签保存为命名版本，以及「加载快照」从历史版本恢复当前数据。快照按版本粒度操作（一个快照包含该版本下所有源文件的翻译数据），互不影响。

**FR8.6 自动保存** — *2026-05-08 | 状态: 已实现 | 优先级: P2*: 系统 SHALL 支持可配置的自动保存策略——定时保存（每 N 分钟）和/或操作触发保存（标记/编辑译文后防抖 2 秒）。保存目标为当前版本的 `current.json`。同时支持手动触发保存。

**FR8.7 项目切换** — *2026-05-08 | 状态: 已实现 | 优先级: P2*: 用户 SHALL 可以通过工作台工具栏或菜单切换活跃项目。切换项目时，当前项目+版本的状态自动保存，新项目的源文件、版本列表、译文、标签、快照自动加载。

**FR8.8 单体项目文件** — *2026-05-08 | 状态: 已实现 | 优先级: P2*: 系统 SHALL 支持 `.transbridge` 单体项目文件格式。该文件本质为 ZIP 压缩包，内含项目配置、所有版本的全部翻译数据和历史快照。支持「文件 → 另存为 .transbridge」导出和双击/拖入打开。日常编辑仍在文件夹中进行，.transbridge 作为分享/归档/备份的便携格式。

**FR8.9 翻译版本模型** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 系统 SHALL 引入「翻译版本」（Translation Variant）作为译文和标签数据的分组键。一个项目可包含多个版本（如"和光术语版""ank术语版"）。版本之间共享源文件解析结果（Entry 的 key/original/context 不变），但各自独立维护 translation、label、snapshot 数据。版本不影响 ESP/EET/XT 解析逻辑——源文件仅解析一次，按版本切换时仅替换译文和标签视图。

**FR8.10 版本创建与复制** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 用户 SHALL 可以创建空白新版本（无译文，标签为空），或从已有版本复制创建新版本。复制时 SHALL 继承源版本的全部译文和标签数据作为起点，之后两个版本独立修改互不影响。版本名称由用户自定义。

**FR8.11 版本切换** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 用户 SHALL 可以通过工作台工具栏或菜单切换活跃版本。切换版本时，系统 SHALL 根据用户配置执行以下行为之一：(a) 自动保存当前版本译文和标签后切换；(b) 弹出对话框提示用户保存或放弃修改。配置项存储在 `workspace.json` 中。切换后 Step2 表格刷新显示新版本的数据，标签库随版本切换（每个版本独立的标签库）。

**FR8.12 版本写回** — *2026-05-08 | 状态: 已实现 | 优先级: P1*: 写回操作 SHALL 支持按版本分别输出。用户可选择：(a) 仅写回当前版本的译文；(b) 分别写回所有版本，每个版本的输出文件写入独立的子目录（目录名为版本名），不修改 ESP/EET/XML 源文件名。默认行为建议为分版本分目录输出。

## 4. 非功能需求

### NFR1: 性能

- ESP 解析：单个插件 ≤ 30 秒（中等规模 MOD）
- AI 翻译：支持并发批次，默认最大并发数 3
- UI 响应：所有耗时操作在后台线程执行，不阻塞 UI

### NFR2: 可靠性

- 断点续传：翻译中断后可从最近 checkpoint 恢复，不丢失已完成结果
- 错误隔离：单个批次失败不影响其他批次
- 递归重试：翻译遗漏的条目自动拆分重试

### NFR3: 兼容性

- 支持 Windows 10/11
- 支持 Skyrim SE (1.5.97 / 1.6.x) 全版本插件格式
- SSE 插件格式：ESP、ESM、ESL（含 ESL-flagged ESP）
- 解包工具集成：支持 xEdit / xTranslator 导出的 DSD JSON 和 XML 格式

### NFR4: 安全性

- API Token 存储在本地 INI 配置文件（明文，待改进）
- 不上传用户的 API Key 到第三方服务

### NFR5: 可扩展性

- 新增 LLM 提供商：实现 LLMClient 子类并注册到工厂方法
- 新增解析器：实现解析逻辑 + TranslationEntry 工厂方法
- 新增导出格式：实现 Writer 类并注册到 UI
- 新增术语来源：实现加载方法并注册到 TermDatabaseManager

### NFR6: 打包分发

- 支持 PyInstaller 单文件打包（transbridge.spec）
- 数据目录自动适应开发/打包环境

## 5. 系统边界

### 在范围内

- ESP/ESM/ESL 插件的可翻译字符串提取与写回
- EET/XT XML 翻译文件的读写
- ParaTranz 翻译平台 API 集成
- AI 辅助翻译（LLM 调用 + 术语管理 + 后处理）
- 本地术语库管理（JSON/Excel/动态提取）
- 桌面 GUI（PyQt6）

### 不在范围内

- xEdit 脚本本身（用户自行使用 xEdit 导出 DSD JSON）
- 翻译记忆库（TMX 格式）管理
- 机器翻译引擎（非 LLM 的传统 MT）
- 协作翻译的实时同步
- Web 界面
- macOS / Linux 支持
- 移动端

## 6. 需求变更历史

| 日期 | 变更内容 | 来源 |
|------|---------|------|
| 2026-05-06 | 回顾性创建需求文档（基于 v0.11+ 代码） | 文档体系初始化 |
| 2026-05-06 | 新增 FR7.7 文件菜单统一入口（工作台 UI 重构） | /bm-analyze |
| 2026-05-06 | 新增 FR7.8 工作台分类筛选优化（移除左侧面板，分类标签嵌入 Step2） | /bm-analyze |
| 2026-05-06 | 新增 FR6.9 独立润色入口（AI 翻译窗口中增加润色模式） | /bm-analyze |
| 2026-05-07 | 新增 FR7.9 工作台交互统一化（多选标签+主表搜索+行内编辑，替代弹窗交互） | /bm-analyze |
| 2026-05-07 | 新增 FR7.10 工作台标记与可视化系统（三态标记列+行背景色+聚焦开关，替代复选框选中） | /bm-analyze |
| 2026-05-07 | 新增 FR5.10 AI 翻译作用域选择器（三维度组合选择：状态+标记+分类，翻译/润色自动适应） | /bm-analyze |
| 2026-05-07 | 新增 FR8 持久化与工作区管理（项目模型+源文件+快照+自动恢复+自动保存） | /bm-analyze |
| 2026-05-08 | 扩展 FR8 为「项目持久化与翻译版本管理」：新增 FR8.9-FR8.12 翻译版本模型/创建复制/切换/写回；原有 FR8.1-FR8.8 更新为三层数据模型（项目→版本→翻译数据） | /bm-analyze |
| 2026-05-07 | 新增 FR2.5 Stage 状态系统修正（对齐 ParaTranz 7 级 stage，Stage 色条，与标记系统独立） | /bm-analyze |
| 2026-05-07 | 新增 FR7.11 自定义标签系统（用户自定义标签库+右键菜单分配+多标签+彩色圆点，替代 FR7.10） | /bm-analyze |
| 2026-05-08 | 新增 FR1.9 XT SST 二进制解析与迁移源（SSU8 解析器 + FormID 提取 + 迁移源集成） | /bm-analyze |
| 2026-05-09 | 新增 FR6.10 AI翻译/润色结果报告系统（应用内多Tab对话框 + Excel自动生成 + 历史报告查看） | /bm-analyze |
| 2026-05-09 | 新增 FR5.11 AI翻译混合模式（三模式制+动作维度+统一进度+合并报告） | /bm-analyze |
| 2026-05-10 | 新增 FR7.12 SmartAssistant 代码分层（后端6组件搬迁+跨包导入更新） | /bm-analyze |
| 2026-05-10 | 新增 FR7.13 Agent 框架全面升级（Phase1: Skill+文件+记忆+自纠错; Phase2: MCP+多Agent+Graph+护栏+可观测） | /bm-analyze |
| 2026-05-10 | 展开 FR7.13 Phase 2 子需求细节：分三批实施（P0 多Agent协作+安全护栏 / P1 Graph编排+可观测性 / P2 MCP Server），Graph 引擎确定为自研轻量方案（零新依赖），每个子需求扩展为详细验收标准+边界条件+异常场景 | /bm-analyze (via /bm-orchestrator --auto) |
