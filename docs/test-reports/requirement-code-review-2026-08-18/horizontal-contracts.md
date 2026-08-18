# TransBridge 需求—实现横向契约审查

**日期**：2026-08-18  
**审查角色**：高级 Python / PyQt 工程实现审查  
**审查方式**：只读静态审查；未运行测试、未构建 wheel、未启动 GUI、未调用外部服务  
**审查范围**：parser/writer、AI 翻译与后处理、Agent 工具/Graph/MCP、项目持久化、Session/Task、FOMOD、Translation Memory、fileops、发布与导入体系  
**限制**：项目缺少 `.Codex/bm_config/paths.json`，因此未按 `bm-qa` 正式流程更新索引或 changelog；本文只新增审查报告。

## 1. 结论

当前代码库的主要风险不是“缺少类或文件”，而是**需求、Plan、入口适配层与领域实现之间缺少可执行契约**。多个 Epic 的底层实现已经存在，但从 GUI、Agent 或 MCP 入口走真实调用链时，仍会因模块名、构造签名、状态归属、线程边界和持久化语义不一致而失败。

本次静态审查确认 4 类阻塞性问题：

1. Agent 的 ESP/EET/XT/SST 解析工具及 EET/XT 写回工具存在直接签名错误，属于“代码已存在、入口不可用”。
2. Variant 切换与恢复采用增量覆盖且不恢复 stage，会造成版本间译文串线，并可触发 AI 重新翻译已恢复或词典命中的译文。
3. MCP 启动入口缺失 `ToolRegistry` 导入，且没有绑定真实 `AppContext` 与认证配置；启用 MCP 时入口本身不可用。
4. 安装发布契约不成立：console script 指向不存在的包属性，业务包内还有 71 个文件、135 处 `src.transbridge` 导入，wheel 安装后存在系统性失败风险。

另外，Session/Task、Graph checkpoint、FOMOD、Translation Memory 均存在“局部实现满足文件级验收，但跨模块联调不满足业务语义”的情况。建议先修数据正确性和入口阻塞，再引入共享服务契约，最后调整架构与 Plan；不建议继续按现有 Epic 边界分别打补丁。

## 2. 严重级别

- **P0**：入口完全不可用、确定的数据串线/损坏风险、启用功能即阻塞启动、发布物无法启动。
- **P1**：核心流程在常见条件下行为错误，或后台任务/状态恢复不可靠，发布前必须解决。
- **P2**：可维护性、错误可观测性、局部一致性或边界条件问题，宜随下一轮重构处理。
- **P3**：文档、命名、依赖分组和演进治理问题。

## 3. P0 问题

### P0-1 Agent Parser/Writer 入口与真实领域 API 完全错配

**需求/Plan 宣称**：`agent-tool-expansion` Story 12/19/24 声称解析结果可落入 `CollectionSlot`，并可根据已解析源文件写回 ESP/EET/XT/strings。

**真实调用链**：

`ToolRegistry` → `tool_parser._parse_file()` → 动态导入 parser → `cls().parse(path)` → `_to_collection()` → `_create_slot()`

**静态证据**：

- `src/transbridge/smart_assistant/tools/tool_parser.py:126-156` 的 dispatch 指向不存在的 `eet_xml_parser`、`xt_xml_parser` 模块。
- 同文件 `:180-184` 无视 dispatch 中定义的 `parse_fn`，对所有非 JSON 类型统一调用 `cls().parse(path)`。
- `PluginParser` 真实入口是 `parse_plugin(Path)`，见 `src/transbridge/parser/plugin_parser.py:24`，不存在 `parse()`。
- `EET_XmlParser` 真实入口是类方法 `from_file(path)`，构造函数要求 `entries`，见 `src/transbridge/parser/eet_parser.py:77,95`。
- `XT_XmlParser` 真实入口是类方法 `from_file(path)`，构造函数要求 `params, entries`，见 `src/transbridge/parser/xt/xt_parser.py:52,61`。
- `SST_Parser` 真实入口是类方法 `from_file(path)`，构造函数要求 entries 等参数，见 `src/transbridge/parser/xt/sst_parser.py:94,106`。
- 即使修复方法名，EET/XT/SST parser 返回领域 Entry 或 parser 迭代器，不能直接作为 `TranslationEntry` 加入集合；`_to_collection()` 当前只做 `list(result)`，见 `tool_parser.py:46-55`。
- `_create_slot()` 只构造 `CollectionSlot(label, collection)`，见 `tool_parser.py:58-78`；没有保存 `esp_path/eet_path/xt_path/sst_path/plugin/strings_lookup`，而写回依赖这些上下文。
- EET/XT 写回工具错误地调用 `EETWriter()` / `XTWriter()` 无参构造并执行 `write(collection, path)`，见 `src/transbridge/smart_assistant/tools/tool_writer.py:44-63`；真实 API 要求 `Writer(parser)` → `apply_collection(collection)` → `write(path)`，见 `writer/eet_xml_writer.py:17,22,99` 与 `writer/xt_xml_writer.py:14,19,87`。
- ESP/strings 写回依赖 `slot.plugin`，见 `tool_writer.py:12-25`；Agent parser 创建的 slot 不保存 plugin，因此即使解析成功也会返回“当前槽位无已解析的插件”。
- `_validate_path()` 拒绝所有绝对路径，见 `tool_parser.py:35-40`。桌面文件选择器和外部 MCP 客户端通常提供绝对路径，导致合法文件无法通过入口。

**影响范围**：FR9.12、Agent Tool Expansion Story 12/19/24；Agent 解析和写回的主路径；MCP 未来复用同一工具时也会继承失败。

**目标方案**：引入 `SourceDocumentService`，禁止工具层直接理解每个 parser/writer 的构造签名。

```text
ParseRequest(path, format, language)
  -> SourceDocument(
       source_id,
       descriptor={kind,path,fingerprint,language},
       collection,
       write_context={plugin|xml_parser|strings_lookup}
     )

WriteRequest(source_id, target_path, target_format, backup_policy)
  -> WriteResult(updated_count, artifacts, warnings)
```

每种格式通过 adapter 实现 `parse()` / `write()`；UI、Agent、FOMOD 只调用同一服务。路径安全改为“允许根目录 + resolve 后 containment 校验”，不再一律拒绝绝对路径。

**兼容迁移**：先保留现有工具名和参数 schema，在 wrapper 内转调新服务；`CollectionSlot` 增加 `source_id`，旧字段暂由 adapter 回填。第二阶段再废弃工具对 `plugin/parser` 对象的直接访问。

**粗略工作量**：热修 1-2 人日；共享服务与四类 adapter 5-8 人日；契约测试 2-3 人日。

**需更新的 Plan/Story**：

- `plans/agent-tool-expansion/stories/story-12-p2-parser-writer-project.md`
- `plans/agent-tool-expansion/stories/story-19-write-back-merge.md`
- `plans/agent-tool-expansion/stories/story-24-parser-side-effects.md`
- `plans/file-parsing/plan.md`
- `plans/file-writing/plan.md`
- 建议新增跨 Epic ADR：SourceDocument/WriterContext 契约。

### P0-2 Variant 切换会串入上一版本译文，恢复数据还会被 AI 当作未翻译

**需求/Plan 宣称**：FR8.9 要求各 Variant 独立维护译文与标签；切换版本时“仅替换译文和标签视图”。

**真实调用链**：

`MainWindow._switch_variant()` → `VariantStore.load()` → `_switch_to_variant()` → `VariantStore.apply_to(existing collection)`

**静态证据**：

- `VariantStore.apply_to()` 只对 current.json 中存在的 id 赋值，缺失项不清空，见 `src/transbridge/persistence/variant_store.py:65-78`。
- `_switch_to_variant()` 直接把新 Variant 应用到当前共享 collection，应用前没有恢复源文件基线或清空旧译文，见 `src/transbridge/ui/main_window.py:1264-1272`。
- 因此 A 版本有译文、B 版本无该 key 时，切到 B 后 A 的译文仍留在内存中，违反 Variant 隔离。
- `collect_from()` 仅写入非空译文，不删除已清空的译文，见 `variant_store.py:92-99`；用户清空译文后，旧值仍在 current.json 内，下次恢复会“复活”。
- `VariantStore` 仍以 `entry.id` 作为持久化索引，见 `variant_store.py:72-76,92-97`；而 ADR-002 与 Agent Tool Expansion Story 23 已把 `entry.key` 定为唯一主索引。
- `apply_to()` 只写 translation，不设置 stage。ESP 重新解析后的 entry 为 stage=0；恢复出译文后仍是 stage=0。
- `AutoTranslator.translate()` 在 overwrite=False 时保留条件是“无译文 **或** stage==0”，见 `src/transbridge/ai_translator/translator.py:372-377`。因此恢复出来但 stage=0 的译文会被重新送给 AI 覆盖。
- FR8.4/Story 05 要求源文件 hash 变化时初始化空白状态并提示；`persistence/` 和 `main_window.py` 中没有 source hash/fingerprint 实现。
- 启动恢复仅重新解析 `type == "esp"` 的 source，见 `main_window.py:1013-1017`，与项目模型支持 EET/XT/Strings 多源不一致。

**影响范围**：FR8.3-8.10、所有 Variant/快照/自动保存、AI 翻译、写回结果；存在用户数据串线与错误覆盖风险。

**目标方案**：引入 `ProjectStateRepository` 与版本化状态 schema。

```text
VariantState(schema_version, source_fingerprint, entries)
entries[key] = {translation, stage, labels, revision, provenance}

load_variant(source_baseline, variant_state)
  = 先从不可变源基线生成新 collection
  + replace 语义应用完整 VariantState
  + 对缺失 key 明确恢复为空/源 stage
```

禁止在同一可变 collection 上叠加多个 Variant。若为性能保留共享结构，也必须使用 `replace_all_state()`：先重置所有可变字段，再应用目标版本。保存完整集合时采用 replace 语义；局部保存必须显式传 `partial=True` 和 tombstone，不能由方法自行猜测。

**兼容迁移**：current.json schema v1 的 id 通过当前解析结果的 `_id_index` 映射为 key，写出 schema v2；无法唯一映射时保留 migration warning，不静默覆盖。首次加载 v2 前自动备份原 current.json。

**粗略工作量**：P0 热修 1-2 人日；schema v2、迁移与 source fingerprint 4-7 人日；多源恢复 2-4 人日。

**需更新的 Plan/Story**：

- `plans/project-persistence/stories/story-01-persistence-data-model.md`
- Story 04 Variant Management、Story 05 Auto Save/Restore、Story 06 Snapshot、Story 08 Variant Write Back
- `plans/agent-tool-expansion/stories/story-23-key-primary-index.md`
- `docs/adr/002-*` 与 `docs/adr/006-*`
- FR8.3/8.4/8.9 的 schema 与迁移条款。

### P0-3 MCP 启用入口当前无法形成一次成功工具调用

**需求/Plan 宣称**：Agent Upgrade Story 12 声称启用 MCP 后可 list/call ToolRegistry 工具，并按配置实施 auth/write/admin 策略。

**静态证据**：

- `src/transbridge/ui/app.py:53-63` 在 MCP enabled 分支使用 `ToolRegistry`，但本文件没有导入该符号；启用 MCP 会在创建主窗口前触发 `NameError`。
- `MCPAdapter` 在 `_ctx is None` 时明确拒绝所有工具调用，见 `src/transbridge/smart_assistant/mcp/adapter.py:42-49`。
- 启动入口创建 `MCPAdapter(ToolRegistry, mcp_config)` 时未传 ctx，之后也未调用 `set_context()`；`MCPServer` 同样未传 ctx。
- MCP 在 `MainWindow()` 和其真实 `AppContext` 创建前启动，见 `app.py:53-64`，因此没有可绑定的桌面状态。
- server 的 config 没有从 `LLMConfig` 传入。`app.py:56-59` 仅把策略给 adapter，不含 `mcp_auth_token`；`MCPServer` 收到空 config 后会生成临时 token，见 `mcp/server.py:26-40`。客户端无法从稳定配置得知该 token。
- Windows GUI/PyInstaller 场景中，server 在后台线程对 `stdin/stdout` 使用 `select.select()`，见 `mcp/server.py:43-50`；这既可能无 console handle，也与 GUI 进程日志/输出生命周期冲突。
- MCP 的 admin 白名单只影响可见性；PermissionGuard 仍硬阻断 admin，代码已在 `mcp/adapter.py:67-77` 自行标记 TODO。Plan 的“白名单可调用”与真实行为不一致。
- 需求文档把 MCP 描述为“本地 JSON-RPC 服务、默认监听 localhost”，Story/实现则为 stdio，传输与生命周期决策未统一。

**影响范围**：FR7.13.5、Agent Upgrade Story 12、外部自动化；启用配置会影响应用启动。

**目标方案**：先做架构选择，不在当前 GUI `main()` 内继续堆补丁。

- **Headless 方案**：新增 `transbridge-mcp` console entry；仅暴露可通过 `ProjectService/SourceDocumentService` 独立工作的工具，不宣称能控制正在运行的 GUI。
- **Desktop-control 方案**：GUI 启动本地 IPC/localhost host，在主线程绑定真实 `AppServiceContext`；MCP transport 只负责协议，所有执行统一进入 `ToolExecutionService`。
- 执行请求必须携带 `channel`, `actor`, `capabilities`, `session_id`, `project_id`, `confirmation_token`，由同一 PolicyEngine 决策，不能在 adapter 中绕过 guard。

**兼容迁移**：短期让 `mcp_enabled=true` 安全降级并显示“暂不可用”而非崩溃；随后增加独立入口。旧配置字段保留一版，并增加明确的 transport 迁移提示。

**粗略工作量**：安全热修 0.5-1 人日；headless 方案 4-6 人日；GUI IPC 方案 7-12 人日。

**需更新的 Plan/Story**：Agent Upgrade Story 12、ADR-012、FR7.13.5；并新增 MCP deployment/runtime Story。

### P0-4 发布入口与包内导入体系不成立

**静态证据**：

- `pyproject.toml:31` 配置 `transbridge = "transbridge:main"`，但 `src/transbridge/__init__.py` 只有 `__version__`，没有 `main`。
- `src/transbridge/main.py:1` 又从 `src.transbridge.ui.app` 导入，而安装后的 src-layout wheel 通常只暴露顶层 `transbridge` 包。
- 业务包中共有 71 个文件、135 处 `src.transbridge` 导入。开发环境同时把仓库根与 `src` 放入 `sys.path` 时，同一源码可能以 `transbridge.*` 和 `src.transbridge.*` 两套模块身份加载，导致 registry、单例、类身份和 `isinstance` 分裂；wheel 环境则可能直接找不到 `src`。
- `main_window.py:13` 使用 `from transbridge import __version__`，紧接着大量使用 `src.transbridge.*`，已经在同一入口混用两种包身份。
- 版本号也不一致：`pyproject.toml:3` 为 0.1.1.1，`src/transbridge/__init__.py:1` 为 0.1.1.8。
- FOMOD/fileops 使用 `py7zr`、`rarfile`，但 runtime dependencies 未声明；`ruff` 反而被放在 runtime dependencies，见 `pyproject.toml:10-28`。

**影响范围**：安装包、CLI、PyInstaller、ToolRegistry 单例、全部模块导入；当前源码测试即使通过也不能证明发布物可用。

**目标方案**：确定唯一包名 `transbridge`。包内优先相对导入，跨顶层子包使用 `transbridge.*`；禁止 `src.transbridge.*`。console script 改为 `transbridge.ui.app:main`，版本改为单一来源（构建元数据或 `importlib.metadata.version()`）。

**兼容迁移**：机械替换分独立提交完成；增加 import guard 测试，扫描并拒绝 `src.transbridge`；开发测试环境移除仓库根带来的偶然 namespace package；构建 wheel 后在空 venv 验证。

**粗略工作量**：2-4 人日，另需 1-2 人日处理打包资源和 clean-install smoke test。

**需更新的 Plan/Story**：建议新增 `release-packaging-hardening` Epic；同步 Core/NFR6、Agent Tool Prompt Layering 中关于“双重导入已修复”的记录。

## 4. P1 问题

### P1-1 缺少统一 EntryMutation 契约，导致 TM、持久化、AI、后处理的 stage 语义互相破坏

- `TranslationMemoryManager.apply_to_collection()` 只写 `e.translation`，不改 stage，见 `src/transbridge/translation_memory/manager.py:333-364`。
- Translation Memory Story 04 甚至明确写“stage 不变”，但 FR2.5 规定有译文的数据源映射为 stage=1；这是 Plan 内部语义冲突。
- FOMOD 声称优先级为“旧包 → 词典 → AI”，但 TM 命中后 stage 仍是 0；AutoTranslator 的候选条件会再次选中它，导致 AI 覆盖词典结果。
- Variant 恢复也有同样问题：translation 恢复但 stage=0，AI 再翻。
- AI 翻译通过 `collection.add()` 替换对象，见 `translator.py:973-1003`；后处理部分路径直接修改 `entry.translation/stage`，见 `post_processor.py:750-805`；TM/迁移又直接就地修改，通知、dirty、索引和线程语义不统一。
- `AutoTranslator` 的协议已经把 `target_entry_ids` 实际当 key 使用，候选也按 `e.key`，但 `_update_collection()` 又调用 `get_by_id()`，动态术语提取还用 `entry.id in id_to_translation`，见 `translator.py:366-368,813-835,983,1014,1032-1033`。目前多数数据 id==key 掩盖了违约；一旦二者分离就静默漏更新。

**目标共享服务**：

```text
TranslationPatch(
  key,
  translation,
  stage,
  provenance,
  expected_revision=None,
  reason=""
)

EntryMutationService.apply_batch(collection_id, patches)
  -> MutationResult(updated, conflicts, rejected)
```

服务负责：key 主索引、stage 转换、不可变替换、revision 冲突检测、一次性 collection_changed/dirty、主线程提交、审计 provenance。AI/TM/Persistence/Migrator/Editor/PostProcessor 全部只产生 patch，不直接改 Entry。

**粗略工作量**：5-8 人日，迁移调用方 5-8 人日。

**需更新**：Stage Unification、AI Translation Story 05、AI Post Process Story 07/12、Translation Memory Story 04、Project Persistence Story 01、Agent Tool Expansion Story 23。

### P1-2 SessionController 的 AWAITING_TASK 协议未接上，长任务完成会在错误状态回调

- `SessionController` 定义 `handle_task_started()`，要求 EXECUTING → AWAITING_TASK，见 `src/transbridge/smart_assistant/session_controller.py:184-203`。
- 全仓没有任何调用 `handle_task_started()` 的代码。
- 自动模式中 `_dispatch_steps()` 返回后立即调用 `handle_execution_complete([])`，见 `session_controller.py:134-142`；长任务工具只返回 task_id，后台仍在运行，此时 Controller 已进入下一轮 THINKING。
- TaskManager 完成时 `ChatWidget._on_task_completed()` 无条件调用 `controller.handle_task_completed()`，见 `chat_widget.py:774-800`；该方法断言当前必须是 AWAITING_TASK，因此会触发 AssertionError。TaskManager 的 callback 隔离会把异常降为 warning，造成“任务做完但会话不继续”的静默故障。
- `assert` 被用作生产状态校验；Python `-O` 下会被移除，使非法转换继续发生。

**目标方案**：工具返回类型增加 `execution=sync|async` 和 `task_id`；ToolExecutionService 在返回异步句柄时发出 `TaskStarted` 事件，Controller 订阅 typed event 并持有 `awaited_task_ids`。生产状态转换用显式 `InvalidTransition`，不用 assert。

**需更新**：Session Controller Plan、Task Monitor Plan、Agent Tool Expansion 长任务 Story、TaskManager 契约。

**粗略工作量**：3-5 人日。

### P1-3 Session 启动恢复只有 UI 历史，没有恢复 LLM 对话状态

- Panel 启动恢复调用 `chat.load_history(data["messages"])`，见 `src/transbridge/ui/tools/smart_assistant/panel.py:111-125`。
- `load_history()` 只渲染 MessageBubble；真正恢复 `ConversationManager.from_dict()` 的是 `ChatWidget.load_session()`，见 `chat_widget.py:1140-1169`。
- 结果是重启后用户看到历史消息，但下一轮 LLM 的 conversation 仍为空，FR13“恢复会话”只实现了视觉层。
- 会话切换调用 `controller.handle_abort()`，但不会取消或重新归属 TaskManager 中仍在运行的任务；旧会话任务完成后会把 observation 写进当前会话。
- TaskManager 是全局单例，TaskHandle metadata 没有强制的 session/project/variant/slot owner；并行任务也没有资源锁。

**目标方案**：`SessionRuntime` 聚合 `conversation_state + controller_state + owned_task_ids`。恢复必须走单一 `load_session()`；任务创建必须绑定 `TaskOwner(session_id, project_id, variant_id, slot_id)`，切换会话时选择 cancel/detach，完成事件只投递给 owner。

**需更新**：Session Manager Story 03、Session Controller、Task Monitor FR14、TaskManager metadata 约束。

**粗略工作量**：3-5 人日。

### P1-4 Graph pause/checkpoint 代码存在，但不满足恢复语义

- `execute_graph()` 初始化时 `_paused.clear()`，循环里却在 `_paused.is_set()` 时调用 `_paused.wait()`；Event 已 set 时 wait 立即返回。`pause()` 设置 Event、`resume()` 清除 Event，见 `src/transbridge/smart_assistant/graph_executor.py:458-476,602-605`，实际 pause 不会阻塞。
- checkpoint load 得到 completed 集合后，pending 仍从 entry_node 开始；`_bfs_one_level()` 不按 completed 跳过节点，见 `graph_executor.py:464-485,408-443`。恢复后可能重跑已完成副作用。
- linear/DAG graph_id 使用 `hash(str(steps))`，见 `graph_executor.py:524,594`；Python hash 跨进程随机化，重启后无法定位原 checkpoint。

**目标方案**：统一 `CancellationToken/PauseToken`；checkpoint 保存稳定 graph definition hash、node output 和 source/config revision；恢复时从未完成 frontier 继续，并对 write/admin 节点使用 idempotency key。

**需更新**：Agent Upgrade Graph Story 09/10、ADR-011、相关 checkpoint 测试。

**粗略工作量**：4-7 人日。

### P1-5 后台线程直接修改共享 collection，`safe_mutate` 只负责最后通知

- AutoTranslator 在 ThreadPoolExecutor worker 中调用 `_update_collection()` 并写入共享 collection，见 `translator.py:538-617,973-1003`。
- Agent TranslationController 又在独立 daemon thread 中运行整个 translator，见 `tool_translator.py:97-156`。
- 完成时 `ctx.safe_mutate(lambda: notify_collection_modified())` 只把通知放回主线程，实际数据写入早已在后台发生，见 `tool_translator.py:126-146`。
- 润色路径同样在后台直接 `collection.add()`，且重建 Entry 时漏掉 `string_id/form_id_with_plugin/dsd_* /editor_id`，见 `tool_translator.py:236-253`，会丢失写回所需元数据。
- 当前允许并行多个 translation/polish/postprocess 任务作用于同一 collection，没有 slot/variant 资源互斥，也没有 revision 冲突检测。

**目标方案**：worker 仅计算 patch；主线程通过 EntryMutationService 提交。TaskService 对 `(project,variant,slot)` 申请 read/write lease；翻译和润色的 write lease 默认互斥。

**需更新**：AI Translation、AI Post Process、Agent Tool Expansion Story 06/10/25、ADR-008 线程边界。

**粗略工作量**：与 EntryMutationService 合并估算 5-8 人日。

### P1-6 FOMOD 会把部分失败报告成“翻译完成”，且声明的优先级实际不成立

- `_ai_translate()` 捕获所有异常并返回 0，见 `src/transbridge/fomod/pipeline.py:150-168`。
- `_write_back()` 捕获所有异常后 `pass`，见 `pipeline.py:170-183`。插件可能完全未写回，但流水线仍继续组装打包。
- UI 完成回调固定显示“翻译完成”，见 `src/transbridge/ui/tools/fomod/fomod_panel.py:231-244`；`PipelineResult` 没有 errors/partial 状态。
- `extract()` 的返回值被丢弃，`PipelineResult.extracted_count` 永远保持 0，见 `pipeline.py:66-75`。
- 使用 `tempfile.mkdtemp()` 后没有 finally cleanup，大型安装包会长期残留临时目录，见 `pipeline.py:62-70`。
- GUI 的 `target_lang` 只传给 ModuleConfig XML；插件 AI 使用 `LLMConfig.target_lang`，localized strings 写回又硬编码 `language="english"`，见 `pipeline.py:87-89,158-165,173-180`。界面选择的目标语言没有贯穿插件翻译/输出。
- TM 命中不更新 stage，随后 AI 会再次选择并覆盖，破坏文件头注释声明的“旧包→词典→AI”优先级。

**目标方案**：FOMOD 改为显式阶段状态机；每阶段返回 `StageResult(status, artifacts, errors, warnings)`，默认 fail-fast，允许用户显式选择 partial。临时目录用 context manager；最终 archive 先写临时文件，全部成功后原子替换。target_lang 作为 PipelineRunConfig 单一来源贯穿 AI、XML、strings 命名。

**需更新**：FOMOD Story 03/04、Translation Memory Story 04、Fileops Archive Story 01。

**粗略工作量**：4-6 人日。

### P1-7 AI checkpoint/run identity 只按文件 stem，无法安全并发或跨项目恢复

- AI 数据目录只使用 `esp_stem`，见 `src/transbridge/config/llm.py:271-275`；不同目录下同名插件共享 checkpoint、动态术语和缓存。
- ProgressCheckpoint 直接覆盖 JSON，不使用项目已有的原子写工具，见 `src/transbridge/ai_translator/translator.py:75-92`。
- checkpoint fingerprint 只保存批次 key 集合和累计计数，没有 source content hash、variant、模型、prompt/config hash。修改源文件或配置后继续，仍可能跳过旧批次。
- 允许多个任务同时处理同一 esp 时会写同一个 progress/post-process 文件。

**目标方案**：新增 `RunStore`，run_id 至少包含 project_id、variant_id、source fingerprint、pipeline version、config/prompt hash；checkpoint 原子写并加进程内资源锁。旧 stem 目录只读迁移一次。

**需更新**：AI Translation Story 05、AI Post Process checkpoint Story、Project Persistence source fingerprint、FOMOD Pipeline。

**粗略工作量**：3-5 人日。

## 5. P2/P3 问题

### P2-1 Translation Memory 命中统计不持久化且 UI 套用未走统一通知

- query 会增加 key/text index 的 hits，见 `translation_memory/manager.py:183-203,241-287`；DictionaryPanel 套用后没有 `manager.save()`，见 `dictionary_panel.py:263-300`，命中排序下次启动丢失。
- UI 套用直接修改 collection，未触发 `collection_changed/dirty`；表格刷新与自动保存依赖后续偶然事件。
- Story 04 仍写“key 优先匹配 entry.id”，而代码和 ADR-002 已改为 entry.key；Plan 需同步。

### P2-2 文件归档契约缺少配额、统一成员校验与结构化诊断

- ZIP 的 containment 使用字符串 `startswith`，见 `src/transbridge/fileops/archive.py:87-90`，存在路径前缀边界缺陷。
- 7z/RAR 分支未在应用层统一校验成员路径；安全性依赖具体第三方版本。
- 所有格式缺少文件数、总解压大小、单文件大小、压缩比和超时限制，FOMOD 可被资源耗尽。
- `extract()`/`pack()` 返回结构不统一：extract 返回 dict，pack 返回 str；上层无法统一表达 partial、warnings、artifacts、cleanup。

建议并入 `ArchiveService`：`inspect()` 先生成 manifest 并校验配额；`extract()` 使用已验证 manifest；返回 typed result。

### P2-3 全局 Controller/Registry 初始化隐藏依赖

- editor/proofreader/translator 工具使用模块级 `_ctrl` 并在惰性初始化时创建新的 `AppContext()`，见 `tool_editor.py:376-385`、`tool_proofreader.py:414-423`、`tool_translator.py:576-585`。
- 这些隐藏 AppContext 会读取配置、创建 QObject，与真正 UI context 并非同一对象；当前仅因 controller 大多使用每次注入的 ExecutionContext 才没有立即暴露。
- `tools.register_all()` 名称称“无导入副作用”，实现却通过 import 触发每个模块底部注册副作用，见 `smart_assistant/tools/__init__.py:19-35`。

建议创建应用级 `ServiceContainer`，显式构造 ToolCatalog、TaskService、SourceDocumentService、EntryMutationService、ProjectRepository；注册函数返回 ToolSpec 列表，不在 import 时改变全局状态。

### P3-1 Plan 的“已完成/已方案”不能代表真实验收

- `plans/session-manager/plan.md` 标记“全部完成”，但启动恢复没有恢复 ConversationManager。
- `plans/session-controller/plan.md` 标记“全部完成”，但 `handle_task_started` 没有调用方。
- Agent Tool Story 19/23/24 为“已方案”，源码已有实现但调用契约未验收；Story 12 仍描述 6 parser/4 writer，与后续合并后的实际工具目录不一致。
- Agent Upgrade Story 12 的验收勾选仍为空，却已有代码和 changelog；代码仍缺可用启动链。
- Translation Memory Story 04 的“stage 不变”与 FR2.5、FOMOD 优先级、AI 候选筛选冲突。

建议以后将状态拆成：`implemented`、`contract-tested`、`release-smoke-tested`、`accepted`，不能只凭文件存在或单元测试数量写“全部完成”。

## 6. 建议的目标共享服务架构

最小目标不是大规模重写，而是把当前重复且互相矛盾的入口适配收束为 7 个共享契约：

1. **SourceDocumentService**：统一 parser/writer、源描述、writer context、文件指纹。
2. **EntryMutationService**：唯一的 translation/stage/labels 修改入口；key 主索引、revision、provenance、主线程提交。
3. **ProjectStateRepository**：workspace/project/variant schema、replace 加载、迁移、source fingerprint。
4. **TaskService**：任务 owner、资源 lease、pause/cancel、typed lifecycle event；取代匿名全局 TaskManager 使用方式。
5. **ToolExecutionService**：GUI/Graph/MCP 共享的参数校验、权限、HITL、执行和结果规范化。
6. **RunStore**：AI/PostProcess/Graph checkpoint 的稳定 run identity、原子存储、幂等恢复。
7. **ArchiveService**：manifest、路径安全、配额、临时工作区、结构化错误与原子产出。

服务之间的建议依赖方向：

```text
GUI / Agent / MCP / FOMOD
        |
        v
ToolExecutionService / PipelineCoordinator
        |
        +--> SourceDocumentService --> parser/writer adapters
        +--> EntryMutationService  --> CollectionStore
        +--> ProjectStateRepository
        +--> TaskService -----------> RunStore
        +--> ArchiveService
```

PyQt 只负责主线程 dispatcher 和 UI 回调。领域服务不得 import QWidget/QObject；后台 worker 不直接写 collection。

## 7. 渐进迁移顺序

### 阶段 0：建立可失败的契约测试（2-3 人日）

- 新增 clean-wheel install smoke test。
- 为 ESP/EET/XT/SST/JSON 建立 `parse → slot context → write → reparse` 契约测试。
- 建立 Variant A/B 隔离、清空译文、恢复 stage 测试。
- 建立长任务状态转换、MCP enabled 启动、FOMOD partial failure 测试。

这些测试先红，用来防止修复过程中继续按 mock 假接口编码。

### 阶段 1：P0 热修（3-5 人日）

- 修复 console entry 与包导入体系的最小启动链。
- Agent parser/writer 暂时直接适配真实构造签名并保存完整 slot 上下文。
- Variant 切换前重置可变状态；恢复时同步 stage；清空译文能删除持久化旧值。
- MCP enabled 分支在没有完整 runtime host 时安全拒绝启动，不得让 GUI 崩溃。

### 阶段 2：EntryMutation + ProjectState（8-12 人日）

- 先迁移 Persistence/TM/Migrator，再迁移 AI/PostProcess/Agent Editor。
- current.json v2 key 化、source fingerprint、provenance/revision；提供 v1 migration。
- 所有后台结果改成 patch，主线程一次提交。

### 阶段 3：SourceDocument + Archive/FOMOD（8-12 人日）

- parser/writer adapter 与 UI Step1/WriteCard 共用。
- Agent 工具只做 request/response 转换。
- FOMOD 使用同一 parse/write 服务和 ArchiveService；去掉吞异常。

### 阶段 4：Task/Session/Graph（7-10 人日）

- 引入 TaskOwner、lease、typed event。
- 接通 AWAITING_TASK；修复 session 恢复和跨会话任务隔离。
- 修复 Graph pause、稳定 graph_id、checkpoint frontier/idempotency。

### 阶段 5：MCP runtime 与发布门禁（5-10 人日）

- 明确 headless 或 desktop-control 模式。
- MCP 走统一 ToolExecutionService，不自行发明权限旁路。
- wheel/PyInstaller/Windows smoke test 纳入发布门禁。

**总体估算**：单人约 30-45 人日；2 人并行且保持明确模块边界时约 4-6 周。若选择 GUI IPC 型 MCP，额外增加约 3-6 人日。

## 8. 跨 Epic Plan 调整建议

建议不要把这些问题继续分散写回原 8 个 Epic。先增加 3 个横向 Epic/ADR，再让原 Story 依赖它们：

### Epic A：Domain Service Contracts

- A1 SourceDocumentService + adapters
- A2 EntryMutationService + stage/provenance/revision
- A3 ToolExecutionService + channel policy
- A4 ServiceContainer/显式注册

### Epic B：State and Task Reliability

- B1 Variant schema v2 + id→key migration
- B2 source fingerprint + immutable baseline
- B3 TaskOwner/resource lease/lifecycle events
- B4 RunStore/checkpoint/idempotency
- B5 Session runtime restore/isolation

### Epic C：Release and External Runtime

- C1 import/package normalization
- C2 dependency/resource manifest
- C3 wheel/PyInstaller smoke gate
- C4 MCP runtime mode and authentication
- C5 Archive quotas/transactional FOMOD output

原 Plan 中对应 Story 应标注 `blocked_by` 或 `superseded_by`，避免同一接口在多个 Epic 中重复设计。

## 9. 必须具备的验收点

### 发布

- 从源码构建 wheel，在空 venv 安装后 `import transbridge`、console script 启动成功。
- 业务包扫描不到 `src.transbridge` 导入；版本只有单一来源。
- 7z/RAR 依赖和 PyInstaller 资源在 clean machine 可探测，缺失时显示能力降级而非 ImportError。

### Parser/Writer

- 每种格式使用真实最小样本完成 parse；结果是 `TranslationEntryCollection`，不是原 parser entry。
- create_slot 后 SourceDescriptor、path、plugin/parser/lookup 上下文完整。
- EET/XT 必须区分 source template 与 output path；写回后重新解析，译文与 stage 一致。
- 合法绝对路径在允许根内可用；`..`、符号链接逃逸和根外路径被拒绝。

### Variant/TM/AI

- A 版本有译文、B 版本无译文：切换到 B 后必须为空，切回 A 恢复。
- 清空译文后保存、重启，仍为空。
- 恢复译文或 TM 命中后 stage 与来源策略一致，不会被 overwrite=False 的 AI 再翻。
- id != key 的构造样本下，AI/TM/Persistence/Agent Editor 都按 key 工作。
- 同一 slot 同时启动翻译和润色时，第二个任务被排队/拒绝，而不是竞争写入。

### Task/Session/Graph

- 长任务返回 task_id 后 Controller 进入 AWAITING_TASK；完成/失败都能回到合法状态。
- 切换会话时旧任务完成结果不会写进新会话。
- 重启恢复最近会话后，下一轮 LLM 能看到恢复历史，不只是 UI 显示历史。
- Graph pause 后节点不再开始；resume 后继续。
- 进程重启后从 checkpoint 恢复，不重跑已完成 write/admin 节点。

### MCP

- `mcp_enabled=true` 不影响 GUI 启动。
- 认证 token 来自明确的 secret/config source，不通过临时 stderr 才能获知。
- tools/list 与一个只读 tools/call 使用真实 context 成功。
- write/admin 在无 capability/confirmation token 时稳定拒绝；授权后行为与 GUI 通道一致。

### FOMOD/Archive

- 任一 plugin write 失败时结果为 failed/partial，UI 不得显示“翻译完成”。
- extracted_count、processed、skipped、failed、artifacts 数值真实。
- target_lang 对 AI、ModuleConfig、localized strings 一致。
- 临时目录在成功、失败、取消三种路径均清理。
- 恶意成员路径、超文件数、超解压体积和高压缩比样本被拒绝；最终 archive 仅在全部必需阶段成功后原子发布。

## 10. 最终判定

**当前不建议以“需求均已实现”作为下一轮迭代基线。** 更准确的状态是：大量领域代码已经存在，但若干关键入口尚未通过真实调用契约，Variant/Stage/Task/Checkpoint 仍缺跨模块一致性。

推荐先完成阶段 0-1，并把 P0 验收全部纳入发布门禁；随后以 EntryMutation、SourceDocument、ProjectState、TaskService 四个共享契约为主线调整架构和 Plan。这样可以保留现有 parser/writer/AI/TM 实现的大部分价值，同时停止在 GUI、Agent、MCP、FOMOD 四套入口上重复修补同类问题。

## 11. ParaTranz JSON 合同修订

用户样本为 `id/key/original/translation/stage/context` 数组；数值 `id` 由 ParaTranz 生成，`key` 是用户原始 ID。实现契约需要补充：

- `SourceDocumentService` 注册独立 `ParaTranzJsonAdapter`；
- 导入以 key 生成/匹配 EntryKey，remote id 进入 scoped external reference；
- remote id 的变化不得影响 Collection、Variant、TM、AI、labels 或 writer 的本地关联；
- 缺 key 是 identity error，不能用 remote id 静默兜底；
- 在线 Downloader 和离线 adapter 对同一 payload 生成同构 ChangeSet；
- 导出可以不携带未知 remote id，但必须始终携带 stable key；remote id 的输出策略需经真实 ParaTranz contract test 冻结；
- 重复 key、重复 external id、未知 Stage 都进入 typed issue list，禁止最后一条静默覆盖。

该兼容切片应从原阶段 3 前移到阶段 0，与 Parser/Writer contract test 一起完成。完整说明见 [专项调整](paratranz-json-compatibility-adjustment.md)。

---

## 整改回填（2026-08-18，Phase 6）

本报告为综合整改正式输入审查结论，保留历史判定与证据不改写。Phase 0～7 已完成，对应根因（R-xxx）由各 V2 Plan/Story 承接并通过 EvidenceManifest 与综合 QA；完整根因→Story→evidence 追踪见 [remediation-ledger](./remediation-ledger.md)，最终汇总见 [final-release-qa-2026-08-18](./final-release-qa-2026-08-18.md)。综合整改 V2 共 37/37 Story 实现完成并通过综合 QA；最终锁定 uv 门禁合计 1374 passed、5 skipped、0 failed。
