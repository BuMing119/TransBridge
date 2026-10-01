# ParaTranz 八位上下文顺序

- 日期：2026-10-01
- 状态：Story 01～04 本地实现与验证完成；真实远端与网页排序未验证。
- 需求：[FR22.9～FR22.11 八位上下文序号、回读与远端刷新](../../docs/requirements.md#fr22-context-order)；来源更新见 [FR19.14](../../docs/requirements.md#fr19-source-update)。
- 依据：本会话需求、ADR-017、既有 dialogue-tree-order 计划。

## 目标与边界

ParaTranz 导出将结构化上下文投影为 `00000428|INFO:NAM1|00123456` 或 `00000431|BOOK:FULL`。
保持 key、原文、译文、状态、本地 context 和插件元数据语义；不添加任务说明等展示噪声。
这提供按上下文文本排序的稳定排序键，不承诺 ParaTranz 网页支持或自动选择这种排序。
本次不上传真实项目、不修改远端文件、不改浏览器或 Qt 编辑器。

## 当前事实与契约

- 插件 `plugin.source_order` 为零基；展示直接沿用，不加一，允许间隔。
- 宽度固定八位，合法范围 0～99,999,999；超限须明确失败，不能截断或生成九位数。
- 分类上传复用 `translation_entry_collection_export.py`，应在分类之前确定回退顺序，不能逐文件重编号。
- ParaTranz adapter 是统一写入边界；原生第三方 DTO 的无修改读写继续保真。
- 仅识别严格的八位前缀与结构化类型字段，不移除任意自由文本中的数字前缀。
- 回读在转换为本地词条时移除展示前缀，将顺序保存在单独的 ParaTranz metadata 中；不伪造插件来源。
- 双向同步需归一化上下文比较，保留远端并发检测证据；翻译更新不能污染本地 context。
- 完整、有效、唯一的源序号优先；缺失/重复/混合数据采用确定性回退，且与既有分类输出顺序一致。

## Story 01：导出与上传上下文投影

- 新增独立 `application/io/paratranz_context_order.py`，承载格式、范围检查及序号选择，避免把新职责放进大类。
- 接入 `application/io/paratranz.py` 与分类导出；上传复用同一路径。
- 普通第三方自由文本上下文不变；重复导出不叠加前缀。
- 测试源序号间隔、分类全局编号、筛选、缺失/重复序号、边界溢出以及本地不变性。

## Story 02：回读与同步兼容

- 接入 `paratranz_mapping.py` 的本地投影和 `paratranz/sync_snapshot.py` 的同步快照。
- 已带前缀数据经读取、工程投影、序列化及重新导出保持顺序信息。
- 测试本地类型/任务分类仍正常、旧无前缀数据兼容、第三方任意文本不被误拆、同步不产生仅由前缀引起的内容差异。
- 核查远端内容更新和 revision 校验，必要时在同一同步边界补齐。

## 执行与验证

1. 分析：两子 Agent 分别审查导出与回读调用链；主会话统一契约。（已完成）
2. 规划：主会话维护本计划与索引。（已完成）
3. 开发：两子 Agent 按文件互斥实现两个 Story；第三子 Agent 补齐同步执行和重试校验。（已完成）
4. QA：子 Agent 审查与聚焦验证，主会话执行联合回归与全库 Ruff。（已完成）
5. 记录：按实际差异和测试证据生成[增量记录](../../docs/changelogs/paratranz-context-order/story-01-02/2026-10-01-001-context-order.md)。（已完成）

优先运行新增 contract tests，再运行既有 ParaTranz、转换与同步测试。
执行 `uv run ruff check src tests` 和 `uv run ruff format --check src tests`；不把无关既有工作树问题混入修复。

## 兼容与迁移

无工程 schema 迁移；新格式影响之后的 ParaTranz 输出。已有远端词条可通过上传/双向同步的仅序号更新刷新上下文，也可随原文文件更新输出新上下文。
旧文件可继续读取；不通过重命名 key 或删除重建远端文件实现排序。
工作区已有助手 UI/压缩相关改动与本任务无关，保留并排除于本次增量归属。

仅序号变化不视为译文内容变化，但上传/双向同步应生成 `context_order_changed` 更新项，经既有确认后仅发送 context 字段；纯下载不修改远端。计划/重试证据包含序号，序号变化后需重新规划。
完整且唯一的源序号直接保留；混合/重复数据回退时，分类导出先保持既有任务组恢复顺序，再复用这些词条在完整集合中的槽位分配全局序号。

## 责任审查与验证结果

### Story 03：远端仅序号更新（已完成）

- 仅在规范化的原文/译文/类型上下文/状态相同、结构化上下文可投影且本地序号已知时，检测远端旧序号或缺少序号。
- UPLOAD/BIDIRECTIONAL 生成既有 UPDATE_REMOTE 动作，reason 明确为 `context_order_changed`，不绕过确认、远端身份和快照/重试检查；DOWNLOAD 保持无远端写入。
- 扩展 ParaTranzPort/Service 的独立 `update_entry_context` 边界，只提交 `{"context": ...}`，不发送 key、原文、译文、状态，不创建词条或扫描匹配。
- 测试字段隔离、远端 ID/响应验证、旧无序号、同序幂等、自由文本不变、纯下载、缺 ID、过期及重试。内容也变化时保留既有冲突策略，不能借改序覆盖远端译文。

### Story 04：单文件上传入口闭环（已完成）

- `upload_collection_as_single` 经正式 ParaTranz adapter 生成一次完整 wire records，再递归拆分；不再直接上传 collection 内部 JSON。
- 递归 413 拆分仍沿用完整集合编号，分类/单文件模式的字段身份契约一致。
- 测试已有文件四种模式、拆分、缺文件仅译文跳过、超限前失败与本地不变性。

阶段：分析、计划、开发、独立 QA 与[Story 03～04 增量记录](../../docs/changelogs/paratranz-context-order/story-03-04/2026-10-01-001-remote-order-refresh.md)已完成。

### Story 03～04 验证与使用流程

- 使用[更新源文件](../source-file-update/plan.md)替换工程中的来源并迁移全部版本，使当前解析词条的顺序反映新版；仅替换磁盘文件不等于已更新工程。
- 发起上传或双向同步预检：相同 key 且内容相同的词条，若序号不同或远端没有序号，列入远端更新。
- 按既有流程确认后只提交 context；key、原文、译文与审核状态不在该请求中。再次预检相同数据应跳过。
- 原文/译文/状态同时变化仍走既有冲突策略；纯下载不刷新远端。单文件与分类输出均携带八位序号。
- 解析所得词条携带顺序元数据，context 保持结构化原值；正式 Variant 不保存原文或该顺序字段，重开时从登记来源重新解析并叠加所选版本状态。无需工程 schema 迁移或远端删词重建。
- 新增 31 项回归全部通过；联合回归 266 passed、1 skipped、1 项此前已用 HEAD 复现的既有失败，详见增量记录。
- 全库 Ruff check 与 format --check（1422 文件）通过；git diff --check 通过。未运行全仓 pytest、真实服务写入或网页排序验收。
- executor 在原有同步执行职责内增加 context-only 分支，无新职责；新增字段隔离接口位于既有 service/port 边界，单文件复用独立 adapter。保持既有大模块责任审查结论，不夹带重构。

### Story 01～02 验证记录

- 格式识别、范围限制及序号选择独立在 `paratranz_context_order.py`；现有 adapter 513 行、executor 611 行仅增加原有写入/同步职责内的调用接线。
- `tool_paratranz.py` 既有模块超过 700 行，本次仅在既有 snapshot 构造函数补 4 行调用，不添加新职责；直接上传职责继续位于独立 `_entry_upload.py`。为避免本次格式变更夹带大规模工具拆分，暂保留；后续新增 ParaTranz 工具能力前应先按查询/同步/上传职责拆分并验证注册兼容。
- 最终聚焦复验：80 passed，包含新增 37 项回归以及同步执行/确认测试。
- 联合回归：385 passed、1 skipped、1 failed；失败 `test_assistant_upload_reuses_only_the_target_projects_known_identity[8]` 已用 HEAD 上传函数在内存替换复现，属于原有矛盾断言，本次不改变重复 key 返回失败的行为。
- 全库 `uv run ruff check src tests`、`uv run ruff format --check src tests` 通过；`git diff --check` 通过。
- 未执行真实远端写入与网页排序验证；不声称此格式能控制 ParaTranz 网页默认排序。
