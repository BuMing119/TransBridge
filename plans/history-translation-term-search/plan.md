# history-translation-term-search（历史翻译与术语统一搜索）

**对应需求**: FR29
**架构决策**: ADR-038
**技术模块**: application + persistence + bootstrap + ui
**状态**: 已完成（2026-09-06，多窗口与范围筛选扩展自动化 QA 通过）
**创建日期**: 2026-09-06

## 目标与边界

提供一个独立于当前项目会话的只读搜索窗口，用单个关键词检索所有已保存 Project/Variant 译文、`.tbdict` 翻译记忆和当前生效术语。相同内容只在结果展示层合并来源，不修改或合并任何权威数据。

首版只支持查看来源与复制译文，不提供回填、编辑、删除或同步能力；不索引未保存工作区、术语草稿/历史版本和 ParaTranz 远端历史。

## Story 01：搜索契约与可重建索引

**依赖**: 无
**主要落点**: `src/transbridge/application/history_search/`、`src/transbridge/persistence/history_search.py`

**验收标准**:
- [x] 定义来源记录、合并结果、来源追踪、诊断和分页查询契约，翻译与术语类型不可互相合并
- [x] 统一执行 NFKC、换行、首尾空白和大小写规范化；原文或译文任一字段包含关键词即可命中
- [x] 相同类型、语言、有效 scope、规范化原文和译文的记录合并为一条结果，同时保留全部来源
- [x] 同一原文/语言/scope 对应多个译文时分别展示，并标记为“存在不同译法”
- [x] SQLite 索引通过同目录暂存文件构建并原子替换；失败或取消时保留上一个完整索引
- [x] 查询支持类型过滤、稳定排序、上限和 `%`/`_` 等 LIKE 字符转义

## Story 02：三类只读数据提供器与后台刷新

**依赖**: Story 01
**主要落点**: `src/transbridge/bootstrap/history_search.py`、`src/transbridge/application/history_search/task_adapter.py`

**验收标准**:
- [x] Project/Variant 提供器枚举全部已保存项目和 Variant，仅收集非 tombstone、非空译文
- [x] Project/Variant 原文通过已注册源文件和 FormatAdapter 解析后按完整 EntryKey 精确恢复；不猜测未匹配原文
- [x] `.tbdict` 提供器逐文件只读解析，损坏文件只产生可见诊断，不触发重命名或修复写入
- [x] 术语提供器只读取每个 Project/Variant 的当前生效版本和有效决策，项目级与插件级 scope 分开
- [x] 单个来源失败不阻断其余来源；刷新报告包含收集数量和诊断
- [x] 刷新通过 TaskRuntime 执行，可取消且不会阻塞 Qt 事件循环；运行时注册查询与刷新入口

## Story 03：统一搜索窗口与 Shell 集成

**依赖**: Story 01、Story 02
**主要落点**: `src/transbridge/ui/tools/history_search/`、`src/transbridge/ui/shell/`

**验收标准**:
- [x] “翻译”菜单提供无需打开当前 Project 的“历史翻译与术语搜索”入口
- [x] 窗口提供关键词、类型筛选、刷新、结果表格、来源详情和复制译文操作
- [x] 首次无索引时自动刷新，之后允许显式刷新；刷新过程和失败诊断有清晰状态
- [x] 输入采用短延迟防抖，查询在 Qt 线程池运行；旧查询结果通过 generation 丢弃
- [x] 空关键词按当前来源与类型范围展示有界首屏；无结果、索引未就绪和刷新失败状态可区分
- [x] 窗口只读，不提供直接套用、批量覆盖或编辑权威数据的入口

## Story 04：回归、性能与追溯收口

**依赖**: Story 01～03

**验收标准**:
- [x] 覆盖三类来源、展示层去重、不同译法、scope 隔离、Unicode/大小写、分页与特殊字符查询
- [x] 覆盖损坏 `.tbdict`、源文件变化、无当前项目依赖、刷新取消和原子替换失败保旧索引
- [x] 覆盖 UI 过期结果丢弃、复制译文、类型筛选及关闭窗口后的订阅清理
- [x] 执行相关 pytest、Ruff check 和 Ruff format 检查，并记录未执行的外部/人工验证
- [x] 根据真实差异更新 Plan 状态、索引、QA 证据和增量变更记录

## Story 05：多窗口与来源范围筛选

**依赖**: Story 01～04
**主要落点**: `src/transbridge/application/history_search/`、`src/transbridge/persistence/history_search.py`、`src/transbridge/ui/tools/history_search/`、`src/transbridge/ui/shell/tool_windows.py`

**验收标准**:
- [x] 每次触发菜单入口均创建新的独立搜索窗口；关闭或修改一个窗口不影响其他窗口
- [x] 每个实例使用无主窗口 owner 的顶级 HWND 和唯一 Windows `AppUserModelID`，形成独立任务栏按钮，并以实例序号区分标题
- [x] 每个窗口提供“全部来源 / 单个 Project / 单个词典”范围选择，并默认选择全部来源
- [x] Project 范围包含该 Project 所有已保存 Variant 的完整译文和当前生效术语；词典范围只包含对应 `.tbdict`
- [x] 关键词留空时按当前范围与类型展示全部候选的有界首屏，不阻塞 Qt 事件循环
- [x] 索引刷新后所有仍打开的窗口重新加载来源范围并刷新各自结果
- [x] 覆盖空关键词、范围隔离和多窗口生命周期回归

## 架构依赖

- [ADR-038：可重建历史搜索投影](../../docs/adr/038-rebuildable-history-search-projection.md)
- Project/Variant 权威快照与 source registry
- FormatAdapter 解析契约和完整 EntryKey
- `.tbdict` `Dictionary` 数据模型（只读反序列化）
- 项目术语 `effective_version` / `TermDecision.is_effective`
- `TaskRuntime`、Shell Action Catalog / Intent / ToolWindows

## 风险与回退

- **源文件已移动或内容已变化**：该 Variant 的无法验证条目不进入本次索引并输出诊断；不根据局部字段猜测原文。
- **历史数据量导致刷新或查询变慢**：刷新保持后台执行，查询有结果上限；若基准不达标，再增加 FTS/增量刷新而不改变应用层契约。
- **索引构建中断**：删除任务自身暂存文件并继续使用旧索引；无旧索引时明确显示“尚未建立索引”。
- **来源内容重复**：仅聚合结果来源，不回写 Project/Variant、`.tbdict` 或术语库，因此可随时删除索引并重建。

## 验证命令

- `uv run pytest tests/application/history_search tests/persistence/test_history_search.py tests/ui/tools/test_history_search_window.py -q`
- `uv run ruff check src tests`
- `uv run ruff format --check src tests`

## 实施与 QA 结果

- 功能最终回归：45 passed；覆盖应用服务、索引、三类提供器、空关键词浏览、Project/词典范围隔离、多窗口生命周期、独立任务栏身份、运行时端到端和菜单入口。
- 相关存储/组合扩大回归：93 passed；覆盖翻译记忆、术语 repository、Variant 物化、Project catalog 与完整 Bootstrap 集成。
- 一万条来源记录的目标关键词与空关键词有界首屏测试：pytest 整体 call 约 0.22 秒，两类查询断言门限均为 0.5 秒。
- `ruff check src tests`：通过。
- `ruff format --check src tests`：1201 files already formatted。
- 未执行：真实大型用户 Project 的人工桌面交互、真实损坏资产组合以及安装包构建；功能不涉及网络服务。
