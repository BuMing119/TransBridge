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
