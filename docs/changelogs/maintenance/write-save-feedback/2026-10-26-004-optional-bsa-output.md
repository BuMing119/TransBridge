# 可选 BSA 字符串输出，默认生成散装文件

- 日期：2026-10-26
- Epic / Story：maintenance / write-save-feedback（非计划维护）
- 入口：用户要求将 BSA 输出做成可选项，默认不勾选、默认生成散装文件，延续本会话的 bm-pilot 写回改进。

## 行为与边界

单文件及批量本地化写回提供「将 STRINGS 打包为 BSA（默认不勾选）」选项。每次打开新计划默认关闭，不根据来源是否在 BSA 内自动开启；未勾选继续输出散装 STRINGS。批量勾选仅影响带本地化资源的插件，其他来源正常写回。

勾选后将完整字符串表打包为 BSA，与插件一起回读验证、备份、提交和失败恢复，不在正式目录同时输出散装表。切换选项清除覆盖确认及旧预检；需重新预检后执行。输出保留既有插件命名及另存选择，不添加 `_translated`。

打包只包含本次 STRINGS，不合并来源归档的纹理、模型或其他资源，也不覆盖来源依赖的 BSA。目标目录已有同名散装表时阻止 BSA 模式，避免散装内容优先读取而遮住新译文；用户可另存到其他目录。使用现有归档命名规则，基础游戏插件的共享 `Skyrim - Interface.bsa` 输出重名由批量冲突检查报告，不互相覆盖或自动合并。

## 逐文件变更

- 新增 `src/transbridge/application/io/plugin_bsa_output.py`：集中可选模式、散装/BSA 路径、私有目录渲染、现有 sse-bsa 打包、逐成员字节核验、校验用成员物化及原归档/散装冲突保护；未新增依赖。
- 修改 `src/transbridge/application/io/plugin_write.py`：默认路径不变；显式选择 BSA 时在私有目录生成表并打包，返回插件及 BSA 输出，复用现有条目分流和完整表保留。
- 修改 `src/transbridge/application/io/operation_write.py`：预检识别 BSA 模式下会遮盖输出的既有散装文件；归档作为正式输出参与指纹、覆盖和备份检查，保留来源归档保护。
- 修改 `src/transbridge/application/io/publish/plugin_bundle.py`：复用原有整组提交/恢复；packed 模式从归档成员物化后运行相同表级校验，提交前复查散装冲突，拒绝迟到文件。
- 修改 `src/transbridge/application/io/publish/plugin_fidelity.py`：BSA 模式按现有插件资源发现路径回读归档，核验物理位置及跨表 string ID，不依赖残留散装文件。
- 修改 `src/transbridge/ui/operations/write_plan.py`、`write_batch.py`：提供默认 false 的资源输出字段，摘要说明散装或 BSA；目录切换保留用户本次选择。
- 修改 `src/transbridge/ui/operations/write_feature.py`：创建及编辑单文件/批量请求时传递布尔 `pack_bsa`，保留其他写回选项；补充冲突检查的用户标签。
- 修改 `src/transbridge/ui/operations/write_dialog.py`：显示清楚的复选框及仅包含本地化字符串的提示，切换时清除旧确认；保留此前的文件/目录另存入口。
- 新增 `tests/integration/publish/test_optional_bsa_output.py`：22 项真实二进制回归，涵盖默认/显式模式、BSA 来源、基础游戏及资源包名称、跨表同 ID、未选条目保留、归档成员缺失/多余/错误拒绝、原归档保护、迟到散装冲突、覆盖备份和整组恢复。
- 新增 `tests/ui/operations/test_bsa_output_options.py`：5 项真实 Qt/任务回归，涵盖单文件/批量默认不勾选、切回散装、旧确认失效、实际 BSA 输出、混合批量及共享归档输出冲突。
- 修改 `docs/dev/ui.md`：在对应写回说明中记录可选输出、默认值、资源范围及冲突行为。
- 修改 `docs/changelogs/INDEX.md`：仅新增本记录入口；此前增量保持不变。

生产模块均未达到 500 行责任审查阈值。写回 UI、目录另存及其他已有未提交变更按启动时文件副本核对，只将以上 BSA 变化计入本增量。无需工程数据迁移，不修改依赖配置或锁文件，不提交 Git。

## 验证

使用仓库 uv 环境，TEMP、TMP、UV_CACHE_DIR 和 pytest basetemp 指向独有 `.tmp-bsa-7f64/`，结束后清理。首轮默认行为回归 **61 passed**；新增可选输出与 UI 回归最终 **27 passed**。

```powershell
uv run --no-sync pytest tests/integration/publish/test_optional_bsa_output.py tests/ui/operations/test_bsa_output_options.py -q --basetemp .tmp-bsa-7f64/d
```

扩大相关验证：

```powershell
uv run --no-sync pytest tests/integration/publish tests/contracts/io/test_bsa_source_leases.py tests/contracts/io/test_hydrated_write_operation.py tests/contracts/io/test_localized_hydrated_publication.py tests/contracts/io/test_localized_strings_adapter.py tests/contracts/io/test_write_recovery.py tests/contracts/io/test_terminology_profile_write.py tests/ui/operations/test_bsa_output_options.py tests/ui/operations/test_write_save_as.py tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_batch_output_conflicts.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_target_plugin_workflow.py tests/ui/operations/test_production_facade.py tests/ui/operations/test_write_execution_feedback.py tests/smart_assistant/tools/test_safe_write_back.py -q --basetemp .tmp-bsa-7f64/r
```

结果：**351 passed、1 skipped**，92.79 秒，退出码 0。跳过项为现有平台能力相关用例；新增用例已包含在该矩阵中，不累计重复计数。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
```

全库静态检查及 2192 个文件格式检查通过。真实 Qt 离屏渲染核对单文件、批量窗口，确认复选框可见且初始未勾选，保留另存/恢复原位置及覆盖确认。收尾执行本次文件的 `git diff --check`。

## 遗留与未验证项

本次功能无未完成项。未运行全仓库 pytest、真实用户归档写回、游戏内加载验收、网络/LLM 请求或安装包构建；未操作用户游戏数据。BSA 输出仅包含字符串资源，不声称是含其他资源的原归档完整替代包。
