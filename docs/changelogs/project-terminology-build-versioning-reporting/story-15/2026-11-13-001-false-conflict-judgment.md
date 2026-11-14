# S15：LLM 真假冲突判断

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S15
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 行为与范围

LLM 模式在全局抽取归并之后独立判断译名差异，抽取缓存命中也会重新执行。分类为同一术语正常表达差异、真实冲突、无法确定。正常差异使用已有候选代表术语，原词条译文和完整证据不变；同一插件范围只生成一条术语，其他插件仅有另一正常表达时保留其已有表达，不跨插件编造证据。人工决定不被模型覆盖。

正常差异不计入待处理冲突，默认列表隐藏；“显示 LLM 判定无需处理的差异”和进阶“查看全部冲突判断…”可查看理由、归并译名及原文译文，并使用既有人工审核改判。真实冲突、预算不足、请求失败、缺失证据、非法返回都留待处理。取消不写入新结果。仅规则模式不调用模型。

不增加多义语境术语库、不改变翻译消费契约、不清除抽取缓存。每组覆盖全部少数译名的去重词条，最多三条多数代表样本；并列时覆盖全部。输入完整预算不足则返回不确定，不静默截断。多数样本不能代表穷尽核查，模型误判仍需通过保留的理由及证据人工纠正。

## 文件与实现

- 新增 `application/terminology/conflict_judgment.py`：判断契约、完整性检查、覆盖策略、候选校验与取消异常。
- 新增 `infra/terminology_conflict_judge.py`：复用结构化输出与模型客户端，每组独立容错；数据明确为不可信证据，次数不是正确性投票。
- 新增 `bootstrap/terminology_conflict_judgment.py`：冻结配置下懒创建客户端、进度、取消监控、关闭资源；`terminology_extraction.py` 把判断提示身份与抽取缓存身份分离；`terminology_build.py` 注入生产判断。
- 新增 `application/terminology/conflict_judgment_projection.py`、`equivalent_candidates.py`：记录理由与 equivalent 状态、选择每个范围的普通术语候选；`models.py` 验证已有代表译名与理由；`reducer.py`、`build_drafts.py` 保留人工决定并避免重复术语。
- 修改 `build.py`：判断发生于冻结持久化之前，结果纳入内容身份；取消丢弃当前计算。`incremental.py` 保留组件缓存，但判断启用时不复用整份已判断结果。
- 修改应用及 SQLite `conflict_queries.py`：持久化分页过滤 equivalent，查询身份包含筛选条件；新版本继续读取旧记录，无数据库迁移。新 enum 数据不保证由旧程序版本读取。
- 修改术语 UI 的 `conflicts_view.py`、`window.py`、`table_models.py`、`conflict_evidence_panel.py`、`conflict_review_dialog.py`、`object_views.py`、`extraction_options.py`、`build_projection.py`：默认过滤、全部判断入口、解释及进度展示。
- 修改 `infra/terminology_export.py`：审核主表及明细导出判断说明与归并译名，保留原证据。成品术语仍使用原格式设施。
- 新增模型适配、构建、生产接线与 UI 判断测试；更新导出测试。

## 验证

后端广泛回归命令：

```powershell
uv run --no-sync --cache-dir .tmp-conflict-judgment/uv pytest tests/application/terminology tests/persistence/terminology tests/contracts/terminology tests/integration/bootstrap/test_terminology_conflict_judgment.py tests/integration/bootstrap/test_terminology_llm_build.py tests/integration/bootstrap/test_terminology_build_review.py tests/integration/bootstrap/test_terminology_task_wiring.py tests/infra/test_terminology_conflict_judge.py tests/infra/test_terminology_export.py -q --basetemp .tmp-conflict-judgment/regression --tb=short
```

421 项中 420 通过，新增集成测试使用错误的分页字段名导致 1 项失败；改用既有 `Page.total` 后复验下列生产测试 3 项全部通过，包含新增取消场景。随后补充跨插件候选保留，相关 24 项通过。

```powershell
uv run --no-sync --cache-dir .tmp-conflict-judgment/uv pytest tests/integration/bootstrap/test_terminology_conflict_judgment.py -q --basetemp .tmp-conflict-judgment/final-integration --tb=short
uv run --no-sync --cache-dir .tmp-conflict-judgment/uv pytest tests/application/terminology/test_judged_build.py tests/application/terminology/test_build_drafts.py tests/application/terminology/test_reducer.py -q --basetemp .tmp-conflict-judgment/scopes --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

UI/export 子任务联合回归 56 项通过，追加导航回归 11 项通过；实际 Qt 渲染检查默认过滤、完整判断入口、理由和证据。全仓 Ruff 及 2579 个文件格式检查通过。生产测试使用假模型，验证抽取只请求一次而冲突判断请求两次、失败重试、SQLite 保存/发布及取消保留旧构建。Windows 项目硬链接夹具通过提升权限运行。任务临时目录已清理。

## 验证边界

未调用真实付费模型，未证明真实模型判断质量；未运行全仓 pytest，未打包发布。用户现有结果需重新提取才执行新阶段，抽取成功缓存可继续复用。独立判断当前按组顺序执行，提供进度与取消，不复用上次模型判定。
