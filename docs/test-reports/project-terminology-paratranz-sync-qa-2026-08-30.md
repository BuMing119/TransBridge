# FR5.17 项目术语库与 ParaTranz 同步 QA 报告

- **日期**：2026-08-30
- **范围**：`project-terminology-paratranz-sync` S00～S08
- **功能结论**：实现完成，离线 QA 通过
- **发行结论**：**OFF**

## 已验证能力

- typed ParaTranz 术语端口、受控合同 fixture、分页与错误归一化。
- Project/Variant 隔离的同步 profile、baseline、remote link、run/outcome、入站复核与 SQLite v3 迁移。
- 基于不可变已发布版本的三方计划、fresh-check、显式确认、受管删除、取消、确定性重试和 `UNKNOWN` 对账。
- 双向同步中的远端读取、入站 change set 与同步状态原子提交；故障注入已验证事务回滚。
- 入站新增/修改/删除的分页复核、接受/拒绝/编辑、draft preview 和仅提交到 draft 的发布边界。
- GUI、Agent 与 MCP 复用同一 application use case；通用 bootstrap 在无 Qt 导入的干净进程中保持 headless。
- 翻译、润色、混合与自定义 AI 任务固定不可变术语快照；运行中版本切换不会改变已启动任务，并按精确远端身份抑制回声。
- remote ID 缺失/重用、target/binding/mapping 漂移、晚到取消、重复入站事实和跨 Variant 隔离均有回归覆盖。

## 验证证据

### FR5.17 聚焦套件

```text
uv run pytest tests/application/terminology_sync tests/application/translation/test_terminology_run_snapshot.py tests/contracts/paratranz/test_terms_api_contract.py tests/contracts/terminology_sync tests/paratranz/test_terms_service.py tests/persistence/terminology tests/integration/terminology_sync tests/performance/terminology_sync tests/smart_assistant/tools/test_terminology_sync_tools.py tests/ui/tools/terminology -q

198 passed, 1 skipped, 3 warnings
```

跳过项是可选 live ParaTranz 合同测试；警告为既有 PyQt SWIG 弃用警告。

### 相关回归

```text
uv run pytest tests/persistence/terminology tests/ai_translator tests/ui/tools/terminology tests/ui/tools/test_ai_translator_story08.py tests/ui/tools/test_ai_translator_slices.py tests/ui/characterization/test_ai_translator_contract.py tests/integration/bootstrap tests/integration/terminology tests/application/terminology -q

557 passed, 3 warnings
```

### 静态与差异检查

```text
ruff check src tests
All checks passed!

ruff format --check src tests
1052 files already formatted

git diff --check
passed
```

仓库的 `scripts/` 目录仍有与本功能无关的历史 Ruff 问题；本次没有修改或批量格式化这些一次性脚本。

## 发行门禁与剩余外部证据

正式发行门禁维持 **OFF**，原因如下：

1. 本轮没有可用的专用 ParaTranz 测试项目凭据，因此未执行可选 live 合同 smoke，也没有生成新的脱敏真实响应样本。受控 fixture 与 HTTP 测试只能证明离线合同和故障行为。
2. FR5.17 依赖的 FR5.16 S12 正式性能门禁仍未通过；当前基座报告为 `docs/test-reports/terminology-benchmarks/2026-08-28-release-candidate.md`。
3. S08 的性能与 HTTP evidence manifest 明确标记为 diagnostic-only，校验器会拒绝将其当作正式发行声明。

解除门禁需要在显式授权的专用测试项目中完成 live 合同采样，并引用一份通过的 FR5.16 S12 正式性能证据；在此之前不得宣称 FR5.17 release-ready。

## 兼容与迁移

- terminology SQLite schema 升级到 v3；现有数据库沿用 backup-first 迁移与失败恢复路径。
- AI 运行档案增加不可变术语 snapshot 引用和摘要，属于向后兼容的附加字段；旧运行数据仍可读取。
- 不增加后台、定时或 Project/Variant 切换触发的自动同步。ParaTranz 写操作始终来自用户显式选择、计划确认和执行。
- 入站远端变化只提交到现有 draft 边界，不直接改写 effective/published version。
