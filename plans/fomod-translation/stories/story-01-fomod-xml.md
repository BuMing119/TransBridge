# Story 01: fomod XML 解析与翻译

**所属方案**: plans/fomod-translation/plan.md
**技术模块**: backend
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 跨 Plan 依赖
- FR16 fileops（解包后的 fomod 目录作为输入）
- infra/llm_client.py 的 LLMClient.chat()（AI 翻译新增文本）

### 引用的架构决策
- ADR-014 决策 5: fomod 界面文本按名称层级键对齐 + LLM 翻译新增 + UTF-16LE 处理

## 验收标准

- [ ] 新建 src/transbridge/fomod/__init__.py + fomod_xml.py
- [ ] 解析 ModuleConfig.xml / info.xml，正确处理 UTF-16LE BOM
- [ ] 按 moduleName/installStep/group/plugin/description 层级键与旧版对齐复用译文
- [ ] 新增/变化且未覆盖的文本走 LLM 翻译（复用 infra/llm_client.py 的 chat()）
- [ ] 写回时保持 UTF-16LE 编码与 BOM

## 关键接口

```python
def read_fomod_xml(path):  # 显式处理 BOM，open rb 读 BOM 判断 utf-16/utf-8 后解码
def write_fomod_xml(path, content):  # 写回 UTF-16LE + BOM
def translate_module_config(new_xml, old_xml, llm):  # 层级键对齐 + AI 翻译新增
```

## 数据流

新版 ModuleConfig.xml (UTF-16LE) 读出文本节点列表；旧版(可选)读出 {层级键: 旧译文}；逐节点键命中复用旧译/新增变化走 LLM.chat 翻译；组装回 XML 写回 UTF-16LE+BOM。

## 实现步骤

### 步骤 1: UTF-16LE XML 读写

涉及文件: src/transbridge/fomod/__init__.py（新建）、fomod_xml.py（新建）

实现要点:
- 读: open(path,'rb') 读前 2-3 字节判断 BOM（FF FE=LE / EF BB BF=UTF-8），用对应 codec 解码
- 写: 编码 utf-16-le 并前置 BOM（FF FE）
- 不依赖 xml.etree 自动编码检测（ADR 决策）

### 步骤 2: 层级键对齐 + AI 翻译

实现要点:
- 解析 XML 提取文本节点（moduleName/installStep@name/group@name/plugin@name/description）
- 旧版同名键复用；新增/变化调 llm.chat 翻译（短文本简单翻译指令，不依赖三轮策略）

边界条件:
- 无旧版 → 全部走 AI 翻译
- XML 无 ModuleConfig.xml → 返回 None 或空
- 未知节点/属性 → 忽略容错

## 文件变更清单

src/transbridge/fomod/__init__.py（新建）、fomod_xml.py（新建）、tests/fomod/test_fomod_xml.py（新建）

## 风险与注意事项

- UTF-16LE BOM 处理错误 → 显式 BOM 读写 + 往返一致性测试
- fomod XML 无官方 schema → 解析需容错