# SST 完整读写与软件流程接入

- 日期：2026-11-08
- Epic/Story：[sst-integration / Story 01–04](../../../../plans/sst-integration/plan.md)
- 需求：FR1.9、FR18.8；[ADR-052](../../../adr/052-sst-io-integration.md)

## 范围与动机

将先前 xTranslator 源码复原实验接入生产。旧解析按偏移扫描会漏掉物理记录，旧身份不能安全写回。
新实现保留完整来源、逐条身份与全部格式字段，原 SST 写回和插件生成 SSU9 共用预检与原子发布。
能力维持实验性，不宣称全面上游兼容；用户已手动反馈真实文件修改版可用。

本记录只归属 SST 相关差异。工作区原有 JSON/XML 来源统一、项目创建、拖放、分类上传确认、
`.transbridge/` 和其他增量不计入本次。原始样本未修改；手动验收文件保留在被忽略的 `out/sst-lab-20261108/`。

## 文件与变化

- 新增 `src/transbridge/parser/xt/sst_codec.py`：SSU8/SSU9 顺序布局、int32 字节长度、UTF-16 code units、
  masters/labels/完整记录及上游低字节 FNV-1a。此前独立 `scripts/sst_lab_codec.py` 迁入此处并删除重复实现。
- 重写 `parser/xt/sst_parser.py`、`sst_serializer.py`，更新 `__init__.py`：保留公开 facade，暴露完整 document、
  record、ordinal；精确更新与默认防覆盖保存，拒绝损坏及多义更新。
- 新增 `converter/sst_entries.py`、`sst_matching.py`；`translation_entry.py`、`translation_entry_collection.py`
  改为委托独立职责，删除旧错位映射。物理记录全部保留；迁移要求 FormID、字段类型、原文唯一匹配，
  INFO/DIAL 从稳定 dsd_type 或上下文前缀提取类型，不把任务上下文拼入字段身份。
- 新增 `application/io/sst_adapter.py`；`import_adapters.py` 保留兼容别名、移除旧 SST 实现；`catalog.py`
  登记实验性写、往返、发布能力。来源摘要、codec v2、ordinal、签名、原文共同校验，旧身份拒绝猜测。
- 新增 `application/io/sst_export.py`：保存插件快照生成 SSU9，依据真实 EDID/fallback、masters、StringID、
  文本列表和物理字段索引；拒绝 VMAD、未知字段、删除记录及非 ASCII 主文件名。支持源文件移走后生成。
- 新增 `application/io/write_formats.py`、`publish/sst_fidelity.py`；更新 `operation_write.py`、
  `publish/validators.py`、`publish/coordinator.py`：输出格式与来源格式分离、SST 单产物、完整回读比较、
  staging 文件名校验、退休解析器重建及取消结果归一。
- `application/io/migration_import.py`：使用捕获内容和真实记录类型进行 SST 迁移，删除旧 index 键逻辑。
- `smart_assistant/tools/tool_parser.py`、`tool_writer.py`、`write_back_preparation.py`：SST 解析保存完整快照，
  `target=sst` 支持原词典及插件转换，输出目录采用保存的 display_name，要求显式转换目标并保护原插件。
- `ui/operations/plan_dialog.py` 支持已有 CHOICE 契约；`write_plan.py`、`write_batch.py`、`write_feature.py`、
  `write_dialog.py` 增加单个/批量输出格式，切换清除过期目标与覆盖确认。批量原 SSU8 不升级，混合 XML 不转换。
  `ui/workbench/cards/write_card.py` 更新可见说明。
- `scripts/sst_lab.py` 与格式测试共用生产 codec。新增/更新 `tests/parser/test_sst_*`、
  `tests/contracts/io/test_sst_*`、迁移/目录测试、`tests/ui/operations/test_write_batch_sst.py`、
  `test_write_save_as.py`、助手写回测试；`tests/source_format_fixtures.py` 仅 SST 布局改为真实 wire。
  能力矩阵测试移除硬编码永久关闭，跟随真实 catalog；正式 MCP 文件读写仍不支持。
- 更新 `docs/requirements.md` FR1.9/FR18.8、ADR-017 相关策略，新增 ADR-052；更新 `docs/dev/parser.md`、
  `sst-experiment.md`、工具说明草稿；纠正 file-parsing Story09–11、I/O Kernel 和能力计划中的旧布局/关闭策略。
  历史 changelog、测试报告和历史验证数字不改写；相关索引仅增加本条。

## 验证

使用现有 uv 环境；沙箱内加 `--offline --no-sync`、专属缓存、pytest `-p no:cacheprovider --basetemp`，
TEMP/TMP 定向工作区任务临时目录。以下批次有重叠，不累加为唯一测试总数。

1. 格式核心/lab/public facade：109 passed；19 真实文件共 9,690 条零修改逐字节一致。
2. SST adapter/catalog/import：79 passed、2 deselected；后续扩大回归包含 import_adapters 并通过。
   插件新建 32 passed；持久化关闭重开、源删除、完整保真及旧身份拒绝 6 passed。
3. 扩大命令：`uv run pytest tests/parser tests/contracts/io tests/ui/operations tests/smart_assistant/tools/test_write_back_options.py tests/ui/test_structured_migration_import.py tests/capability/test_capability_matrix.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_xml.py -q`。
   首轮 1,246 passed、9 failed。两项旧 SST 测试的硬编码字段与无效 wire 已修正；
   5 项 Windows 沙箱原子重命名权限失败在沙箱外复验，相关 8 项全部通过。
4. 修正后命令：`uv run pytest tests/contracts/io/test_migration_import.py tests/contracts/io/test_sst_migration_matching.py tests/contracts/io/test_original_match_migration.py tests/ui/operations/test_write_save_as.py tests/smart_assistant/tools/test_write_back_options.py -q`，94 passed。
   GUI 单个/批量另存 25 passed；batch agent 后续补充测试 8 passed。
5. 两项无关 UI 失败在独立 HEAD `b3878ccae3870e9a9e49e1bbd49158f726a75930` 的 src/tests 快照复现：
   `test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads` 模拟 slot 无 collection；
   `test_upload_menu_uses_card_mode_selection_while_other_intents_use_plan_facade` 模拟 host 无 workbench。
   HEAD 同为 2 failed，未修改无关代码或夹具；对照目录已清理。

## 兼容与遗留

- 已有 SST 工程可编辑；缺 codec v2 物理绑定时提示重新导入原 SST 并迁移译文，不自动重写身份。
- 原 SST 保存哈希和全部 metadata；插件生成哈希采用固定上游规则，同样输入得到同样哈希。
- VMAD、未知映射及非 ASCII 主文件名暂不支持插件新建；原 SST 完整写回不受生成边界影响。
- 正式 MCP 仅基础能力/上下文工具，未扩展文件读写面。
- 未运行全仓 pytest、安装包发布或全面 xTranslator 应用/另存验证；没有 computer-use、commit 或 push。
- 上述两项基线 UI 夹具问题留待独立任务，不将本轮广域称为全绿。

## 最终复核补充

- 新增 `smart_assistant/tools/_sst_import.py`，修改 `_source_import.py` 的 SST append 分支：
  以捕获的完整文档重建证据，解析条目须与快照一致，仅修改 task.base 中唯一匹配目标的译文/阶段，
  保留目标身份和 metadata，未匹配与重复项保存为 unmapped。未放宽通用成果合入身份规则。
- `tool_parser.py` 修复 SST 未选格式会被 use case 拒绝的入口：按单次读取的 magic 选择 SSU8/9 adapter，
  使用 parse_bytes 保持来源一致。`test_sst_append.py` 新增 20 项，包含两个版本、插件/原 SST、
  歧义、空译文、未知签名、快照注入、源路径删除和保存失败后的重试。
- GUI 迁移也检查全部物理来源的重复定位，即使两个译文相同或其中一个为空也不猜测。
  最终 `test_sst_migration_matching.py` 为 14 项。插件 masters 和助手输出文件名均优先使用保存的 display_name，
  避免恢复来源的内部 URI 被写入词典；助手写回测试增至 30 项。
- 助手扩大单独回归 68 passed、1 failed；失败为既有 EET 回执重试，已在
  [前序来源统一记录](../../maintenance/translation-import/2026-11-07-003-source-format-parity.md) 中以原版模块复现。
  本次未修改 EET 回执路径。

最终联合命令（外层仍使用专属缓存和临时目录，因 Windows 沙箱重命名限制在本机环境执行）：

```powershell
uv run pytest tests/parser/test_sst_lab_codec.py tests/parser/test_sst_lab_cli.py tests/parser/test_sst_parser_serializer.py tests/contracts/io/test_sst_adapter.py tests/contracts/io/test_sst_export.py tests/contracts/io/test_sst_publication.py tests/contracts/io/test_sst_migration_matching.py tests/contracts/io/test_migration_import.py tests/ui/operations/test_write_save_as.py tests/ui/operations/test_write_batch_sst.py tests/smart_assistant/tools/test_sst_append.py tests/smart_assistant/tools/test_write_back_options.py tests/ui/test_structured_migration_import.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_xml.py tests/capability/test_capability_matrix.py -q
uv run ruff check src tests scripts/sst_lab.py
uv run ruff format --check src tests scripts/sst_lab.py
git diff --check
```

联合 **339 passed**；全库 Ruff、格式和 diff 空白检查通过。各子 Agent 专属目录已自行核验清理，
主任务临时目录 `.tmp-sst-integration-20261108` 在最终检查后清理；保留前序用户数据和手动验收产物。
