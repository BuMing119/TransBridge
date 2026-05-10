# Story 11: SST 二进制序列化器

**所属方案**: `plans/file-parsing/plan.md`
**技术模块**: `src/transbridge/parser/xt/` (新建 + 修改)
**状态**: 已确认
**创建日期**: 2026-05-09

## 前置依赖

### 上游 Story
- **Story-09**（同 plan）：已完成 → 提供 `SST_Parser` + `SST_Entry` + `SST_Subrecord`。本 Story 需在 `SST_Entry` 追加 `_raw` 字段，在 `SST_Parser` 追加 `_raw_header` / `_magic` 属性。

### 跨 Plan 依赖
- `file-writing/plan.md` → `PluginWriter.write()`（写回流程）。SST 序列化器是独立模块，不直接依赖写回流程，但写回流程后续可集成 `SST_Serializer` 作为新的输出格式。

### 引用的架构决策
- ADR-001: TranslationEntry 统一数据模型
- ADR-002: Collection 数据中枢

---

## 验收标准

（从 plan 原样复制）

- [ ] `SST_Serializer.from_parser(sst: SST_Parser)` 从解析器实例创建序列化器，保留原始二进制数据供模板复制
- [ ] `to_bytes()` 重建完整 SST 二进制，与源文件字节级一致（未修改时）
- [ ] `update_and_save(form_id, new_text, path)` 修改指定记录的 translated_text 后写回
- [ ] `update_entries(updates: list)` 批量修改后写回
- [ ] 修改 `text`（English）后，str_len 和记录偏移同步更新
- [ ] 修改 `translated_text`（Chinese）后，chn_len 同步更新，后续数据正确偏移
- [ ] `overwrite=False` 时输出到新路径，`overwrite=True` 时覆盖原文件
- [ ] 输出文件能被 `SST_Parser.from_file()` 重新解析，条目数一致、文本正确
- [ ] 非 SSU9 文件调用时明确报错（SSU8 不支持写回）

---

## 数据流

```
SST_Parser.from_file("input.sst")  ← 解析时保留 _raw_header + 每条 SST_Entry._raw
    │
    ▼
用户修改: entry.translated_text = "新译文"
    │  或: modified_entries = {form_id: {text, translated_text}}
    ▼
SST_Serializer.from_parser(parser)
    │  持有: _magic, _raw_header, [(entry, tail_bytes), ...]
    │
    ├─→ update_and_save(form_id, "新译文", "output.sst")
    │        │
    │        ▼ 内部调用
    │   _rebuild_record(entry, raw_head, chn_text)
    │        │
    │        ▼ 返回 bytes
    │   to_bytes() → Header + Σ(record_bytes)
    │        │
    │        ▼
    │   save(path) → "output.sst"
    │
    └─→ update_entries([{form_id: 123, translated_text: "A"}, ...], "out.sst")
             │
             ▼ 批量修改缓存 → to_bytes() → save()

验证: SST_Parser.from_file("output.sst") → 条目数一致, 文本匹配
```

SSU9 记录二进制结构（26B 固定头 + 可变尾）：
```
[form_id 4B][edid 8B][unk12 4B][f2 4B][str_idx 2B][str_len 2B][pad 2B]
[eng_text N*2B UTF-16LE]
[chn_len 4B LE][chn_text M*2B UTF-16LE]
[extra/subrecords ...]
```

## 关键接口

### SST_Entry 扩展（修改现有）

```python
@dataclass(frozen=True)
class SST_Entry:
    # ── 现有字段（不变）──
    rec: str
    form_id: int
    text: str
    index: int = 0
    trail_hash: bytes = field(default_factory=bytes)
    extra: int = 0
    global_seq: int = 0
    f2: int = 0
    translated_text: str = ""
    subrecords: tuple[SST_Subrecord, ...] = ()

    # ── 新增字段 ──
    _raw: bytes = field(default_factory=bytes, repr=False)
    # SSU9: 原始 26B head + eng_text (不含 tail)
    # 序列化时先写 _raw[:26] 固定头，再写 eng_text，再写 chn_len + chn_text + tail
```

### SST_Parser 扩展（修改现有）

```python
class SST_Parser:
    _raw_header: bytes = b""   # 新增: 原始 header (magic 4B + ... + name + null + metadata)
    _magic: bytes = b""        # 新增: b"SSU8" 或 b"SSU9"

    # from_file() / _parse_ssu9() 中填充 _raw_header 和 _magic
    # SST_Entry 构造时传入 _raw 参数
```

### SST_Serializer（新建）

```python
class SST_Serializer:
    """基于 SST_Parser 解析结果，模板重建 SST 二进制文件。

    不增删记录，不修改 header，保留 extra/subrecords 原样。
    仅支持 SSU9 格式。
    """

    def __init__(self, magic: bytes, header: bytes,
                 records: list[tuple["SST_Entry", bytes]]) -> None: ...
    # records: [(entry, tail_bytes), ...]
    # tail_bytes = chn_len(4B) + chn_text + extra/subrecords（原始，未解析）

    @classmethod
    def from_parser(cls, sst: "SST_Parser") -> "SST_Serializer": ...
    # 校验 magic != b"SSU8"（不支持）
    # 遍历 sst.entries，提取 _raw 字段 + 重构 tail_bytes

    def to_bytes(self) -> bytes: ...
    # Header + Σ _rebuild_record(entry, tail_bytes, entry.translated_text, entry.text)
    # 未修改时结果与源文件完全一致

    def save(self, path: str | Path) -> Path: ...
    # 写 to_bytes() 到文件（原子写入：.tmp → rename）

    def update_and_save(self, form_id: int, translated_text: str,
                        path: str | Path, overwrite: bool = False) -> bool: ...
    # 找到 form_id 匹配的第一条记录 → 修改 translated_text → to_bytes() → save()
    # 返回 True（找到并修改）或 False（未找到）
    # overwrite=False: path 若已存在则报 FileExistsError

    def update_entries(self, updates: list[dict],
                       path: str | Path, overwrite: bool = False) -> dict: ...
    # updates: [{form_id: int, translated_text: str, text: str}, ...]
    # 返回 {matched: int, updated: int, not_found: list[int]}
    # text 可选，用于修改 English 原文
```

### 内部方法

```python
def _rebuild_record(self, entry: SST_Entry, tail: bytes,
                    eng_text: str, chn_text: str) -> bytes: ...
# 1. 从 entry._raw[:26] 复制固定头
# 2. 用 struct.pack 覆盖 str_len（从 eng_text 重新计算字符数）
# 3. 写 eng_text.encode("utf-16-le")
# 4. 计算 chn_bytes = chn_text.encode("utf-16-le")
# 5. 写 struct.pack("<I", len(chn_bytes)) + chn_bytes
# 6. 追加原始 extra/subrecords（从 tail 中提取：跳过 4B chn_len + chn_text）
#    定位方式: orig_chn_len = struct.unpack_from("<I", tail, 0)[0]
#             extra_start = 4 + orig_chn_len
```

---

## 实现步骤

### 步骤 1: 扩展 SST_Parser 和 SST_Entry 保留原始二进制

**涉及文件**: `src/transbridge/parser/xt/sst_parser.py`（修改）

**实现要点**:
- `SST_Entry` 新增 `_raw: bytes` 字段（默认 `b""`，repr=False）
- `SST_Parser.__init__` 新增 `raw_header: bytes = b""` 和 `magic: bytes = b""` 参数（默认值保证向后兼容）
- `SST_Parser._parse_ssu9()` 中：
  - 保存 `_raw_header = data[:start]`（magic + plugin name + null + metadata）
  - 保存 `_magic = b"SSU9"`
  - 每条记录构造时传入 `_raw = data[off : off + 26 + eng_len]`（26B头 + eng_text）
- `SST_Parser._parse_ssu8()` 中保存 `_magic = b"SSU8"`（_raw 不需要，SSU8 不支持写回）
- 所有现有测试和调用方不受影响（新字段有默认值）

**边界条件**:
- `_raw` 可能为空（旧版 SST_Entry 直接构造）→ 序列化时检查并报错
- `raw_header` 可能为空（旧版 Parser）→ from_parser 时检查

**测试策略**:
- 解析 SSU9 文件后，验证 `_raw_header[:4] == b"SSU9"` 和 `_magic == b"SSU9"`
- 验证每条 entry._raw 长度 >= 26

---

### 步骤 2: 新建 SST_Serializer 类骨架与 from_parser

**涉及文件**: `src/transbridge/parser/xt/sst_serializer.py`（新建）

**实现要点**:
- 定义 `SST_Serializer` 类，构造器保存 `_magic`、`_header`、`_records: list[tuple[SST_Entry, bytes]]`
- `from_parser(sst)`:
  1. 校验 `sst._magic != b"SSU8"`，是则 raise ValueError
  2. 校验 `sst._raw_header` 非空
  3. 遍历 `sst.entries`：
     - 提取 `entry._raw`（26B头 + eng_text）
     - 提取 tail_bytes：从 entry._raw 之后到源文件末尾（实际上需要在 parser 中也保存 tail）
  
  **设计调整**：当前 SST_Entry._raw 只保留了 26B头+eng_text，但 tail（chn_len+chn_text+extra）没有存储。需要在 `_parse_ssu9()` 中也保留 tail 数据。

  修改方案：在 `_parse_ssu9()` 中，每条 SST_Entry 构造时多传一个 `_tail: bytes` 字段（tail = data[after_eng : next_off]）。

- `from_parser` 中的 `records` 结构改为 `[(entry, tail_bytes), ...]`

**边界条件**:
- entry._raw 为空 → raise ValueError("SST_Entry 缺少原始二进制数据，请用 SST_Parser.from_file() 解析")
- entry._tail 为空 → 仅无 Chinese 文本的条目，正常处理

**测试策略**:
- `from_parser(parser)` 成功构造
- from_parser 后 records 长度 == len(parser.entries)

---

### 步骤 3: 实现 _rebuild_record 与 to_bytes

**涉及文件**: `src/transbridge/parser/xt/sst_serializer.py`

**实现要点**:
- `_rebuild_record(entry, tail, eng_text, chn_text)`:
  ```python
  # 1. 复制固定头 26B，覆盖 str_len
  head = bytearray(entry._raw[:26])
  eng_bytes = eng_text.encode("utf-16-le")
  struct.pack_into("<H", head, 22, len(eng_text))  # str_len = 字符数
  
  # 2. 写 eng_text
  result = bytes(head) + eng_bytes
  
  # 3. 写 chn_len + chn_text
  chn_bytes = chn_text.encode("utf-16-le")
  result += struct.pack("<I", len(chn_bytes))
  result += chn_bytes
  
  # 4. 追加原始 extra/subrecords（跳过原 chn_len + chn_text）
  if len(tail) >= 4:
      orig_chn_len = struct.unpack_from("<I", tail, 0)[0]
      extra_start = 4 + orig_chn_len
      if extra_start <= len(tail):
          result += tail[extra_start:]
  
  return bytes(result)
  ```
- `to_bytes()`: `_header + b"".join(_rebuild_record(e, tail, e.text, e.translated_text) for e, tail in self._records)`

**边界条件**:
- eng_text 包含 emoji/特殊字符 → UTF-16LE 正确编码（surrogate pairs）
- chn_text 为空 → chn_len=0，不写 chn_bytes
- tail 为空或过短 → 跳过 extra 追加
- 未修改条目的 to_bytes() 结果应与源文件完全一致

**测试策略**:
- 解析 → 不修改 → to_bytes() → 与源文件 bytes 逐字节比对
- 修改一条 translated_text → to_bytes() → 重新解析验证文本

---

### 步骤 4: 实现 update_and_save 与 update_entries

**涉及文件**: `src/transbridge/parser/xt/sst_serializer.py`

**实现要点**:
- `update_and_save(form_id, translated_text, path, overwrite=False)`:
  1. 遍历 records，找 `entry.form_id == form_id` 的第一条
  2. 找到 → 修改缓存（替换 entry 的 translated_text）→ `to_bytes()` → `save(path, overwrite)`
  3. 未找到 → return False
- `save(path, overwrite=False)`:
  1. Path(path) 已存在且 overwrite=False → raise FileExistsError
  2. 原子写入：写 .tmp → os.replace
- `update_entries(updates, path, overwrite=False)`:
  1. 构建 `{form_id: {translated_text, text}}` 查找表
  2. 遍历 records，匹配 form_id 则应用修改
  3. `to_bytes()` → `save()`
  4. 返回统计

**边界条件**:
- 同一 form_id 多条记录 → 全部更新（by form_id 匹配）
- translated_text 和 text 均可选 → 不传则不修改该字段
- path 目录不存在 → save() 中自动创建

**测试策略**:
- update_and_save 不存在的 form_id → 返回 False
- update_entries 批量修改 → 重新解析验证每条文本
- overwrite=False 时重复写入 → FileExistsError

---

### 步骤 5: 更新包导出

**涉及文件**: `src/transbridge/parser/xt/__init__.py`（修改）

**实现要点**:
- 添加 `from .sst_serializer import SST_Serializer`
- `__all__` 包含 `SST_Serializer`

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `parser/xt/sst_parser.py` | 修改 | SST_Entry 加 `_raw`、`_tail` 字段；SST_Parser 加 `_raw_header`、`_magic`；构造时填充 |
| `parser/xt/sst_serializer.py` | 新建 | SST_Serializer 完整实现（~180行） |
| `parser/xt/__init__.py` | 修改 | 导出 SST_Serializer |

## 风险与注意事项

- **风险 1**: SSU9 格式未文档化，extra 段结构不完整理解 → **缓解**: extra 原样拷贝不解析，保证写回后字节一致
- **风险 2**: Unicode 代理对（emoji）导致 str_len 计算错误 → **注意**: UTF-16LE 编码中代理对占 2 个码元（4 字节），`len(text)` 为 Python 字符数（proxy 对算 1），与 SSU9 的 str_len 语义一致
- **注意 1**: SST_Entry 是 frozen dataclass，`translated_text` 不可原地修改。需要在 `update_and_save` 中用 `dataclasses.replace(entry, translated_text=new_text)` 创建新实例
- **注意 2**: 批量更新时注意不要重复编码/写回，所有修改一次性应用到缓存，最后统一 `to_bytes()` + `save()`
- **注意 3**: 原始 binary 的保留对向后兼容重要——新增 `_raw`、`_tail`、`_raw_header` 字段都设默认值，不影响旧代码直接构造 SST_Entry / SST_Parser
