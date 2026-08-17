# Story 03: 流水线编排

**所属方案**: plans/fomod-translation/plan.md
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 01（fomod_xml 界面文本翻译）、Story 02（builder 组装）

### 跨 Plan 依赖
- FR16: fileops.extract/pack、fileops.diff_directories、migrator.migrate、TranslationMemoryManager.apply_to_collection
- ai_translator/translator.py 的 AutoTranslator（AI 兜底，含术语库 + 名词提取 + 可关后处理）
- parser/plugin_parser.py（解析 ESP）、writer/plugin_writer.py（写回 ESP）、parser/plugin/plugin_with_context.py（SSEPluginWithContext）

### 引用的架构决策
- ADR-014: 翻译来源优先级（旧归档键对齐→词典兜底→AI）、做法1+键优先级
- ADR-015: 通用工具复用（fileops/migrator）

## 验收标准

- [ ] 新建 src/transbridge/fomod/pipeline.py
- [ ] 编排流程：解包→diff→逐插件[键对齐→词典兜底→AI翻译→写回]→界面文本翻译→组装→打包
- [ ] 逐插件翻译循环：每个 .esp/.esm/.esl 独立解析、迁移、兜底、翻译、写回
- [ ] AI 兜底复用 AutoTranslator（含术语库匹配 + 名词提取），非裸 LLMClient
- [ ] 运行时上下文注入：llm_config（AI）+ tm_manager（词典），由 GUI 传入
- [ ] 纯 Python 无 PyQt 依赖（ADR-008）

## 数据流（翻译来源按 ADR-014 优先级）

解包(fileops.extract) → diff(fileops.diff_directories) →
逐插件循环 for esp:
  ① PluginParser.parse_plugin → Collection
  ② 有旧版时 migrator.migrate（键对齐迁移）
  ③ tm.apply_to_collection（词典兜底）
  ④ 剩余 stage=0 无译文 → AutoTranslator.translate（术语库 + 名词提取）
  ⑤ PluginWriter 写回 esp
界面文本翻译(fomod_xml.translate_module_config) → 组装(builder) → 打包(fileops.pack)

## 关键接口

```python
class FomodPipeline:
    def __init__(self, rules=None, llm_config=None, tm_manager=None):
        # llm_config: LLMConfig（AI 翻译 + 界面文本），tm_manager: TranslationMemoryManager（词典兜底）

    def run(self, new_archive, output_archive, *, old_archive=None,
            work_dir=None, fmt='zip', progress_callback=None, stop_event=None) -> PipelineResult:
        # 依序执行，逐插件循环，返回 PipelineResult
```

## 实现步骤

### 步骤 1: PipelineResult + 编排骨架

涉及文件: src/transbridge/fomod/pipeline.py（新建）

实现要点:
- PipelineResult 汇总各步统计（extracted/inherited/needs_review/dict_applied/ai_translated/plugins_processed/kept/stripped）
- 纯 Python，不 import PyQt

### 步骤 2: 逐插件翻译循环

实现要点:
- 遍历 new_dir 下 .esp/.esm/.esl，先 PluginParser 解析 → Collection
- 有旧版同款插件 → migrator.migrate 键对齐（继承 + needs_review）
- tm.apply_to_collection 词典兜底
- 剩余 stage=0 无译文 → AutoTranslator.translate（TranslatorConfig + llm_config）
- PluginWriter 写回（SSEPluginWithContext + PluginStringsLookup）

边界条件:
- 旧归档缺失 → 跳过键对齐，直接词典兜底 + AI
- 词典/llm_config 为 None → 对应步骤跳过
- 写回失败 → 不阻断流水线（组装仍产出）

### 步骤 3: 界面文本翻译 + 组装打包

实现要点:
- llm_config 非 None 时，调 fomod_xml.translate_module_config 翻译 ModuleConfig.xml
- assemble_output 组装 + pack 打包

## 文件变更清单

src/transbridge/fomod/pipeline.py（新建）、tests/fomod/test_fomod_pipeline.py（新建）