# Story 08：端到端故障演练、兼容与发布门禁

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17 全部验收场景；FR22.2～FR22.8；FR5.16 S12
- **前置依赖**：S00～S07 全部完成
- **产出**：自动化门禁、性能manifest、QA报告和发行判定

## 目标

用受控HTTP服务、真实项目隔离SQLite、TaskRuntime和GUI/Agent/MCP adapter贯通FR5.17完整成功链与故障链，证明默认关闭、三方差异、双向入站、AI运行固定、partial/reconcile/retry和多入口等价。该Story不以普通mock单元测试代替端到端证据，也不绕过FR5.16尚未通过的正式基座门禁。

## 原始验收标准

- FR5.17 的十个验收场景均有自动化集成测试或明确的受控服务证据，并可追溯到 plan item/outcome/baseline/run spec digest。
- 在常规大术语库上，远端分页读取、plan 生成/分页展示、SQLite 写入和 UI 投影使用有界内存；取消后 500ms 内出现反馈且不再调度新网络请求。
- 认证失效、权限不足、限流、网络中断、取消、部分成功、timeout-after-commit、数据库故障、目标/binding/revision 变化均不会改变 current effective version，也不会重复已确认副作用。
- 默认关闭、未绑定、无网络、无已发布版本和旧 Project/terminology SQLite 的现有本地术语与 AI 流程通过兼容回归，且无不必要网络请求。
- FR5.16 基座正式发布证据通过后，才可把 FR5.17 标记为发行完成；若基座 S12 仍失败，FR5.17 可保持功能实现但发行门禁必须为 OFF。

## 验收场景到证据的映射

每个场景都必须保存 test node ID、fixture seed、local version/digest、remote snapshot digest、baseline revision、plan hash、run ID和最终outcome摘要：

1. 默认关闭：open Project、switch Variant、start AI均无term write request，legacy结果不变。
2. 重复备份：第二次plan全skip/echo，remote write调用为零；再次拉取无inbound候选。
3. 真实remote修改：双向plan识别remote changed，导入/发布前effective digest不变。
4. AI运行一致：run spec创建后publish/restore，任务全部batch仍使用旧snapshot，新run用新version。
5. plugin特例：lossy item可见且payload为空，不影响另一plugin。
6. 多Variant：同target第二line在write前blocked，需要明确mapping替换。
7. suppression/delete：remote旧副本、重建、同步、legacy fallback均不复活。
8. 独立remote：backup不删除/覆盖；预计managed delete逐项可见并确认。
9. 认证/权限/限流/断线/取消/partial：两端状态可判定，retry不重复confirmed副作用。
10. target/binding/account/endpoint/remote revision变化：旧plan stale，不能提交到新/过期target。

## 受控测试基础设施

- `ControlledParaTranzTermsServer`：真实HTTP socket或requests adapter，支持分页、revision/ETag能力开关、request log、可编排error/latency/timeout-after-commit和remote mutation。
- `TerminologySyncScenarioBuilder`：固定seed生成local versions、baseline/item links、remote independent/managed terms、plugin scope和Variant lines。
- `TerminologySyncEvidenceManifest`：记录环境、seed、counts/digests、request次数、timing/RSS、outcome和门禁结论。
- `NoNetworkSpy`：默认关闭/未绑定/no version流程一旦访问terms endpoint立即失败。
- fault injection ports：SQLite begin/commit/full/read-only/CAS、TaskRuntime cancellation/late result、target resolver revision变化。

测试fixture不得依赖真实用户translation data或提交大二进制数据库；用确定seed按需生成。

## 性能与资源规程

- 固定常规规模和压力规模，包括术语数、分页数、baseline比例、remote independent比例、冲突/删除/lossy比例和UI page size。
- 分阶段测量remote fetch、mapping/planning、state read/write、execute/reconcile、inbound freeze/query和UI projection；网络等待单独计时。
- 记录峰值RSS和完成后稳定RSS；plan/items和response page应流式/分页，不要求UI或Agent物化全部rows。
- 取消测试在fetch、planner、remote write、SQLite commit前后注入，测量从用户请求到可见CANCELLING/停止新调度不超过500ms。
- 墙钟预算若FR5.17未单独定义，不自行发明发布阈值；报告实际基线与回归阈值。FR5.16相关预算继续引用其正式runner和S12结论。

## 依赖有序的实施步骤

1. 新建integration/performance目录和共享controlled server，复用现有`tests/contracts/paratranz`的typed error/cancel fixture模式。
2. 建立十个acceptance scenario的fixture builder和manifest schema；每个测试断言plan/outcome/baseline/run spec完整追溯链。
3. 跑纯backup成功链：first create/update、重复no-op、managed delete、independent remote和plugin lossy。
4. 跑bidirectional成功链：remote add/update/delete→immutable change set→draft import→显式publish→new AI run；每个边界检查effective digest。
5. 跑AI并发一致性：translation/polish/mixed/custom在barrier停住，中途publish/restore/sync/Variant switch，再释放后比较每批terminology snapshot identity和prompt term结果。
6. 编排认证、授权、404、409、429、5xx、timeout-before/after-commit、分页漂移、target/binding/account变化、cancel和late response；验证unknown/reconcile/retry。
7. 注入SQLite migration/corrupt/future/read-only/full/CAS conflict和draft成功/disposition失败；验证fail closed和历史不丢。
8. 对GUI/Agent/MCP运行相同scenario，比较plan hash/counts/action reasons、confirmation/stale和result；UI额外验证分页/responsiveness/accessibility。
9. 建立no-network兼容suite覆盖未启用/未绑定/no version/legacy sources/旧Project和v2 terminology DB。
10. 运行常规/压力profiles，输出manifest和人类可读摘要；不得手工挑选最快一次或把外部等待混入CPU预算。
11. 汇总FR5.16基座S12最新证据。若仍失败，QA报告把FR5.17功能门禁和发行门禁分开，发行状态保持OFF。
12. 运行聚焦、扩大回归、Ruff/format/diff-check，并记录未运行live smoke及原因；只有全部required gate通过才更新Plan状态。

## 文件变更清单

- **新增** `tests/integration/terminology_sync/`：acceptance、fault、entrypoint parity、publish boundary和AI consistency。
- **新增** `tests/integration/terminology_sync/controlled_server.py`、`scenario_builder.py`、`evidence.py`。
- **新增** `tests/performance/terminology_sync/`：dataset、measure、manifest和slow tests。
- **可能新增** `scripts/benchmark_terminology_sync.py`：参考设备runner，复用FR5.16 measure约定。
- **新增** `docs/test-reports/project-terminology-paratranz-sync-qa-<date>.md`。
- **更新** `plans/project-terminology-paratranz-sync/plan.md`和`plans/INDEX.md`：只在证据真实通过后更新状态。

## 边界条件与错误处理

- 受控server成功不代表live API合同已验证；S00 live证据缺失要在报告中单列，但离线CI仍必须完整。
- live smoke只使用专用测试项目；清理仅依据本次run的confirmed managed remote IDs，unknown item不删除。
- 性能失败不通过删除断言、缩小正式dataset或提高未确认预算解决；记录瓶颈并回到相应Story修复。
- FR5.16 S12失败时不能把FR5.17标记“发行完成”，即使FR5.17自身测试全绿。
- 测试产生的临时Project/SQLite/log/HTTP证据放在pytest tmp_path或任务专用目录，结束后只清理本任务创建项。
- secret canary扫描所有manifest、diagnostic、Tool/MCP output和QA报告。

## 测试策略与建议命令

- 聚焦：`uv run pytest tests/application/terminology_sync tests/persistence/terminology/test_sync_state.py tests/paratranz/test_terms_service.py -q`。
- 合同/集成：`uv run pytest tests/contracts/paratranz/test_terms_api_contract.py tests/contracts/terminology_sync tests/integration/terminology_sync -q`。
- AI/UI/Agent：`uv run pytest tests/ai_translator tests/application/translation tests/ui/tools/ai_translator tests/ui/tools/terminology tests/smart_assistant/tools -q`的相关筛选集。
- 性能：`uv run pytest tests/performance/terminology_sync -m slow -q`，参考设备再运行benchmark脚本。
- FR5.16基座：执行其Plan S12列出的formal runner/release gate命令并引用最新bundle，不复用过期结果。
- 静态：`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`、`git diff --check`。

## 风险、回退与未决问题

- timeout-after-commit、remote ID重用和分页漂移是最重要的真实故障；测试必须控制服务端状态，不可只让mock抛异常。
- 完整AI/UI/performance矩阵耗时较长，应分聚焦CI、nightly/reference-device和可选live smoke三层，但required正确性场景不能只在live环境。
- 回退发布时关闭sync capability并保留baseline/outcome/change set只读；旧本地effective术语和legacy流程仍需通过兼容suite。
