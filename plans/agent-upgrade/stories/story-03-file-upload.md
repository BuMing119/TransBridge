# Story 03: 文件上传与知识注入

**所属方案**: `plans/agent-upgrade/plan.md`
**技术模块**: smart_assistant/file_parser
**状态**: 已确认
**创建日期**: 2026-05-10

## 前置依赖

### 上游 Story
- Story-01（同 plan）：infra/ 包就绪（BinaryFileParser 不依赖 infra）

### 引用的架构决策
- [ADR-009: FileParser 统一接口设计](../../../docs/adr/009-agent-file-memory-reflexion.md)

## 验收标准

- [ ] `FileParser` ABC + `ParsedDocument` 数据类定义
- [ ] `TextFileParser` 支持 .xlsx/.csv/.md/.txt/.json
- [ ] `BinaryFileParser` 支持 .pdf/.docx
- [ ] `ParatranzParser` 支持 ParaTranz 导出格式
- [ ] 上传 UI：拖拽区 + 文件选择按钮，显示已上传文件列表
- [ ] 上传后文件解析为 ParsedDocument，注入 agent 上下文
- [ ] agent 翻译时自动引用已上传的纠错表/术语参考

## 数据流

```
用户拖拽文件 → 文件列表显示
  → FileParser.get_parser(path) 工厂匹配解析器
  → parser.parse(path) → ParsedDocument
  → 存储到 self._uploaded_docs: dict[str, ParsedDocument]
  → ContextBuilder.build() 追加文档摘要到 system prompt
  → agent 翻译/校对时引用文档内容
```

## 关键接口

### ParsedDocument & FileParser (base.py)

```python
@dataclass
class ParsedDocument:
    source_path: Path
    format: str       # "excel"/"csv"/"markdown"/"pdf"/"word"/"paratranz"
    title: str
    sections: list[dict]  # [{"heading":str, "content":str, "rows":list[dict]}]
    raw_text: str     # 纯文本提取（供向量嵌入，Story-04 使用）
    metadata: dict

class FileParser(ABC):
    supported_extensions: list[str] = []
    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument: ...
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in self.supported_extensions
    @classmethod
    def get_parser(cls, path: Path) -> "FileParser | None":
        for sub in cls.__subclasses__():
            if sub().can_handle(path): return sub()
        return None
```

### TextFileParser (text_parser.py)
- Excel: openpyxl 逐行读取 → sections[{"heading": sheet_name, "rows": [...]}]
- CSV: csv.reader → 同上
- Markdown: 按 ## 标题分段 → sections[{"heading": title, "content": text}]
- TXT/JSON: 全量文本 + 结构化提取

### BinaryFileParser (binary_parser.py)
- PDF: pdfplumber 提取文本 → raw_text + 按页分段
- Word: python-docx 提取段落 → raw_text + 按段落分段

## 实现步骤

### 步骤 1-4: 创建解析器（4 文件新建）
`file_parser/base.py` (ABC+ParsedDocument+工厂) → `text_parser.py` → `binary_parser.py` → `paratranz_parser.py` → `__init__.py`

### 步骤 5: UI 上传区域
**涉及文件**: `chat_widget.py`（改）
- 在消息区域上方新增文件拖拽区（QWidget + dropEvent）
- 已上传文件列表（带删除按钮）
- `_uploaded_docs: dict[str, ParsedDocument]` 存储

### 步骤 6: ContextBuilder 扩展
**涉及文件**: `context_builder.py`（改）
- `ContextBuilder.build()` 追加已上传文件摘要：
  ```
  ## 已上传参考文件
  - {title} ({format}): {前200字符摘要}...
  ```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `file_parser/__init__.py` | 新建 | 子包导出 |
| `file_parser/base.py` | 新建 | ABC + ParsedDocument + 工厂 |
| `file_parser/text_parser.py` | 新建 | Excel/CSV/MD/TXT/JSON |
| `file_parser/binary_parser.py` | 新建 | PDF/Word |
| `file_parser/paratranz_parser.py` | 新建 | ParaTranz 格式 |
| `chat_widget.py` | 修改 | 拖拽区 + 文件列表 UI |
| `context_builder.py` | 修改 | 注入已上传文件内容 |

## 风险与注意事项

- **风险**: PDF 提取质量依赖第三方库，复杂排版可能丢内容 → 提供文本格式 fallback 提示
- **风险**: 大文件(>10MB)解析耗时阻塞UI → 在线程中解析，显示进度
- **注意**: 新增依赖 `pdfplumber` + `python-docx` 需更新 requirements
