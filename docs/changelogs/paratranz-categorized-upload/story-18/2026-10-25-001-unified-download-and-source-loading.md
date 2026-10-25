# 统一下载窗口、修复默认服务与来源读取性能

- 日期：2026-10-25
- Epic：paratranz-categorized-upload
- Story：S18
- 需求：FR3.3；[计划](../../../../plans/paratranz-categorized-upload/plan.md)
- 范围：本地代码、测试和说明；不提交 Git、不构建安装包、不访问真实 ParaTranz 项目。

## 问题与结果

用户发现下载仍有两个入口、插件列表加载慢，实际下载报
`ParaTranzService.__init__() missing 3 required positional arguments: 'strings', 'history', and 'exports'`。
之前测试替换了整个服务工厂，未覆盖默认生产构造，这是本次补测的直接原因。

当前工程单插件和多插件现共用一个“下载更新…”入口、同一窗口和一个主按钮。
按钮由“开始对照”切换为“更新并保存”；复核和重新对照为辅助操作。
默认仅选择当前插件，可全选、全不选；未打开具体插件时仍能选择当前工程来源。
下载成功自动保存当前本地版本、保留下载前历史还原点、保存失败原窗口重试的 S17 语义保留。

## 实际文件变化

- `src/transbridge/ui/operations/facade.py`：当前权威工程的普通/批量调用均进入同一下载 launcher，
  不再用 batch 参数分流窗口；非权威上下文和显式自定义工厂的通用计划合同保留。
- `src/transbridge/ui/workbench/cards/download_card.py`、`workbench/step3.py`：一个下载按钮，
  当前集合为空但已打开工程时仍可进入；旧批量方法成为同入口别名。
- `src/transbridge/ui/coordinators/operation_coordinator.py`、`ui/shell/menu_builder.py`、
  `action_catalog.py`、`intent_composition.py`、`context_help.py`：菜单、意图和帮助与实际能力一致；
  旧批量 QAction 隐藏、旧 intent 可兼容分派，不产生第二个可见入口。
- `src/transbridge/ui/operations/batch_download_dialog.py`、`batch_download_view.py`：
  通用标题、当前来源预选、全选/全不选、一个阶段主按钮，后台工作及失败重试保持原窗口。
- `src/transbridge/ui/operations/batch_download_session.py`：默认工厂改为 `ParaTranzService.from_config`，
  补齐完整 API 依赖；权限读取传递取消令牌。列表改用轻量 catalog，目录修订竞争内部有界重读。
  同轮路径命名只建一次；提交路径检查跳过空来源，避免已排除的空插件阻断其他可靠来源保存。
- 新增 `src/transbridge/application/projects/file_source_catalog.py`：复用启用来源登记和当前 Variant，
  获取名称、命名空间和非删除词条数量，不打开任何源文件。
- 新增 `src/transbridge/application/projects/source_hydration_cache.py`：缓存不可变来源文本、身份和上下文，
  不持有完整插件二进制、写回模板或旧译文；上限 128 来源、250,000 词条、128 MiB 估算保留量。
  每次校验源文件和 loose Strings/BSA 当前字符串成员的实际内容，包含缺失依赖；不依赖 mtime/size。
  请求身份/会话/权限/授权根隔离，内容读取前复用规范路径授权。读取中变化拒绝采用不完整结果。
- `src/transbridge/application/projects/categorized_upload.py`、`gui_facade.py`：公开 `file_sources_catalog()`；
  每次快照按 namespace 建一次状态索引，复用来源解析后叠加最新 Variant 编辑和引用。
- `src/transbridge/persistence/v2/variant.py`：`VariantSnapshot.__post_init__` 的命名空间集合只构造一次，
  保留重复身份、未声明命名空间的验证及确定性排序；既有大模块只作局部热点修正，不新增职责。
- 新增 `scripts/benchmark_download_sources.py`：真实合成插件、相同不可变工程/Variant、显式基线模块、
  前后交替五轮，分别测量目录、全量/单源冷解析和再次捕获，记录解析次数及输入哈希。
- 新增 `tests/ui/operations/test_unified_download_service.py`：真实默认 facade、Qt 窗口、ApiWorker、
  Service/API 和文件网关，只替换 HTTP 响应。验证权限、当前路径、未选来源、取消、读取/保存原地重试、
  单个主按钮实际对照到保存及磁盘读回；完整成功路径仅三次 HTTP：权限、文件列表、当前文件。
- 新增 `tests/application/projects/test_file_source_performance.py`：零解析目录、未保存编辑、
  同大小/时间戳源变化、Strings/BSA 变化、缓存预算、读取中变动及授权前读取保护，共 13 项。
- 更新下载窗口、session、生产入口、意图、主窗口、来源读取测试；旧通用计划用例显式注入自定义工厂，
  不再把另一套窗口当作当前工程默认行为。session fixture 依据登记 source_id 查实际路径，消除随机来源顺序假设。
- 更新 `docs/requirements.md`、`docs/dev/paratranz.md`、`paratranz-categorized-upload.md` 及当前计划和直接索引，
  移除单独/批量默认分流的过期说明，历史增量保持不变。

## 性能证据

Windows、Python 3.12.12、仓库现有 uv 环境和依赖，80 个真实生成的 ESP、每个 1,200 词条，共 96,000 条。
基线为本轮开始时保存的 `categorized_upload.py`，不是工作区中其他未提交改动的 Git HEAD。
两组共享本轮 `VariantSnapshot` 集合优化，不把该项收益混入来源服务对比。
冷指来源解析缓存为空，不清空操作系统文件缓存；无网络和用户翻译数据。
真实生命周期及不可变契约构造同一工程目录和 Variant，建项耗时不计入来源读取。
两组交替执行，各五轮，源文件哈希前后不变；数据是合成基准，不能直接承诺用户真实大插件耗时。

中位数与五轮最小～最大值：

- 列表：6,750.156 ms（6,712.200～6,837.476）→ 68.713 ms（67.524～75.473）；解析 80→0 次。
- 全量冷捕获：6,908.035 ms（6,696.497～6,970.851）→ 6,514.768 ms（6,428.038～6,606.186）；均解析 80 次。
- 全量再次捕获：6,803.477 ms（6,660.511～6,978.314）→ 821.110 ms（803.796～843.796）；解析 80→0 次。
- 单源冷捕获：159.123 ms（152.157～164.258）→ 158.531 ms（156.681～170.252）；均解析 1 次。
- 单源再次捕获：152.747 ms（148.839～156.043）→ 98.249 ms（96.166～103.586）；解析 1→0 次。

每轮新缓存估算保留 56,872,710 字节，不是进程峰值 RSS；未测真实大型 BSA 的峰值内存。
首次所选内容仍需完整解析；打开目录及重复解析是本次主要改善，不能声称全部下载瞬时完成。

原始五轮耗时（ms，before/after 各自按轮次）：

```text
before.list          6712.200 6793.715 6750.156 6837.476 6730.628
after.list             70.087   67.524   68.713   68.031   75.473
before.cold_all      6908.035 6970.851 6696.497 6908.735 6730.882
after.cold_all       6514.768 6606.186 6555.362 6448.816 6428.038
before.warm_all      6738.998 6978.314 6660.511 6803.477 6955.867
after.warm_all        821.110  824.502  815.734  843.796  803.796
before.cold_selected  158.329  159.123  152.157  161.137  164.258
after.cold_selected   156.681  158.531  157.456  164.204  170.252
before.warm_selected  152.747  156.043  149.990  148.839  155.954
after.warm_selected    96.166   98.249   97.415   98.696  103.586
```

复测命令（需显式保存待比较的旧模块；临时输入和输出位于独占目录）：

```powershell
uv run --no-sync python scripts/benchmark_download_sources.py --baseline <旧categorized_upload.py> --workspace <新建临时目录> --output <临时结果.json> --plugins 80 --entries-per-plugin 1200 --repeats 5
```

仓库没有 bm-perf 提及的 `scripts/benchmark_command.py`，因此使用上述专用脚本。

## 验证与边界

- 默认服务构造先实跑复现失败；修正后真实默认服务/窗口调用链通过，测试不再替换整个 service/factory。
- 空来源原用例先扩展到实际 apply，稳定复现 `IndexError`，根因修复后该用例通过。
- Qt 实际窗口截图核对一个主按钮、当前来源预选、辅助重新对照和通用标题。
- 最终整合：**1,574 passed、1 deselected**，79.86 秒，退出码 0；4 条既有弃用 warning。
  运行时 TEMP/TMP 指向任务独占目录，未设置 FAISS_OPT_LEVEL。

```powershell
uv --cache-dir .tmp-unified-download-a0eb07b2/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/application/projects tests/contracts/paratranz tests/persistence/v2 tests/paratranz tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_intent_router.py tests/ui/test_workbench_theme_migration.py -q --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-unified-download-a0eb07b2/final-integrated
uv --cache-dir .tmp-unified-download-a0eb07b2/uv-cache run --no-sync ruff check src tests scripts/benchmark_download_sources.py
uv --cache-dir .tmp-unified-download-a0eb07b2/uv-cache run --no-sync ruff format --check src tests scripts/benchmark_download_sources.py
```

Ruff check 全过，格式检查 2,187 文件全过，退出码均为 0。
源服务/持久化专项 71 项、真实窗口/服务等专项 61 项及独立复核 46 项与整合用例重叠，不累加通过数。

初轮扩大回归 1,571 项通过，14 个 `test_typed_client.py` 用例因沙箱禁止连接本地 HTTP server，
得到 WinError 10013，不能据此声称其 HTTP 状态映射出错；最终整合排除该文件。
不相关 `test_terms_api_contract.py` 未运行；S16 已独立复现的旧上传排序断言
`test_reordered_source_projects_into_context_only_remote_plan` 继续明确排除，不修改无关上传语义。
一次设置 FAISS_OPT_LEVEL=generic 的启动发生 native handle 异常并中断；相同环境的小组合及完整收集小组合各 27 项通过，
无法稳定复现。最终整合使用环境默认 FAISS 模式，不修改本轮无关依赖。
初轮部分插件写回测试因系统 TEMP 不可写失败，改用任务独占 TEMP/TMP 后 UI 扩大回归 509 项通过，无写回业务改动。

未运行全库 pytest、安装包构建、真实云端下载或写入；受控 BSA 端口覆盖成员变化，未测试真实大型 BSA，
也不宣称抵御恰好在最终内容校验后发生的跨进程文件修改。无依赖、schema、数据迁移或云端文件迁移。
临时基准输入、截图、缓存和测试目录在确认任务归属后统一清理，保留以上数值和复测脚本。
