# Embedding 服务与本地模型管理

**状态**: 已完成
**对应需求**: [FR5.14](../../docs/requirements.md)
**相关 ADR**: [ADR-030](../../docs/adr/030-independent-embedding-and-managed-local-models.md)、[ADR-013](../../docs/adr/013-vector-retrieval-enhancement.md)、[ADR-020](../../docs/adr/020-high-performance-ui-foundation.md)
**业务域**: AI 翻译 / 语义检索 / 服务配置 / 本地模型生命周期

## 目标

- 彻底移除 Embedding API 对主 LLM provider、model、Base URL 与 API Key 的隐式继承或回退。
- 让 Embedding API 面板在信息层级、校验、连接检查和反馈上与主 LLM 配置保持一致，同时仍是独立配置。
- 取消本地推理路径中的自动模型下载，提供应用内预设模型的下载、取消、重试、选择和安全删除。
- 当用户主动需要本地语义检索却没有可用模型时，提供“关闭语义检索 / 前往模型配置”的引导闭环。
- 保持 disabled 模式和非语义检索旅程可用，并让模型切换继续遵守既有索引 fingerprint 失效合同。

## 实施与验收结果

- Embedding API 的 provider、model、Base URL、API Key、连接检查和运行预检已与主 LLM 完全解耦；OpenAI 预设仅在用户主动选择且字段为空时填充 Embedding 自身建议值。
- 本地模型改为稳定 model ID、固定 revision、完整安装 manifest 和 local-only 推理；应用启动、预检与正式运行均不承担下载。
- 模型管理器已覆盖异步下载、取消、重试、选择、当前模型删除确认和受管目录安全删除；下载失败或不完整快照不会发布为已安装。
- 无模型引导已覆盖主动选择与运行触发；关闭按钮、Esc 和“关闭语义检索”统一持久化 `disabled`，历史无效本地配置在启动时静默归一化为关闭。
- 聚焦配置、客户端、存储、UI、索引和模块边界测试通过；本轮涉及文件 Ruff check 与 format check 通过。全仓测试中的本轮主窗口边界失败已修复并单独复验，剩余全仓失败属于既有性能隔离和 Smart Assistant 测试问题。

## 非目标

- 不改变 FR5.12 的精确匹配、BM25、向量融合、阈值和 Top-K 算法。
- 不在本轮加入在线模型市场、任意 Hugging Face 仓库输入或通用文件夹模型导入器。
- 不随安装包捆绑任一模型，也不在应用启动时后台预下载。
- 不自动开始术语索引构建、AI 翻译或其他业务运行。
- 不重做主 LLM 配置或统一配置仓库，只复用其产品语言和既有原子持久化边界。

## 当前实现事实与关键约束

- src/transbridge/config/llm.py 已有独立 EmbeddingConfig 与 TransBridge.Embedding credential reference，但 src/transbridge/infra/embedding_client.py、config_presenter.py 和 run_spec.py 仍存在向主 LLM Key/Base URL 回退的兼容逻辑。
- LocalSentenceTransformerClient 当前直接将 local_model_path 或模型名称交给 SentenceTransformer；仓库名可在首次加载时触发 Hugging Face 网络访问。
- 既有 plans/embedding-retrieval-ux-reliability/plan.md 已完成三态配置、独立检查、预检和索引身份，本计划承接其中明确列为后续轮次的模型下载与管理能力。
- 当前工作树已出现初始 embedding_model_store.py、embedding_config_view.py、embedding_model_dialog.py 及聚焦测试切片；它们是本轮实施落点，不因文件已存在就视为完成，仍需按 FR5.14/ADR-030 收敛并验收。
- 所有下载、扫描、模型初始化和删除不得阻塞 Qt 事件循环；应用退出与窗口关闭必须安全收敛后台线程。
- API Key 继续只通过 credential store/environment 解析；配置、日志、下载状态、catalog、manifest 和索引身份不得包含明文秘密。
- 删除只允许受管模型根目录内、由 catalog/model ID 解析出的目标；不得把 UI 文本或任意路径直接用于递归删除。
- 遵守仓库文件/类责任阈值。Embedding UI 已从 config_view.py 提取时，不得把下载器、文件系统策略或引导状态重新塞回主窗口类。

## Story 1：Embedding API 独立配置与产品对齐

### 验收标准

- Embedding API 面板单独提供 mode、provider、model、Base URL、API Key、密码显示语义和连接检查；布局、提示与状态反馈和主 LLM 配置一致。
- 保存、加载、自动保存与运行快照保持 Embedding 自身字段；修改 Embedding 不改动主 LLM，反之亦然。
- Embedding Key、Base URL、provider 或 model 缺失时，在网络请求前给出定位到 Embedding 面板的错误。
- create_embedding_client()、显式连接检查和 preflight_ai_run() 均不读取主 LLM 对应字段作为 fallback。
- Embedding 与主 LLM 填入相同值时仍生成各自配置与凭据引用；清空 Embedding 字段不会因主 LLM 有值而通过。

### 文件落点

- 修改 src/transbridge/config/llm.py
- 修改 src/transbridge/infra/embedding_client.py
- 修改 src/transbridge/ui/tools/ai_translator/embedding_config_view.py
- 修改 src/transbridge/ui/tools/ai_translator/config_view.py
- 修改 src/transbridge/ui/tools/ai_translator/config_presenter.py
- 修改 src/transbridge/ui/tools/ai_translator/run_spec.py
- 修改 tests/contracts/config/test_unified_repository.py
- 修改 tests/infra/test_embedding_client.py
- 修改 tests/ui/tools/test_embedding_retrieval_config.py
- 修改 tests/ui/tools/test_ai_translator_story08.py

### 实施步骤

1. 删除 Presenter、预检和 client factory 中所有 Embedding → LLM fallback，只接受 EmbeddingConfig 的显式值。
2. 在独立 Embedding View 切片中使用稳定 item data 映射 provider/mode，提供自身 placeholder、错误定位与测试动作。
3. 保持统一 repository 的一次原子保存，并验证主 LLM 与 Embedding credential reference 分离。
4. 为配置 round-trip、清空字段和同值但独立身份增加回归测试。

### 测试策略

- 参数化覆盖 disabled/local/api 的加载、保存和显隐。
- 使用互不相同的主 LLM/Embedding 值验证 client 参数；再将 Embedding 必填字段置空，断言不发生网络调用。
- 验证 INI、异常文本和 repr 不包含 API Key。

## Story 2：版本化预设目录与受管模型存储

### 验收标准

- 应用内至少提供一组版本化预设，每项有稳定 ID、显示信息、固定来源 revision、预估大小、维度/运行信息和推荐标记。
- 模型根目录位于 get_data_dir()/models/embedding；列表能区分未安装、完整安装和无效/不完整目录。
- 下载写入任务专属 staging，完成校验后原子发布；失败、取消和无效快照不会成为 installed，也不覆盖其他安装。
- 完整安装写入版本化 manifest；运行路径只由已知 model ID 和有效 manifest 解析，不能由任意 UI 路径指定。
- 下载器只在显式 download use case 中访问网络；本地 client 使用 local-only 加载。
- 删除未知 ID、符号链接、根目录自身、越界路径或无效解析目标时 fail closed；不同任务/模型的文件保持不变。

### 文件落点

- 新增/修改 src/transbridge/infra/embedding_model_store.py
- 修改 src/transbridge/config/paths.py（仅在需要集中模型根路径时）
- 修改 src/transbridge/infra/embedding_client.py
- 新增/修改 tests/infra/test_embedding_model_store.py
- 修改 tests/infra/test_embedding_client.py
- 修改 tests/security/test_dependency_degraded.py（仅验证 disabled/local-only 能力边界）

### 实施步骤

1. 冻结 preset 与 install manifest schema，记录稳定 ID、来源 revision 和无秘密 fingerprint 输入。
2. 将 catalog 查询、状态扫描、staging 下载、完整性校验、原子发布和安全删除收敛到 store。
3. 为并发目标已存在、取消、磁盘/下载异常、残留 staging 和越界 ID 定义确定错误语义。
4. 让本地 Embedding client 只接收 store 已验证路径，并显式启用 local-only，移除仓库名自动解析。

### 测试策略

- 使用临时目录与 fake downloader 验证成功、幂等、取消、异常、无效快照、并发目标和残留隔离。
- 覆盖父目录跳转、符号链接、模型根目录、未知 ID 等删除负路径。
- patch SentenceTransformer 断言传入本地路径与 local-only 选项，且 client 构造不会调用下载器。

## Story 3：模型管理 UI 与后台下载生命周期

### 验收标准

- 本地模式显示当前模型摘要和“管理本地模型”入口，不再把仓库名/路径自由文本作为主要操作。
- 管理界面展示预设名称、说明、维度/特性、预估大小、推荐、安装和当前使用状态。
- 用户可下载、取消、失败后重试、在已安装模型间切换和删除；进行中动作禁用冲突操作。
- 下载运行在线程中，UI 保持响应；有可靠总量时显示确定进度，否则显示不伪造百分比的不确定进度与真实阶段文案。
- 下载成功后自动选择模型并持久化稳定 model ID；不自动运行翻译或构建索引。
- 关闭管理窗口或退出应用时，不遗留运行中的 QThread；取消至少在下载器可中断的安全点生效，半成品不发布。

### 文件落点

- 新增/修改 src/transbridge/ui/tools/ai_translator/embedding_model_dialog.py
- 新增/修改 src/transbridge/ui/tools/ai_translator/embedding_config_view.py
- 修改 src/transbridge/ui/tools/ai_translator/view_controls.py
- 修改 src/transbridge/ui/tools/ai_translator/view_state.py
- 修改 src/transbridge/ui/tools/ai_translator/config_view.py
- 修改 src/transbridge/ui/tools/ai_translator/ai_translator_window.py
- 新增/修改 tests/ui/tools/test_embedding_model_dialog.py
- 修改 tests/ui/tools/test_embedding_retrieval_config.py

### 实施步骤

1. 用 store snapshot 驱动列表，View 不直接读取目录或调用 Hugging Face。
2. 用专用 worker/controller 管理一个下载任务的开始、进度、取消、成功、失败和释放。
3. 将选择结果提交给 ConfigPresenter，由统一 repository 保存；切换后刷新摘要与预检。
4. 对当前模型删除执行二次确认，先 disabled 并持久化、释放引用，再调用受管删除。
5. 使用 ThemeService/ComponentStyle 与无障碍文本对齐现有产品，不新增局部硬编码主题作为最终样式。

### 测试策略

- Qt 测试覆盖列表状态、动作 enablement、下载成功/失败/取消、选择、删除确认和关闭时 worker 生命周期。
- 用 event loop 等待线程信号，不使用真实网络或脆弱固定 sleep。
- 验证模型管理操作不触发 AI 运行或索引构建回调。

## Story 4：无模型引导与持久化关闭语义

### 验收标准

- 用户主动把 mode 切到 local 且无有效选择时显示引导；用户启动需要本地语义检索的任务而选择无效时也显示同一引导。
- 应用普通启动、载入设置、自动保存和不依赖语义检索的旅程不弹引导。
- 引导明确说明语义检索不可用且普通翻译/字面术语匹配不受影响。
- “关闭语义检索”、右上角关闭、reject 与 Esc 走同一命令并持久化 mode=disabled。
- “前往模型配置”打开并定位模型管理区；用户退出且仍无有效选择时 mode 保持 disabled。
- 下载并选择有效模型后才持久化 local；再次运行不重复弹引导。

### 文件落点

- 修改 src/transbridge/ui/tools/ai_translator/embedding_model_dialog.py
- 修改 src/transbridge/ui/tools/ai_translator/embedding_config_view.py
- 修改 src/transbridge/ui/tools/ai_translator/config_presenter.py
- 修改 src/transbridge/ui/tools/ai_translator/ai_translator_window.py
- 修改 src/transbridge/ui/tools/ai_translator/run_controller.py
- 修改 src/transbridge/ui/tools/ai_translator/run_spec.py
- 新增/修改 tests/ui/tools/test_embedding_model_dialog.py
- 修改 tests/ui/tools/test_embedding_retrieval_config.py
- 修改 tests/ui/tools/test_ai_translator_story08.py

### 实施步骤

1. 把“请求本地模式”与“已持久化 local”分开：先验证选中安装，再提交配置。
2. 集中实现 guide decision，确保 window close/Esc 不绕过 disable 持久化。
3. 在 run preflight 返回可识别的 local-model-missing code，由 Controller 映射到同一引导，而不是复制第二套弹窗。
4. 引导转入 manager 后，按最终 selection 决定 local 或 disabled 并刷新运行预检。

### 测试策略

- 覆盖主动选择、运行触发、启动不触发三条路径。
- 分别模拟 disable、close、Esc、configure 后取消、configure 后成功，验证 repository round-trip。
- 验证 disabled 后 translation preflight 不探测本地依赖或模型。

## Story 5：迁移、索引身份与综合门禁

### 验收标准

- 既有显式 Embedding API 值和独立 credential reference 保留；过去仅靠 LLM fallback 的配置变为可定位的不完整状态。
- 旧自由文本 local_model_path 不触发网络；不能映射到完整受管安装时迁移为 disabled，并仅在事件触发时引导。
- 选中 preset ID/revision/维度进入索引 fingerprint；模型切换、升级或删除使旧索引 stale，同一安装仍可跨会话复用。
- disabled 模式不导入/初始化 sentence-transformers、模型或 FAISS，也不清理用户数据。
- 本轮相关 UI、配置、client、store、索引与安全测试通过；Ruff check/format check 对涉及范围通过。

### 文件落点

- 修改 src/transbridge/config/llm.py
- 修改 src/transbridge/ai_translator/term_vector_manifest.py
- 修改 src/transbridge/ai_translator/term_vector_index.py（仅必要的身份接线）
- 修改 src/transbridge/infra/embedding_client.py
- 修改 src/transbridge/ui/tools/ai_translator/run_spec.py
- 修改 tests/contracts/config/test_unified_repository.py
- 修改 tests/ai_translator/test_term_vector_manifest.py
- 修改 tests/ai_translator/test_term_vector_index.py
- 修改 tests/infra/test_embedding_client.py
- 修改 tests/ui/tools/test_embedding_retrieval_config.py

### 实施步骤

1. 定义旧配置读取归一化与一次迁移，避免保存时重新引入 fallback。
2. 用 catalog ID/revision 扩展无秘密 embedding identity；保留 ADR-013 的旧 manifest 安全重建行为。
3. 审计所有 embedding-to-LLM fallback 和 SentenceTransformer(repo_name) 路径。
4. 执行聚焦测试、相关 UI 套件和 Ruff 门禁，复核 diff 不包含凭据、临时模型或下载缓存。

### 测试策略

- 旧 API 配置、旧 local 模型名、现行受管模型三类迁移夹具。
- 同模型复用、model ID/revision/维度变化、旧 manifest 缺字段的索引回归。
- 在无网络测试环境运行除显式 fake downloader 外的完整相关测试，证明普通测试和应用构造不下载模型。

## 依赖顺序

1. Story 1 与 Story 2 可并行：前者冻结独立 API，后者冻结本地模型存储。
2. Story 3 依赖 Story 2 的 store snapshot、下载与删除合同。
3. Story 4 依赖 Story 1 的权威 mode 语义和 Story 3 的模型管理入口。
4. Story 5 在前四个 Story 收敛后执行迁移、索引与综合验收。

## 风险与回退

- Hugging Face 下载器不一定提供精确总量或立即取消：UI 必须使用不确定进度，不得伪造平滑百分比；取消在可控安全点生效，staging 绝不发布。
- Windows 上已加载模型可能占用文件：删除当前模型必须先 disabled、释放 client；仍被占用时保留安装并报告重试，不做强制递归删除。
- preset revision 或过滤规则错误可能得到不完整模型：manifest 发布前执行运行入口完整性检查，失败只清理本任务 staging。
- 从 fallback 切到严格独立配置会暴露历史空字段：保留显式值并提供面板定位，不自动复制主 LLM secret。
- 若 UI 新切片与并行中的 AI 翻译窗口改动重叠，保留 View/Presenter/Controller 边界并最小接线，不覆盖无关工作流改动。
- 回退本功能时只将 Embedding 设为 disabled 并隐藏管理入口；不得自动删除已下载模型或旧索引。

## 明确假设

- 首轮预设由项目维护的静态 catalog 提供，不需要远程刷新 catalog。
- 本轮主流程只支持应用管理的预设模型；高级自定义目录导入以后续需求处理。
- 下载完成可自动选择，但从未自动开始翻译或索引构建。
- 引导的 close/Esc 等同“关闭语义检索”已经由用户确认，不再保留仅关闭本次提示的状态。

## 建议验证命令

- uv run pytest tests/infra/test_embedding_client.py tests/infra/test_embedding_model_store.py -q
- uv run pytest tests/ui/tools/test_embedding_retrieval_config.py tests/ui/tools/test_embedding_model_dialog.py tests/ui/tools/test_ai_translator_story08.py -q
- uv run pytest tests/contracts/config/test_unified_repository.py tests/ai_translator/test_term_vector_manifest.py tests/ai_translator/test_term_vector_index.py -q
- uv run ruff check src/transbridge/config/llm.py src/transbridge/infra/embedding_client.py src/transbridge/infra/embedding_model_store.py src/transbridge/ui/tools/ai_translator tests/infra/test_embedding_client.py tests/infra/test_embedding_model_store.py tests/ui/tools/test_embedding_retrieval_config.py tests/ui/tools/test_embedding_model_dialog.py
- uv run ruff format --check src/transbridge/config/llm.py src/transbridge/infra/embedding_client.py src/transbridge/infra/embedding_model_store.py src/transbridge/ui/tools/ai_translator tests/infra/test_embedding_client.py tests/infra/test_embedding_model_store.py tests/ui/tools/test_embedding_retrieval_config.py tests/ui/tools/test_embedding_model_dialog.py
