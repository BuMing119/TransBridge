# 项目自包含保存实施计划

- **状态**：已确认；S01～S03 本地实现与相关 QA 完成；S04 自动恢复扩展本地实现与相关 QA 完成（2026-11-03）。
- **日期**：2026-11-03
- **需求**：[FR8.3～FR8.4、FR8.8、FR19.7、FR19.10～FR19.11、FR19.14.5](../../docs/requirements.md)。
- **架构**：[ADR-051](../../docs/adr/051-self-contained-project-storage.md)，复用 ADR-018、ADR-035 的持久化边界。
- **Feature slug**：`project-self-contained-storage`。

## 目标与范围

“完整保存项目”意味着项目正式存储拥有重新打开、编辑和输出所需的原文、译文、Stage、标签、上下文、类型、持久来源顺序、输出模板及实际使用的 Strings 内容；原始路径只是可空参考。复用现有来源 artifact、Project/Variant repository 和 journal，不另建存储体系。

任务日志、AI 会话历史、任务成果与本机缓存不属于项目自包含的必要内容。保留换源备份时，包内必须同时带走它引用的旧 artifact 闭包。不更改真实用户 data，不运行批量迁移，不新增每次保存 hash 扫描、通用校验平台或运行期旧格式回退。

## 当前实现事实与关键契约

- 正常新建来源已有 artifact，保存解析成果、源文件 bytes 与实际 Strings；缺口在保存成功语义、旧结构运行分支及包内依赖范围，不需要把全部原文字段重复塞进 Variant。
- 现有 `SourceRegistration.source_artifact_id` 保持正式字段。schema 5 新增必需 `content_state`：`captured` 必须有 artifact ID；`missing` 不带 artifact ID。它描述持久捕获状态，不声明磁盘内容永久健康。
- schema 4 到 5 只由现有 `v2/migration.py` 升级链转换；旧引用保留，没有引用则标记 missing。S04 在现有打开/升级边界补充缺内容修复，覆盖刚升级的旧项目与已为 schema 5 的 missing 项目；运行时只接受新结构，不恢复外部 baseline loader 兜底。
- 已存原文和已存模板是不同能力。缺内容时按已保存内容/快照、项目所属备份、明确登记原路径的顺序尝试恢复，复用现有来源补齐匹配与原子备份发布；不能补造空字符串、local key 或译文。正常完整项目不扫描历史、不读取外部源文件。S04 修复失败停止正常打开并保留原件与备份，不删除项目。
- 保存 Project/Variant 成功但来源内容缺失时返回部分保存，清楚说明译文已保存、完整项目未保存；clean no-op 同样不得假报完整。普通编辑保存不重新扫描、hash 或重写全部 artifact。

## Story 01：规范来源状态与单一自动升级入口

**状态**：本地实现与相关 QA 完成。

**验收**：新写入统一 schema 5；当前来源读路径仅消费规范字段。v4 引用完整保留，缺引用标记 missing；旧记录经只读发现时不写盘，正式激活复用备份和原子升级。未来 schema、坏引用和损坏记录沿用已有保护，不访问外部原路径，不猜测原文。

**文件与步骤**：修改 `src/transbridge/persistence/v2/{models,schema,migration}.py`、`src/transbridge/application/projects/source_registry.py`、`src/transbridge/persistence/{project_source_inputs,saved_project_sources}.py`；先定义一致字段和迁移，再删除运行 fallback。升级缺引用时直接标记 missing，不新增备份搜索或恢复入口。

**验证**：schema、migration、repository、项目发现/打开测试；覆盖旧引用、无引用、未来版本、坏内容、外部原文件被替换以及缺来源仍保留已存条目。

## Story 02：来源闭包与保存结果一致

**状态**：本地实现与相关 QA 完成；依赖 S01。

**验收**：完整来源保存并关闭后，删除/移动/替换原路径仍能还原全部条目和输出。localized 插件引用的非零字符串 ID 无法解析时明确失败，不静默丢条目；只捕获实际使用的 Strings。来源缺失时编辑可持久化，返回部分保存且 UI 不显示完整“已保存”；clean 分支相同。保存失败和并发编辑保持原 journal/revision 语义。

**文件与步骤**：`src/transbridge/application/projects/lifecycle.py` 与现有保存呈现/反馈入口；`src/transbridge/parser/plugin_parser.py`、插件 localized 解析与输入捕获模块。来源添加/补齐/更新时捕获闭包，普通 save 使用已知来源可用性事实，不重建 artifact。

**验证**：项目生命周期及 UI 保存反馈，正常/clean/dirty/missing/损坏输入矩阵；合成 localized 插件与 loose/BSA Strings 场景，断开原始输入后的重开、条目保真和写回测试。

## Story 03：可移植包与换源备份闭包

**状态**：本地实现与相关 QA 完成；依赖 S01，S02 提供端到端验收输入。

**验收**：导出只包含正式项目/版本、必要来源 artifact、项目自有正式数据和保留的恢复备份；任务日志、AI 历史和缓存不因目录递归被夹带。保留换源备份时，旧 Project/Variant 引用的 artifact 一并进入包；历史 preimage 导入项目自有目录，只读保留，不激活旧 journal，也不新增一键恢复接口。缺失已引用依赖明确失败，不发布伪完整包。独立存储根导入后无需原路径或原机器缓存。

**文件与步骤**：`src/transbridge/persistence/{project_archive,project_archive_files,source_artifact_archive}.py` 和新增职责切片 `source_update_archive.py`；用已有归档成员规则与 artifact 依赖遍历补齐范围，复用现有包校验/事务。不扩大为任意历史恢复框架。

**验证**：归档 round trip、换源前后旧 artifact 闭包、缺依赖失败、日志/AI 历史排除、独立 store 下条目逐字段一致及输出模板可用。

## Story 04：在既有升级边界自动恢复缺失来源

**状态**：本地实现与相关 QA 完成；依赖 S01～S03。S01～S03 的已完成描述保留为阶段事实，本 Story 扩展其缺内容恢复边界。

**验收**：

- 仅在打开/激活准备发现必需来源内容缺失时进入恢复；schema 4 升级产生 missing 和已有 schema 5 missing 都必须覆盖。正常完整项目沿用当前读取，不扫描历史、不解析外部参考，目录只读发现不触发修复或写盘。
- 优先使用项目已保存内容与已有快照，再尝试项目所属备份，最后尝试来源明确登记的原路径。同存储其他工程的明确登记路径只有记录了相同内容指纹才可作为已知来源引用候选。仅使用能证明来源和条目对应关系的候选；不全盘搜索、不按文件名猜配，不恢复已移除来源。较高优先级候选能补齐后即停止后续查找。
- 已存真实原文可以恢复文字信息，但不能冒充缺失模板或 Strings。只有实际可用内容满足相应用途后才标记恢复成功；缺失信息不得从译文、key、占位提示或任意空字符串生成。
- 成功恢复的内容写入正式内部 artifact，复用现有补齐匹配、备份、revision 与原子发布；保留全部正式版本的译文、Stage、标签、引用和未匹配条目，不把补缺当作来源更新删除旧词条。再次打开不再依赖恢复候选或外部原路径。
- 候选不可读、无法可靠匹配或全部不足时给出明确缺失原因，停止正常激活，保留原项目与备份，不直接删除。发布失败同样保留可恢复现场，不更新活动工程指针。
- 沿用已有 worker/coordinator 执行文件读取和解析；不新增平行升级系统、通用修复平台、非必要完整性门禁或日常全量 hash 扫描。

**文件与步骤**：

1. 在 `src/transbridge/persistence/current_project.py` 与 `project_lifecycle_loader.py` 的打开准备接入 `ProjectContentMigration`；纯 schema 转换继续由 `v2/migration.py` 承担。`bootstrap/persistence.py` 注入已有来源准备、artifact 与来源更新事务存储。
2. 新增 `project_content_candidates.py` 负责固定存储根内的成果、真实 Variant 快照、所属备份及明确登记引用读取，同一次修复缓存候选，多个缺来源不重复全表枚举。快照仅贡献真实身份/指纹，不能作为模板。已知指纹不一致拒绝；旧项目无指纹时要求稳定 namespace/明确原路径和全部存活条目唯一匹配，多候选版本歧义拒绝。
3. 新增 `project_content_migration.py` 复用 `application/projects/source_supplement_mapping.py` 和已有来源更新事务存储，把全部来源与正式版本一次备份、一次原子提交；补齐映射允许唯一主来源的 legacy namespace，当前译文状态优先于候选内容。折叠译文来源亦恢复其保存成果，不增加独立权威 namespace。
4. `saved_project_sources.py` 增加恢复专用捕获入口；`source_artifact_store.py` 仅在该入口允许先保全损坏 manifest/blob 原字节，再原子恢复正确的内容寻址记录，普通 put/save 保持原规则。
5. 在打开入口返回既有结构化诊断，确保修复失败不进入正常工作台。文案说明已尝试恢复、仍缺什么以及原件/备份已保留；取消请求立即停止，不继续尝试下一个候选；`open_path(dirty_decision=CANCEL)` 在读取与修复前返回。`prepare_path` 是打开准备，可持久化修复但不改变当前 GUI；普通 UI 在调用前完成保存/放弃/取消决策，不将该 API 当作无写盘预览。

**验证**：合成隔离存储覆盖 schema 4 与 schema 5 missing、保存内容/快照优先级、所属备份、明确原路径、候选失败继续尝试、全部候选不足、不能匹配的条目保留、多版本译文状态、原子写失败、活动指针不变、修复后删除候选仍可重开，以及完整项目禁止历史/外源读取。聚焦 persistence/application 测试后运行相关打开与迁移回归，再运行全库 Ruff 与格式检查。不操作真实用户 data。最终聚焦 50 项通过，含 22 项自动恢复专项与显式取消；广域 1158 通过、4 失败、1 跳过，其中一次权限失败单独复跑通过，另外 3 项为既有报告范围断言及硬链接环境失败。全库 Ruff 与 2349 文件格式检查通过；没有宣称广域全绿。命令、归因及未执行边界见 [S04 增量](../../docs/changelogs/project-self-contained-storage/story-04/2026-11-03-001-automatic-source-content-repair.md)。

## 依赖、风险与交付门槛

S01 → S02/S03 → S04 → 联合 QA。已有内容、快照、所属备份和明确原路径均无法提供缺失信息时，停止正常打开并保留原件/备份；不承诺恢复不存在的内容。schema 升级与来源修复沿用原字节备份和原子发布，失败保留现场；代码回退不得删除 artifact 或降级覆盖新文档。

字段和行为调整应保持已有无关工作区差异；`docs/requirements.md` 的 FR3.4 和 `plans/INDEX.md` 的分类上传变更不属于本任务。验证先跑聚焦 pytest，再相关集成回归及 `uv run ruff check src tests`、`uv run ruff format --check src tests`。S01～S03 的最终稳定单次联合回归 999 通过、1 项 Windows 符号链接权限跳过；全库 Ruff 和 2345 文件格式检查通过。新来源缺非零 Strings ID、原路径删除后的保存/写回、缺输入部分保存与导航、历史依赖跨存储根归档均有回归覆盖。未运行真实用户 data 迁移或人工桌面/真实网络验收。命令、逐文件变更与边界见[增量记录](../../docs/changelogs/project-self-contained-storage/story-01-to-03/2026-11-03-001-self-contained-project-storage.md)。
