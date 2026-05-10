# Story 02: Skill 系统

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/skills
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01（同 plan）：必须已完成 → infra/ 包就绪

### 引用的架构决策
- [ADR-005: Skill 定义采用 TOML 格式](../../../docs/adr/005-toml-prompt-no-langchain.md)
- [ADR-008: skills/ 子包结构](../../../docs/adr/008-smart-assistant-code-layering.md)

## 验收标准

- [ ] `SkillLoader` 可解析 TOML 格式 Skill 定义文件
- [ ] `SkillRegistry` 可按关键词/工具匹配 Skill
- [ ] `SkillExecutor` 可执行 Skill（注入 system prompt + 调度工具）
- [ ] `data/skills/` 目录创建，含 1 个预置示例 Skill
- [ ] 快捷指令面板新增「Skill」按钮，列出可用 Skill
- [ ] 用户点击 Skill → 填入输入框 → agent 按 Skill 流程执行

## 数据流

```
用户点击 Skill / 输入匹配 Skill 关键词
  → SkillRegistry.match(user_input) 
  → 返回匹配的 SkillSpec（按 trigger.keywords 匹配）
  → SkillExecutor.execute(spec, ctx):
      1. 注入 spec.prompt.template 到 system prompt
      2. 限制可用工具为 spec.tools.allowed
      3. 调用 _run_llm_round() 
  → agent 按 Skill 定义的流程执行
```

## 关键接口

### `SkillSpec` 数据类

```python
@dataclass
class SkillSpec:
    name: str
    display_name: str
    description: str
    version: str
    enabled: bool
    trigger_keywords: list[str]
    required_tools: list[str]
    prompt_template: str
    allowed_tools: list[str]
    source_path: Path  # TOML 文件路径
```

### `SkillLoader`

```python
class SkillLoader:
    @staticmethod
    def load(path: Path) -> SkillSpec:
        """解析单个 TOML 文件 → SkillSpec"""
    
    @staticmethod
    def load_all(directory: Path) -> list[SkillSpec]:
        """扫描目录加载所有 .toml 文件，跳过解析失败的文件"""
```

### `SkillRegistry`

```python
class SkillRegistry:
    def register(self, spec: SkillSpec) -> None: ...
    def unregister(self, name: str) -> None: ...
    def match(self, user_input: str) -> list[SkillSpec]:
        """按 trigger_keywords 匹配，返回按匹配度排序的列表"""
    def list_all(self) -> list[SkillSpec]: ...
    def reload(self, directory: Path) -> None:
        """重新扫描目录，热加载"""
```

### `SkillExecutor`

```python
class SkillExecutor:
    def __init__(self, registry: SkillRegistry, chat_widget): ...
    def execute(self, spec: SkillSpec) -> None:
        """注入 Skill prompt → 限制工具 → 触发 LLM 对话"""
```

## 实现步骤

### 步骤 1: SkillSpec + SkillLoader

**涉及文件**: `smart_assistant/skills/skill_loader.py`（新建）

**实现要点**: 定义 SkillSpec 数据类，实现 TOML→SkillSpec 解析，处理 [meta]/[trigger]/[prompt]/[tools] 四个 section

**伪代码**:
```python
import tomllib
from pathlib import Path
from dataclasses import dataclass

@dataclass
class SkillSpec:
    name: str; display_name: str; description: str; version: str
    enabled: bool; trigger_keywords: list[str]; required_tools: list[str]
    prompt_template: str; allowed_tools: list[str]; source_path: Path

class SkillLoader:
    @staticmethod
    def load(path: Path) -> SkillSpec:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        meta = data["meta"]
        trigger = data.get("trigger", {})
        prompt = data.get("prompt", {})
        tools = data.get("tools", {})
        return SkillSpec(
            name=meta["name"], display_name=meta["display_name"],
            description=meta.get("description", ""),
            version=meta.get("version", "1.0"),
            enabled=meta.get("enabled", True),
            trigger_keywords=trigger.get("keywords", []),
            required_tools=trigger.get("requires_tools", []),
            prompt_template=prompt.get("template", ""),
            allowed_tools=tools.get("allowed", []),
            source_path=path,
        )
```

**边界条件**: TOML 语法错误 → `tomllib.TOMLDecodeError` 捕获，记录警告，返回 None；缺少必填字段 → `KeyError` 捕获，跳过该文件

### 步骤 2: SkillRegistry

**涉及文件**: `smart_assistant/skills/skill_registry.py`（新建）

**实现要点**: 类级别字典存储，按关键词交集匹配，返回按匹配度排序

**边界条件**: 无 Skill 注册 → `match()` 返回空列表；重复注册同名 Skill → 覆盖

### 步骤 3: SkillExecutor

**涉及文件**: `smart_assistant/skills/skill_executor.py`（新建）

**实现要点**: 接收 SkillSpec + chat_widget 引用，注入 prompt + 限制工具 + 触发 LLM

**伪代码**:
```python
class SkillExecutor:
    def execute(self, spec: SkillSpec, ctx) -> None:
        # 构建 Skill 专用 system prompt
        skill_prompt = spec.prompt_template.format(**ctx)
        ctx.conversation.add_system(skill_prompt)
        # 可选：限制工具可用列表（通过 tool_registry 过滤）
        ctx.run_llm_round()
```

**边界条件**: Skill prompt 包含 `{变量}` 但 ctx 无对应属性 → KeyError 捕获，提示用户补全信息

### 步骤 4: 子包 __init__.py

**涉及文件**: `smart_assistant/skills/__init__.py`（新建）

导出 SkillSpec, SkillLoader, SkillRegistry, SkillExecutor

### 步骤 5: 预置示例 Skill

**涉及文件**: `data/skills/translate_with_terms.toml`（新建）

按 ADR-005 定义的 TOML 格式编写，内容为「术语辅助翻译」场景

### 步骤 6: UI 集成

**涉及文件**: `quick_actions.py`（改）, `chat_widget.py`（改）

**实现要点**: 
- 快捷指令面板新增「Skill」下拉按钮，列出所有已启用 Skill
- 点击后文本填入 `[Skill: {name}] {description}` 
- chat_widget 解析 `[Skill: xxx]` 前缀，调用 SkillExecutor

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/skills/__init__.py` | 新建 | 子包公开导出 |
| `smart_assistant/skills/skill_loader.py` | 新建 | SkillSpec + SkillLoader |
| `smart_assistant/skills/skill_registry.py` | 新建 | 注册 + 匹配 |
| `smart_assistant/skills/skill_executor.py` | 新建 | Skill 执行调度 |
| `data/skills/translate_with_terms.toml` | 新建 | 预置示例 |
| `quick_actions.py` | 修改 | 新增 Skill 按钮 |
| `chat_widget.py` | 修改 | `[Skill:xxx]` 解析 + SkillExecutor 调用 |

## 风险与注意事项

- **风险**: TOML 文件格式错误导致所有 Skill 加载失败 → 单独捕获每个文件的异常，跳过失败文件
- **注意**: Skill prompt 中引用变量需与 ContextBuilder 输出的字段名对齐
