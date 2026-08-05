# Story 03: 工具模块 Controller 封装

**所属方案**: `plans/smart-assistant-refactor/plan.md`
**技术模块**: backend
**状态**: 已实现
**创建日期**: 2026-05-22

## 前置依赖

### 上游 Story
- Story 02（base.py 类型分离）: 需要 `tools/types.py` 已存在，Controller 类的构造器参数类型引用 `ExecutionContext` / `ToolResult`

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008 (2026-05-22 更新节): Controller 类封装 + `_common.py` 共享函数
- ADR-005: TOML 格式约束不涉及

## 验收标准

- [ ] `tool_translator.py`: `TranslationController` 类（~350行），构造器注入 `AppContext` + `TaskManager`，9 个 `_tool_*` 函数 + 4 个内部辅助函数（`_action_label`/`_get_profiles`/`_get_post_process_config`/`_get_term_db_info`）转为实例方法。`_load_llm_config` 提取到 `_common.py`，`_register_translator_tools` 保留为模块级
- [ ] `tool_proofreader.py`: `ProofreaderController` 类（~280行），3 个 `_tool_*` 函数 + 5 个内部辅助函数（`_build_postprocessor`/`_summarize_*`/`_generate_report`）转为实例方法。`set_last_report`/`get_last_report` 保留为模块级函数（跨模块访问），`_register_proofreader_tools` 保留为模块级
- [ ] `tool_editor.py`: `EditorController` 类（~350行），7 个 `_tool_*` 函数 + 1 个内部辅助函数（`_resolve_label_id`）转为实例方法。`_register_editor_tools` 保留为模块级
- [ ] `tools/_common.py`: `load_llm_config()` 共享函数（~30行），消除 LLM 配置加载重复。`build_postprocessor` 暂不提取（两处签名差异较大，强行统一会引入回归风险）
- [ ] `_register_*_tools()` 使用惰性初始化 + 模块级 wrapper 兼容模式（避免空 AppContext 问题 + 保持测试向后兼容）
- [ ] `tool_translator.py` 和 `tool_proofreader.py` 中不再有重复的 LLM 配置加载逻辑
- [ ] 跨模块导入 `set_last_report` 保留为模块级函数（`tool_translator.py:260` 依赖 `from ...tool_proofreader import set_last_report`）
- [ ] 158 处 `_tool_*` 测试引用全部有对应模块级 wrapper（`grep -rn "_tool_" tests/` 逐项验证，0 ImportError）
- [ ] 所有 39 个工具的注册和执行行为不变
- [ ] 现有测试全部通过（~223 用例，0 ImportError）

## 数据流

```
工具注册（模块加载时）:
  _register_translator_tools()
    │
    controller = TranslationController(app_ctx, task_manager)
    │
    ToolRegistry.register(namespace, [
      {"name": "start_translation",
       "execute": controller.start_translation,  ← 实例方法代替模块级函数
       ...}
    ])

工具执行（运行时）:
  ToolExecutionHandler.execute_step(step)
    │
    spec.execute(args, ctx)  ← 调用 controller.start_translation(args, ctx)
    │
    controller._load_llm_config()  ← 来自 _common.py（消除重复）
    controller._task_mgr.register(...)  ← 注入的 TaskManager

共享函数关系:
  tools/_common.py
    └── load_llm_config()          ← TranslationController / ProofreaderController 共享

  build_postprocessor 各自保留在对应 Controller 中（签名差异大，不提取）
```

## 关键接口

### tools/_common.py（新增 ~30行）

```python
"""工具模块共享函数 — LLM 配置加载。

从 tool_translator 和 tool_proofreader 中提取重复的 LLM 配置加载逻辑。
保持与原始实现完全相同的签名和返回类型，不改行为。"""

def load_llm_config():
    """从 LLMConfig.load_from_file() 加载配置，返回 _LLMConfig 对象。
    与 tool_translator._load_llm_config() 和 tool_proofreader._load_llm_config()
    的现有实现完全一致。消费者使用属性访问: cfg.api_key, cfg.model 等。"""
    from src.transbridge.infra.config import LLMConfig
    return LLMConfig.load_from_file()

# 注意: build_postprocessor 暂不提取——
# tool_translator 和 tool_proofreader 中的构建逻辑差异较大
# （参数数量不同、阶段配置不同），强行统一会引入回归风险。
# 两处的 PostProcessor 构建逻辑各自保留在对应 Controller 中。
```

### TranslationController (tool_translator.py)

```python
class TranslationController:
    """AI 翻译/润色工具控制器。封装翻译启动、任务控制、配置管理等操作。"""

    def __init__(self, app_context, task_manager):
        self._ctx = app_context
        self._task_mgr = task_manager

    # 翻译启动
    def start_translation(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def start_polish(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...

    # 任务控制
    def stop_task(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def get_task_status(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...

    # 配置管理
    def get_translation_config(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def set_translation_config(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def set_term_config(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...

    # 作用域
    def set_scope(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def get_scope_preview(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
```

### ProofreaderController (tool_proofreader.py)

```python
class ProofreaderController:
    """后处理工具控制器。封装后处理执行、报告生成、质量查询。"""

    def __init__(self, app_context, task_manager):
        self._ctx = app_context
        self._task_mgr = task_manager

    def run_postprocess(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def get_quality_report(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def list_quality_reports(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
```

### EditorController (tool_editor.py)

```python
class EditorController:
    """编辑器工具控制器。封装筛选、条目查询、选择、编辑、Stage 设置、标签管理。"""

    def __init__(self, app_context, task_manager):
        self._ctx = app_context
        self._task_mgr = task_manager

    def set_filters(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def get_visible_entries(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def select_entries(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def edit_translation(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def set_stage(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def list_labels(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
    def manage_entry_labels(self, args: dict, ctx: ExecutionContext) -> ToolResult: ...
```

## 实现步骤

### 步骤 1: 创建 tools/_common.py

**涉及文件**: `src/transbridge/smart_assistant/tools/_common.py`（新建）

**实现要点**:
- 定义 `load_llm_config()` 函数 — 从 `tool_translator.py` 和 `tool_proofreader.py` 中提取重复的 LLM 配置加载实现
- 函数是无状态的纯函数

**边界条件**:
- LLMConfig.load_from_file() 失败 → 传播异常（不在此层捕获）

**测试策略**:
- 集成测试：在 Controller 类中使用 `_common.load_llm_config()` 验证结果与原有实现一致

### 步骤 2: 重构 tool_translator.py → TranslationController

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）

**实现要点**:
- 创建 `TranslationController` 类，构造器接收 `(app_context, task_manager)`
- 将 15 个模块级函数转为实例方法（函数签名保持 `(args: dict, ctx: ExecutionContext) -> ToolResult`）
- 方法内部通过 `self._ctx` / `self._task_mgr` 访问共享状态，替代原有的闭包/全局变量
- 删除 `_load_llm_config()` 和 `_action_label()` 的本地定义，改用 `from ._common import load_llm_config`
- 修改 `_register_translator_tools()`:
  ```python
  # 惰性初始化——不在模块加载时创建 Controller，而在首次工具调用时创建
  _translator_ctrl: TranslationController | None = None

  def _get_translator_controller() -> TranslationController:
      global _translator_ctrl
      if _translator_ctrl is None:
          from src.transbridge.ui.context import AppContext
          _translator_ctrl = TranslationController(AppContext(), TaskManager())
      return _translator_ctrl

  # ToolSpec 中使用 lambda 惰性分发
  ToolRegistry.register_tools("translator", [
      {"name": "start_translation",
       "execute": lambda args, ctx: _get_translator_controller().start_translation(args, ctx),
       ...},
  ])
  ```
  或保持模块级函数作为 wrapper（更简单、更兼容）:
  ```python
  def _tool_start_translation(args, ctx, collection=None) -> ToolResult:
      return _get_translator_controller().start_translation(args, ctx, collection)
  ```

**变更前后对比**:
```python
# 变更前（模块级函数反模式）
def _tool_start_translation(args: dict, ctx: ExecutionContext,
                            collection: TranslationEntryCollection) -> ToolResult:
    config = _load_llm_config()  # 模块内私有函数
    ...

# 变更后（Controller 实例方法）
class TranslationController:
    def start_translation(self, args: dict, ctx: ExecutionContext,
                          collection: TranslationEntryCollection) -> ToolResult:
        config = load_llm_config()  # 来自 _common.py
        ...
```

**边界条件**:
- 工具函数使用 `@require_collection` / `@validate_params` 装饰器 → Controller 方法同样支持装饰器
- `_tool_set_scope` 可能引用其他工具函数 → 改为 `self.set_scope(...)` 内部调用
- **Controller `self._ctx` vs 方法 `ctx` 参数**（重要设计约束）:
  - `self._ctx`（构造器注入的 AppContext）**仅限**用于初始化路径中不依赖集合/项目数据的操作。经代码审查确认，`load_llm_config()` 实际不依赖 AppContext，因此 `self._ctx` 在当前设计中处于**冗余/预留**状态
  - 运行时数据访问（collection、translation_scope、entry_labels 等）**必须**通过方法的 `ctx: ExecutionContext` 参数——该参数由 ExecutionEngine 在每次工具调用时注入真实的 AppContext
  - **实施建议**: 优先不设 `self._ctx`，所有上下文通过方法 `ctx` 参数传入。若未来有方法确实需要构造时的 AppContext 引用，再按需添加，并显式注释该方法为何需要 `self._ctx`
  - 惰性初始化中的 `AppContext()` 空实例不会影响运行时行为（因为真实数据通过 `ctx` 参数注入）

**测试策略**:
- 运行现有翻译工具测试，验证行为不变
- 检查 `_register_translator_tools()` 注册的工具数不变

### 步骤 3: 重构 tool_proofreader.py → ProofreaderController

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_proofreader.py`（修改）

**实现要点**:
- 创建 `ProofreaderController` 类，构造器接收 `(app_context, task_manager)`
- 将 3 个 `_tool_*` 函数 + 5 个内部辅助函数（`_build_postprocessor`/`_summarize_refine_results`/`_summarize_polish_results`/`_summarize_decisions`/`_generate_report`）转为实例方法，**保留 2 个模块级函数**（`set_last_report` / `get_last_report`——`tool_translator.py:260` 跨模块导入它们）
- 删除本地的 `_load_llm_config()`，改用 `_common.load_llm_config()`；`_build_postprocessor()` **保留在原文件不变**（签名与 translator 不同，不适合提取）
- 修改 `_register_proofreader_tools()`

**边界条件**:
- `set_last_report` 和 `get_last_report` 作为模块级可变状态持有者，保留为独立函数供跨模块访问
- `_build_postprocessor()` 保留在 proofreader 内部（构建逻辑与 translator 中的差异较大）

**测试策略**:
- 运行现有后处理工具测试，验证行为不变

### 步骤 4: 重构 tool_editor.py → EditorController

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_editor.py`（修改）

**实现要点**:
- 创建 `EditorController` 类，接收 `(app_context, task_manager)`
- 将 9 个模块级函数转为实例方法
- `_resolve_label_id` 辅助方法保留为 Controller 的私有方法
- 修改 `_register_editor_tools()`

**测试策略**:
- 运行现有编辑工具测试，验证行为不变

### 步骤 5: 验证测试 wrapper 覆盖率（专项验证）

**涉及文件**: `tests/smart-assistant/` 下所有引用 `_tool_*` 的测试文件

**背景**: 当前 158 处测试引用直接导入 `_tool_*` 私有函数名（分布在 8 个测试文件中：`test_agent_tool_integration.py: 82处`、`test_tool_consolidation.py: 12处`、`test_run_postprocess.py: 6处` 等）。Controller 封装后，每个 `_tool_*` 必须对应一个模块级 wrapper 函数委托给 Controller 方法。

**验证步骤**:
1. 全局搜索所有测试文件中的 `_tool_` 引用：
   ```
   grep -rn "_tool_" tests/smart_assistant/ --include="*.py"
   ```
2. 列出所有被测试引用的 `_tool_*` 函数名，去重得到完整清单
3. 逐一确认每个函数名在对应的工具模块中存在模块级 wrapper：
   ```python
   # wrapper 模板
   def _tool_xxx(args, ctx, collection=None) -> ToolResult:
       return _get_xxx_controller().xxx(args, ctx, collection)
   ```
4. 运行全量测试（`pytest tests/smart_assistant/ -x -q`），确认 0 ImportError
5. 若任何 `_tool_*` 函数缺少 wrapper，测试会以 `ImportError` 失败 → 补加 wrapper

**验收标准**:
- [ ] `grep -rn "_tool_" tests/smart_assistant/` 输出的每个函数名在源码中有对应 wrapper
- [ ] 全量测试通过，0 ImportError

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/_common.py` | 新建 | 共享函数 `load_llm_config()`，~30行 |
| `src/transbridge/smart_assistant/tools/tool_translator.py` | 修改 | 9个`_tool_*`+4辅助→TranslationController类，660→~350行 |
| `src/transbridge/smart_assistant/tools/tool_proofreader.py` | 修改 | 3个`_tool_*`+5辅助→ProofreaderController类，430→~280行 |
| `src/transbridge/smart_assistant/tools/tool_editor.py` | 修改 | 7个`_tool_*`+1辅助→EditorController类，406→~350行 |

## 风险与注意事项

- **风险 1**: 惰性初始化 + 模块级 wrapper 兼容 → Controller 实例在首次工具调用时创建（而非模块导入时），避免空 AppContext 问题
- **风险 2**: **跨模块导入兼容**：`tool_translator.py:260` 导入 `from ...tool_proofreader import set_last_report` → 该函数必须保留为模块级导出（或改为 `ProofreaderController.set_last_report` 类方法）。建议保留模块级 `set_last_report` 函数不变，不纳入 Controller 封装
- **风险 3**: **测试兼容**：158 处测试引用直接导入 `_tool_*` 私有函数名。通过保留模块级 wrapper 函数（委托给 Controller 方法）实现向后兼容。Story 完成后运行全量测试验证
- **注意 1**: `tool_translator.py` 中的 `_get_profiles()` 和 `_get_post_process_config()` 等辅助函数也转为 Controller 私有方法
- **注意 2**: Controller 类的方法签名必须与 ToolSpec 注册时的一致：`(args: dict, ctx: ExecutionContext, [collection: TranslationEntryCollection]) -> ToolResult`
