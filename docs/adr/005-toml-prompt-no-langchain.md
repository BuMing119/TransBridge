# ADR-005: TOML + string.Template 构建 Prompt，不使用 LangChain

- **状态**: 已接受
- **日期**: 2026-03 (回顾性记录于 2026-05-06)
- **决策者**: BuMing

## Context

AI 翻译模块需要灵活的 Prompt 构建方式，包括：
- 术语注入
- 上下文信息注入（NPC 种族/职业、对话任务线）
- 多语言支持
- 格式化输出指令（JSON 格式）
- 需要支持 OpenAI 和 Anthropic 两种 API

需要决定 Prompt 模板和 LLM 调用的技术方案。

## Decision

采用 **TOML 模板 + string.Template** 自建 Prompt 构建，**不引入 LangChain/LangGraph**：

```python
class PromptBuilder:
    def __init__(self, game_profile: str, target_lang: str):
        self._game_config = load_toml(f"data/prompts/games/{game_profile}.toml")
        self._lang_config = load_toml(f"data/prompts/langs/{target_lang}.toml")

    def build_translation_prompt(self, batch, terms) -> str:
        template = Template(self._game_config["translation"]["system"])
        return template.safe_substitute(terms=terms, ...)
```

**评估 LangChain/LangGraph 后明确拒绝的理由**:

| 维度 | LangChain/LangGraph | 当前实现 |
|------|---------------------|---------|
| PyQt6 QThread 集成 | asyncio 冲突 | 原生支持 |
| 暂停/停止控制流 | 无等价机制 | BaseException + Event |
| 流式增量写回 | 仅 log callback | 边流边写 Collection |
| In-flight 术语共享 | State 更新粒度不够 | Lock + shared dict |
| Round2 特殊并发 | 难以表达 | ThreadPoolExecutor |
| Prompt 变量语法 | `{var}` 与 JSON 冲突 | `$var` 避开冲突 |
| 依赖体积 | 30+ 传递依赖 | 0 额外依赖 |

## Consequences

- **正**: 零额外依赖，打包体积小
- **正**: `$var` 语法与 JSON 格式不冲突（LangChain 的 `{var}` 与 JSON 花括号冲突）
- **正**: 完整的流式控制（chunk 回调、连接取消）
- **正**: 定制并发模型无框架约束
- **负**: Prompt 模板管理、版本控制需自行维护
- **负**: 如果未来后处理演进为复杂多智能体系统，LangGraph 可能更合适（但当前无此需求）

## Alternatives Considered

- **LangChain ChatPromptTemplate**: 见上表，核心问题是 `{var}` 语法与 JSON 输出格式冲突
- **f-string / format()**: 拒绝：不支持从外部文件加载模板
- **Jinja2**: 可选方案，但引入额外依赖，TOML 当前足够

### 更新: 2026-05-10 - Skill 定义文件采用 TOML 格式

**决策**: Skill 系统（FR7.13.1）的定义文件采用 TOML 格式，与 Prompt 模板保持技术栈一致。

**Skill 定义文件格式** (`data/skills/<skill_name>.toml`):

```toml
[meta]
name = "translate_with_terms"
display_name = "术语辅助翻译"
description = "先查询术语库，再使用术语翻译选中词条"
version = "1.0"
enabled = true

[trigger]
keywords = ["翻译", "术语", "标准化"]
requires_tools = ["lookup_terms", "translate_entries"]

[prompt]
template = """
你是一个翻译专家。在执行翻译之前：
1. 使用 lookup_terms 查询以下术语的标准译名：{keywords}
2. 使用 translate_entries 翻译词条，必须使用查询到的术语译名
3. 翻译完成后使用 check_quality 验证术语一致性
"""

[tools]
allowed = ["lookup_terms", "translate_entries", "check_quality"]
```

**原因**: TOML 是项目已有的 Prompt 模板格式（ADR-005），用户可编辑，支持注释，不需要新依赖。Skill 本质是「带触发条件的 Prompt 模板 + 工具组合」，与 ADR-005 的 TOML 技术路线天然一致。

**影响**:
- 新增依赖: `tomli` (Python 3.11 已内置 `tomllib`，无需新增)
- 目录变更: `data/skills/` 目录存放用户自定义 skill 文件，系统预置 skill 放在 `src/transbridge/smart_assistant/skills/presets/`
- Skill 文件热加载: `skill_loader.py` 监控 `data/skills/` 目录变化（watchdog 或轮询）
