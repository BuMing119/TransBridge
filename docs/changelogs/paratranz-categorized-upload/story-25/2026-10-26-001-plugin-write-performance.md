# S25：插件写回性能优化

日期：2026-10-26（Asia/Shanghai）。
关联：[当前计划](../../../../plans/paratranz-categorized-upload/plan.md)、[S24](../story-24/2026-10-25-001-bsa-localized-write.md)、[写回说明](../../../dev/writer.md)。

## 原因与修复边界

用户最新实际日志 `20261025_235514.log` 的可见写回段约 177 秒。
80 个插件在写回阶段发生 161 次源快照解析，完整输出回读另算；其中 Dragonborn 因跨表类型恢复再多解析一次。
Skyrim 记录 `updated=64766, inline=0`，却仍因目标路径不同调用库 `save()`，重建和重压整个插件。
同轮还有 232,141 条 DEBUG、27,407,341 字节日志。日志持续推进，没有 ERROR 或死锁证据。

本轮按用户授权优化重复工作，继续复用原解析/写入库；没有自写二进制解析、解压或新的字段分类，
没有关闭来源、定位、stage、完整输出回读或回滚保护，也没有修改全局缓存、并发或日志配置。

## 文件与实现增量

- 新增 `application/io/plugin_publication_source.py`：单次发布内按快照对象 identity 和实际源语言复用
  原库解析结果、原公开条目及写前完整物理条目。按对象而非单纯插件 SHA，防止相同插件字节配不同
  Strings、命名空间或来源时串用；保留快照强引用以防 identity 复用。
  ContextVar 作用域每次发布独立，嵌套/并行隔离，退出必 reset；失败、取消、重试不继承旧可变插件。
  同次发布再次 render 也重建可变实例。渲染返回后退休大型 parser，仅留条目用于校验，避免双份解码对象重叠。
- `application/io/plugin_write.py`：写入及跨表类型恢复复用上述准备结果；仅外置 Strings 变化、
  没有 inline 修改时直接写入已确认快照的插件原字节，保留原有产物集合约定。
  真正 inline 修改仍调用原库 `save()`；namespace、原文/定位、ID、类型、stage 和共享 ID 检查每次照常执行。
- `application/io/publish/plugin_fidelity.py`：源物理位置取写前缓存，不从已修改插件重新推导原文。
  输出仍完整调用原库读取，校验所有物理位置，包括未请求、空文本和冲突跳过位置；各 Strings 文件照常验证。
  未采用仅遍历缓存位置的快速输出验证，避免漏掉新增 Strings ID 使此前未解析字段变得可解析的情况。
- `application/io/publish/plugin_bundle.py`、`coordinator.py`：各自公开 publish 边界建立独立准备作用域，
  render 完全返回后释放源 parser，再进行完整输出读取、校验、备份和原子替换。
  验证结果、目标指纹、权限、stage 决定不进入缓存，原提交和回滚机制保留。
- `parser/plugin_parser.py`：DIAL 映射、空文本及 INFO 补全按扫描聚合计数，使用惰性格式化。
- `parser/plugin/plugin_with_context.py`：递归扫描共享局部未解析 ID 计数，每次扫描最多 5 个 DEBUG 定位样本，
  不保留或格式化所有逐条诊断。公开 group/whole-plugin/dictionary 接口签名及提取结果保持。
- `docs/dev/writer.md`：更新当前应用入口的准备复用、原字节复制、内存释放和完整验证边界；相关计划/索引同步。

固定优化前完整 `src` 副本含 1,218 个文件及全部既有 dirty，manifest digest 为
`fea4fecf481956fe523540d15b78eca67118c46cafa30376166fe5eeb6a929a9`。
本轮仅记录相对该副本的七个生产文件增量，不把已有 S24、AI 或其他工作区改动纳入本次归属。
职责复核：准备与生命周期提取到独立小模块；现有 500 行以上 context parser 仅局部聚合扫描诊断，
没有增加新解析职责或格式规则。未改依赖、lockfile、工程 schema 或发行配置。

## 正确性证据

- 新增 `tests/integration/publish/test_plugin_write_reuse.py`：19 个参数化用例。
  真实压缩 BSA 及共享类型 ID 验证原字节复制、源仅解析一次而目标仍完整回读、失败后重试、
  同输入再次发布、inline 再次 render、嵌套/并行隔离、相同插件 SHA 配不同 Strings 不命中，
  以及 staged 读取入口前原 decoded plugin 的 weakref 已失效。该释放断言未靠强制 GC。
  六个关键性能行为用优化前出版函数在隔离进程中复验，均先失败；新实现通过。
- 新增 `tests/parser/test_plugin_scan_logging.py`：7 项，提取结果、source order、进度和计数不变，
  各入口仅有界样本、计数不串轮，关闭 DEBUG 不做 ID 格式化。固定 before 两模块时 7 项全失败，修后全通过。
  合成 100 INFO 加 100 空文本场景日志 205→5 条，120 个未解析 ID 场景 121→2 条（含既有 DLBR 摘要）。
- 独立只读审查检查作用域、嵌套、取消/重试、缓存 key、写前物理位置与退休时机，未发现遗留问题。
  其指出的双 decoded 对象风险已在正式 after 测量前处理并补回归。

最终源码冻结后执行：

```powershell
uv --cache-dir .tmp-opt25/uv-cache run --no-sync pytest tests/contracts/io tests/integration/publish tests/writer tests/parser tests/application/fomod tests/application/projects tests/persistence/v2 tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_write_execution_feedback.py tests/ui/operations/test_fomod_execution_feedback.py tests/ui/test_project_source_hydration.py tests/ui/test_v2_project_transfer.py tests/ui/test_fomod_journey.py tests/ui/test_logging_setup.py -q --disable-warnings --basetemp .tmp-opt25/g -k 'not test_strict_parse_preserves_loading_exception_and_default_compatibility and not test_strict_parse_accepts_valid_plugin_without_translatable_strings and not test_reordered_source_projects_into_context_only_remote_plan'
uv --cache-dir .tmp-opt25/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-opt25/uv-cache run --no-sync ruff format --check src tests
```

结果：**1295 通过、1 跳过、3 排除、7 警告**，70.15 秒；全库 Ruff/格式通过，2212 文件。
三个排除仍是 S24 已核验的两项严格 parser 缺失 `test.esp` 的 mock fixture，以及旧来源更新的上传顺序断言。
未为其放宽真实检查。新增 26 项已包含在扩大计数内，其他专项不相加。

## 性能测量与结果

环境：Windows 11 build 26200、Python 3.12.12、32 个逻辑 CPU、约 32 GiB RAM；现有 uv 环境，
`sse-bsa=1.1.0`、`sse-plugin-interface=1.0.1`、`psutil=7.2.2`。
两版本均使用 DEBUG FileHandler 与 `DiagnosticLogFormatter`，GC 开启，阈值 `(700,10,10)`。
每轮独立新进程，前后同一源及输出卷，不清除 OS 文件缓存。基线和 after 不并行测量，
扩大 QA 与 after 错开；正常运行有监控采样，profile 仅作独立诊断。

主指标 `publish_seconds` 包含预检及完整 hydrated workload 发布，来源初次加载、请求输入准备和发布后
额外独立全量回读分别计时，不算进发布指标。通用 runner 的进程总耗时包含这些步骤，不能混用。
所有产物仅在本轮临时目录；游戏原插件和 Interface BSA 始终只读，每轮前后检查 SHA256。

- 实际 Skyrim：249,752,131 字节，64,766 条请求。
  before 五轮样本 90.585、82.172、100.390、88.181、87.227 秒，中位 **88.181**，范围 **82.172～100.390**。
  after 五轮样本 42.077、41.542、49.494、38.879、38.288 秒，中位 **41.542**，范围 **38.288～49.494**。
  中位耗时缩短 **52.9%**，吞吐约 **2.12 倍**。发布峰值 RSS before **4128～4197 MiB**，
  after **4053～4055 MiB**；没有因准备复用而出现双份大型 decoded 对象叠加。
- 合成中型压缩 BSA：6,000 条，before/after 各五轮。
  before 中位 **1.654** 秒（**1.198～2.094**），after **0.971** 秒（**0.950～1.077**），缩短 **41.3%**。
  发布峰值 RSS 约 93 MiB→94～96 MiB，未出现倍增。
- 实际 Dragonborn：10,907 条，仅各一次正常覆盖，**23.583→8.346** 秒；不能作为五轮稳定性能结论。
  跨表源恢复与完整输出回读均通过。
- after 每轮额外断言：源快照解析 **1 次**、纯 external 库 `save()` **0 次**、staged 完整库读取 **1 次**。
  before Skyrim 源快照两次、Dragonborn 三次；输出读取保留。
  Skyrim 同配置整轮诊断文件 **19,870,457→10,010 字节**，并未关闭全局 DEBUG。
- 每轮额外独立回读核对全部合成译文，输入源哈希均未变。
  三种 companion 产物跨版本逐字节相同；after 插件哈希等于原插件字节。
  before 库重建后的插件哈希与原始文件不同，因此不声称四件套跨版本全字节相同。

代表性命令（driver 和固定源码为本轮拥有的隔离工具，不写入长期 performance 基线）：

```powershell
.venv/Scripts/python.exe .agents/skills/bm-perf/scripts/benchmark_command.py --runs 5 -- .venv/Scripts/python.exe .tmp-opt25/driver.py --scenario skyrim --game-data 'D:\MyProgram\SteamLibrary\steamapps\common\Skyrim Special Edition\Data' --source-root .tmp-opt25/before/src --results-root .tmp-opt25/before-skyrim
.venv/Scripts/python.exe .agents/skills/bm-perf/scripts/benchmark_command.py --runs 5 -- .venv/Scripts/python.exe .tmp-opt25/driver.py --scenario skyrim --game-data 'D:\MyProgram\SteamLibrary\steamapps\common\Skyrim Special Edition\Data' --source-root src --results-root .tmp-opt25/after-skyrim
```

中型同命令使用 `--scenario medium`，Dragonborn 使用 `--scenario dragonborn`、正常各一次。
driver 断言所有 `transbridge` 导入路径来自指定 source-root；临时 manifest 核验输入代码，
原始逐轮结果、分段计数、日志字节和源/产物哈希汇总用于本记录。
独立 profile 确认 Dragonborn 旧链库读取四次（三源一目标）及完整重压；其 trace 开销不参与正常统计，
各段 inclusive 时间不能相加。监控线程的 wait 累计不作为业务瓶颈。
追加 profile 在额外回读阶段中止，其完整 publish 仅作诊断，不混入正常轮或正确性通过数量。

## 兼容性与边界

无工程迁移或用户新增步骤；现有库及保护规则保留，运行中的旧进程需重新加载代码。
仍需一次源解码和完整输出解码，大型插件不会变成瞬时完成；本轮未改写库解析器以跳过记录。
这些是发布后端的同输入对照，不是 GUI 全批量旅程、80 插件或 96,306 条真实工程译文的完整测速，
不保证其他机器、磁盘或后台负载得到同样比例。

本轮拥有的 `.tmp-opt25` 在核验完成、所有测量进程结束后清理，包括固定源码副本、合成产物、
profile、日志和 uv 缓存；保留既有用户日志、工程及其他临时目录。
未修改游戏目录、调用真实云端、运行全库 pytest、构建发行包、commit 或 push。
本次范围无未完成项；三项既有测试问题保留。

## 最终执行协议与完整性复核

测量统一设置 `PYTHONDONTWRITEBYTECODE=1`、`UV_CACHE_DIR=.tmp-opt25/uv-cache`、
`TEMP/TMP=.tmp-opt25/reader-temp`，before 的 `PYTHONPATH=.tmp-opt25/before/src`，after 为 `src`；
每轮计时前执行 `gc.collect()`，两版本一致。正式中型基线仅使用 `before-medium-final`，早期试跑不混入统计。

全部 **22 个正常前后测量轮**独立回读成功，after 的 **11 轮**均通过 1/0/1 发布计数断言。
结束时再次核对 5 个源资产和 88 个产物文件，哈希均符合逐轮记录。
优化前 1,218 个源码文件、优化后 1,219 个文件的最终 manifest 均未漂移；after manifest digest 为
`4ee48186da88f06f7cb4d7231ac1b313083c84d6c98a0e778fb7413f8ca1f19b`。
正常与中止的诊断测量进程均已退出，不遗留监控或基准子进程。
