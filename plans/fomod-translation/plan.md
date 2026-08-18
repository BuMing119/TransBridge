# fomod-translation（FOMOD 安装包翻译流水线）

**对应需求**: FR15.5-15.7（FOMOD 流水线剩余部分；FR15.2-15.4/15.6 通用能力已由 FR16 复用）
**技术模块**: backend + ui
**业务域**: FOMOD 安装包本地化
**状态**: 已确认
**创建日期**: 2026-08-14

## 功能边界

### 范围内

- fomod_xml.py: ModuleConfig.xml / info.xml 解析与翻译（UTF-16LE 处理、键复用 + AI 翻译新增）
- builder.py: 输出组装编排（复用 fileops/filter_rules.py 过滤 + 目录复制/打包）
- pipeline.py: 流水线编排（解包→diff→键对齐→词典兜底→AI翻译→组装→打包）
- GUI 面板: 向导式 + QThread 后台执行 + 结果摘要

### 范围外

- 通用工具（归档/diff/过滤/键对齐，FR16 已实现，本 Epic 复用）
- TMX 标准格式、语义/模糊匹配、CLI 单独交付
- 写回用户本地已有 ESP（仅产出新中文安装包）

## Story 清单

### Story 01: fomod XML 解析与翻译
**验收标准**:
- [ ] 新建 src/transbridge/fomod/__init__.py + fomod_xml.py
- [ ] 解析 ModuleConfig.xml / info.xml，正确处理 UTF-16LE BOM
- [ ] 按 moduleName/installStep/group/plugin/description 层级键与旧版对齐复用译文
- [ ] 新增/变化且未覆盖的文本走 LLM 翻译（复用 infra/llm_client.py 的 chat()）
- [ ] 写回时保持 UTF-16LE 编码与 BOM

> 详细实现指南见 plans/fomod-translation/stories/story-01-fomod-xml.md

### Story 02: 输出组装
**验收标准**:
- [ ] 新建 src/transbridge/fomod/builder.py
- [ ] 复用 fileops/filter_rules.py 过滤侵权资源
- [ ] 目录复制：保留翻译后的插件 + 脚本 + fomod 元数据
- [ ] 组装产出可打包的目录结构

> 详细实现指南见 plans/fomod-translation/stories/story-02-builder.md

### Story 03: 流水线编排
**验收标准**:
- [ ] 新建 src/transbridge/fomod/pipeline.py
- [ ] 编排流程：解包→diff→逐插件[键对齐→词典兜底→AI翻译→写回]→界面文本翻译→组装→打包
- [ ] 逐插件翻译循环：每个 .esp/.esm/.esl 独立解析、迁移、兜底、翻译、写回
- [ ] AI 兜底复用 AutoTranslator（含术语库匹配 + 名词提取），非裸 LLMClient
- [ ] 运行时上下文注入：llm_config（AI）+ tm_manager（词典），由 GUI 传入
- [ ] 纯 Python 无 PyQt 依赖（ADR-008）

> 详细实现指南见 plans/fomod-translation/stories/story-03-pipeline.md

### Story 04: GUI 面板
**验收标准**:
- [ ] 新建 src/transbridge/ui/tools/fomod/ 向导式面板
- [ ] 4 步向导：选文件→审核变更→翻译→组装输出
- [ ] 后台执行复用 QThread（ApiWorker 模式）+ 进度反馈
- [ ] 结果摘要：迁移统计（继承/需复核/命中/待翻译）+ diff 摘要

> 详细实现指南见 plans/fomod-translation/stories/story-04-gui.md

## 架构依赖

- 引用 ADR: docs/adr/014-fomod-translation-memory.md（fomod 包结构、翻译来源优先级、做法1+键优先级）、docs/adr/015-generic-file-entry-tools.md（fileops/migrator 通用工具）、docs/adr/004-qthread-async-pattern.md（后台线程）
- 依赖的接口契约: fileops.extract/pack、fileops.diff_directories、fileops.filter_files、migrator.migrate、TranslationMemoryManager.apply_to_collection、infra/llm_client.py 的 chat()、writer/plugin_writer.py 的 apply_collection/write
- 复用不改动: FR16 的全部通用工具

## 风险与回退方案

- UTF-16LE XML 处理错误 → fomod_xml.py 显式处理 BOM（不依赖 ElementTree 自动检测）+ 往返一致性测试
- py7zr 大归档解压慢 → 分层提取（仅提取插件 + fomod 目录，跳过 BSA/贴图）
- fomod XML 结构不统一（无官方 schema）→ 解析容错，未知节点忽略

## 综合整改状态增量（2026-08-18）

- `partially-verified`：保留 4 Story 历史交付；必要阶段吞异常、取消后 pack、target_lang/TM/资源保真和原子发布未通过本轮验收。
- `blocked_by`：`fomod-pipeline-v2` S01～S05、`unified-task-translation-runtime-v2` S01/S02、`release-hardening-v2` S02/S04。
- `superseded_by`：旧 pipeline/builder 直接编排与发布流程由 typed stages、ArchivePolicy 和 staging publish 取代；XML/builder 资产作为 adapter 保留。
