# 备忘录：后续待办事项

> 本文件记录用户明确要求「后续再做」的事项，避免在本次开发中遗漏或误实现。

## 2026-08-14

### MEMO-001: 批量转换词典（Batch Dictionary Conversion）

- **描述**: 用户后续要做「批量转换词典」能力——把已有的翻译内容批量转换为词典（翻译记忆），区别于本次的单次「存为词典」动作。
- **状态**: 待办，不做于本次 FR15 开发
- **关联**: Translation Memory（翻译记忆/词典）系统
- **备注**: 本次只需做词典系统的「设计 + 保存 + 使用」三件事；「导入翻译」能力已存在（XT/EET/ESP 解析 → Collection），无需重做。

### MEMO-002: 词典自动套用的开关配置

- **描述**: 解析 ESP 时「自动套用词典」目前是**默认开启、无开关**的。用户要求后续加一个配置开关（大概率放在解析配置对话框里），让用户能手动控制是否自动套用词典。
- **状态**: 待办，本次不实现（用户明确说「暂时不要实现」）
- **关联**: Translation Memory 应用机制（`_apply_dictionary_to_collection` / 解析链路）
- **备注**: 当前实现：`_run_parse_esp` 与 `_run_batch_parse_esp` 的 `_do()` 里迁移源链末尾无条件调用 `_apply_dictionary_to_collection`，命中数累加进 migrate_count。后续加开关时，需在 `ParseConfigDialog` 增加选项并传入 cfg，由 `_apply_dictionary_to_collection` 按开关决定是否执行。

### MEMO-003: FOMOD 翻译流水线（Epic 2）

- **描述**: 用户原始痛点是「FOMOD 安装包翻译」。词典系统（Epic 1）已做，但 FOMOD 流水线（解包/diff/词条迁移/fomod 文本翻译/剔除侵权资源/打包）尚未开始。用户曾说「epic2 很多地方可以优化，稍后再聊」。
- **状态**: 待办，独立 Epic，未开始
- **关联**: FR15.2~15.7（归档解包/打包、diff、ESP 词条迁移、fomod 文本翻译、输出组装、GUI 面板）
- **备注**: ADR-014 决策 4/5/6 已定归档实现（py7zr+zipfile+rarfile 捆绑 unrar）、fomod 文本处理、侵权规避（扩展名白名单过滤），可作为 Epic 2 的架构基础。
