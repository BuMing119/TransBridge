# Story 09: XT SST 二进制解析器

**所属方案**: `plans/file-parsing/plan.md`
**技术模块**: parser
**状态**: ✔️ 已实现（含 index 修正）
**创建日期**: 2026-05-08
**最后更新**: 2026-05-08（SSU8/SSU9 per-EDID index 修正 + EDID 后缀扩展 + str_idx 过滤）

## 前置依赖

### 上游 Story
- Story-05（XT XML 解析）：已完成 → `XT_XmlParser` 接口风格参考（`from_file()`, `entries`, `to_json()`, `to_csv()`）
- Story-01（ESP 插件解析）：已完成 → `TranslationEntry.create_from_plugin_entry()` 工厂方法模式参考

### 跨 Plan 依赖
- `core-data-model/plan.md` → `TranslationEntry` dataclass（ADR-001 已冻结）

### 引用的架构决策
- **ADR-001**: TranslationEntry 统一数据模型 — `create_from_sst_entry()` 遵循现有工厂方法模式
- **ADR-002**: Collection 数据中枢 — SST 条目后续作为迁移源合并到 Collection

## 验收标准

（从 plan 原样复制，已更新为实际实现）

- [x] `SST_Parser.from_file()` 能正确解析 `tests/trans_exe/xt/ccvsvsse002-pets_english_chinese.sst`（SSU8），输出 116 条记录（第 117 条截断被跳过并警告），全部 116 条 per-EDID index 与 XT XML 一致
- [x] SSU9 文件 `tests/trans_exe/xt/hlioremi_english_chinese.sst` 正确解析 8,487 条记录，99.9% 含中文翻译，8,391/8,391 与 XT XML 文本匹配（index 99.9% 一致）
- [x] 非 SSU8/SSU9 文件给出明确 `ValueError`
- [x] UTF-16LE 解码失败时跳过该条目并记录警告，不中断整体解析
- [x] 空 SST 文件（仅 header 无条目）返回空 entries 列表
- [x] `TranslationEntry.create_from_sst_entry()` 正确映射字段：key=EDID:FormID|index, original=SST文本, context=EDID, 有 translated_text 时 stage=1
- [x] 与 `XT_XmlParser` 接口风格一致（`entries` 属性、`from_file()` 工厂方法、`to_json()`/`to_csv()` 导出）

## 数据流

```
SST 二进制文件 (.sst)
    │
    ▼
SST_Parser.from_file(path)
    │
    ├─ 1. 读取全部字节
    ├─ 2. 校验 magic (SSU8/SSU9) → 否则 raise ValueError
    ├─ 3. SSU8: 跳过 16B header / SSU9: 解析插件名 header
    └─ 4. 按格式分发 _parse_ssu8() / _parse_ssu9()
        SSU8:
        ├─ 逐字段 struct.unpack_from() (LE)
        ├─ field_a 低字节提取 per-EDID index (field_a & 0xFF) + 1
        ├─ 全局序号 global_seq 保留尾部 seq 字段
        └─ append SST_Entry
        SSU9:
        ├─ 模式匹配扫描记录起点 (EDID 后缀 + str_len + UTF-16LE 校验)
        ├─ str_idx=0x4000 过滤 (39% 假阳性)
        ├─ unk12 低 16 位提取 per-EDID index (unk12 & 0xFFFF) + 1
        ├─ Phase 2: 尾部提取中文翻译 (4B LE 长度前缀 + UTF-16LE)
        └─ append SST_Entry
    │
    ▼
SST_Parser.entries: list[SST_Entry]
    │
    ▼
TranslationEntry.create_from_sst_entry(sst_entry)
    ├─ id = f"{edid}:{form_id:08X}|{index}"
    ├─ key = id
    ├─ original = text
    ├─ translation = translated_text (SSU9) 或 "" (SSU8 单语言)
    ├─ stage = STAGE_TRANSLATED if translated_text else STAGE_UNTRANSLATED
    ├─ context = edid
    └─ editor_id = edid
    │
    ▼
TranslationEntry (统一格式)
```

## 关键接口

### 数据结构

```python
# sst_parser.py

@dataclass(frozen=True)
class SST_Entry:
    """SST 二进制文件中的单条记录（SSU8 或 SSU9）"""
    edid: str          # 8字符 EDID，如 "ACTIFULL", "INFONAM1"，已去除尾部 \0
    form_id: int       # Field_B / 记录首 4B，游戏内字符串 ID (FormID)
    text: str          # UTF-16LE 解码后的主字符串（SSU8: 单语言; SSU9: English）
    index: int = 0     # per-EDID 子索引，1-based（SSU8: (field_a & 0xFF)+1; SSU9: (unk12 & 0xFFFF)+1）
    trail_hash: bytes  # 尾部数据原始字节（SSU8 only），长度 2/4/6/14
    extra: int = 0     # 额外 ID / 2B LE（SSU8 only）
    global_seq: int = 0  # 全局序号，对应 XML sID（SSU8 only）
    f2: int = 0        # 第二 ID / 4B LE（SSU9 only）
    translated_text: str = ""  # 译文字符串（SSU9 双语）
```

### 类接口

```python
class SST_Parser:
    """XT SST 二进制文件解析器
    
    解析 xTranslator 导出的 SST (Skyrim Strings Table) 二进制文件。
    接口风格与 XT_XmlParser 保持一致。
    """
    
    def __init__(self, entries: list[SST_Entry]) -> None:
        """内部构造，推荐使用 from_file() 工厂方法"""
        self.entries = entries
    
    @classmethod
    def from_file(cls, sst_path: str) -> "SST_Parser":
        """从 SST 文件路径创建解析器实例。
        
        Args:
            sst_path: .sst 文件路径
            
        Returns:
            SST_Parser 实例
            
        Raises:
            ValueError: 魔数不是 SSU8
            FileNotFoundError: 文件不存在
        """
        ...
    
    def to_json(self, ensure_ascii: bool = False, indent: int = 2) -> str:
        """导出为 JSON 字符串"""
        ...
    
    def to_json_file(self, path: str, ensure_ascii: bool = False, indent: int = 2) -> None:
        """导出为 JSON 文件"""
        ...
    
    def to_csv_file(self, path: str) -> None:
        """导出为 CSV 文件"""
        ...
    
    def __len__(self) -> int:
        return len(self.entries)
```

### SSU9 解析器设计

SSU9 记录结构（每条记录 26B 固定头 + 变长 English + tail）：

```
Offset  Size  Field
0       4     form_id (LE)
4       8     edid (ASCII)
12      4     unk12 (LE) — per-EDID index 编码: low 16 bits = REC id, high 16 bits = idMax
16      4     f2 (LE) — 第二 FormID
20      2     str_idx (LE) — 记录类型标志 (0/256/512/1024/16640/17408 合法; 0x4000 过滤)
22      2     str_len (LE) — English 字符串字节数
24      2     (未使用)
26      N     English 字符串 (UTF-16LE)
26+N    var   Tail: [4B chn_len LE] + [chn_len B Chinese UTF-16LE]
```

**EDID 后缀白名单**（扩展后）：
```python
_VALID_EDID_SUFFIXES = (
    "FULL", "NAM1", "NAM2", "DATA", "DESC", "NAME", "GOLD", "SNAM",
    "QNAM", "CNAM", "EDID", "MODL", "MODT", "DNAM", "ITXT", "NNAM",
    "RNAM", "SHRT",
)
```
新增 DNAM (MGEFDNAM)、ITXT (MESGITXT)、NNAM (QUSTNNAM)、RNAM (INFORNAM)、SHRT (NPC_SHRT)，覆盖 41 种 EDID 类型。

### TranslationEntry 工厂方法

```python
# translation_entry.py 追加

@classmethod
def create_from_sst_entry(cls, sst_entry: "SST_Entry") -> "TranslationEntry":
    """从 SST_Entry 创建 TranslationEntry。
    
    字段映射:
        id = "{edid}:{form_id:08X}|{index}"
        original = text
        translation = translated_text (非空时) 或 "" (空字符串)
        context = edid
        stage = STAGE_TRANSLATED if translated_text else STAGE_UNTRANSLATED
        editor_id = edid
    """
    ...
```

## 实现步骤

### 步骤 1: 新建 `SST_Entry` dataclass 和 `SST_Parser` 骨架

**涉及文件**: `src/transbridge/parser/sst_parser.py`（新建）

**实现要点**:
- 创建 `SST_Entry` frozen dataclass，5 个字段
- 创建 `SST_Parser` 类，定义 `__init__`, `__len__`, `to_json`, `to_csv_file` 等方法签名
- `to_json()` 使用 `dataclasses.asdict()` + `json.dumps()`
- `to_csv_file()` 使用 `csv.DictWriter`，字段名按 SST_Entry 字段

**边界条件**:
- `trail_hash` 在 JSON 序列化时转为 hex 字符串（bytes 不可直接 JSON 序列化）

**伪代码**:
```python
# sst_parser.py

import csv
import json
import logging
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class SST_Entry:
    edid: str
    form_id: int
    text: str
    index: int
    trail_hash: bytes
    extra: int


class SST_Parser:
    def __init__(self, entries: list[SST_Entry]):
        self.entries = entries
    
    def __len__(self):
        return len(self.entries)
    
    def to_json(self, ensure_ascii=False, indent=2):
        data = {
            "entries": [
                {**asdict(e), "trail_hash": e.trail_hash.hex()}
                for e in self.entries
            ]
        }
        return json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    
    def to_json_file(self, path, ensure_ascii=False, indent=2):
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(ensure_ascii=ensure_ascii, indent=indent))
    
    def to_csv_file(self, path):
        fieldnames = ["edid", "form_id", "text", "index", "extra", "trail_hash_hex"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for e in self.entries:
                writer.writerow({
                    "edid": e.edid,
                    "form_id": f"0x{e.form_id:08X}",
                    "text": e.text,
                    "index": e.index,
                    "extra": f"0x{e.extra:04X}",
                    "trail_hash_hex": e.trail_hash.hex(),
                })
```

**测试策略**:
- 单元测试：创建手动构造的 `SST_Entry`，验证 `to_json()`/`to_csv()` 输出格式

---

### 步骤 2: 实现 `SST_Parser.from_file()` 核心解析逻辑

**涉及文件**: `src/transbridge/parser/sst_parser.py`

**实现要点**:
- `from_file()` 是工厂方法，读取文件 → 解析 → 构造 `SST_Parser` 实例
- 魔数校验：前 4 字节必须为 `b"SSU8"`
- 跳过 16 字节 header（不解析 header 中未知字段）
- 循环解析记录直到 `pos >= len(data)`
- 每条记录固定开销 35 字节 + 可变部分（字符串 + 尾部数据）
- 使用 `struct.unpack_from('<H', ...)` / `'<I'` 等小端格式

**解析循环逻辑**:
```
while pos < len(data):
    # 最小检查: 是否能读 prefix(2) + edid(8) + fA(4) + fB(4) + sep(2) + strlen(4)
    if pos + 24 > len(data): break
    
    rec_type = struct.unpack_from('<H', data, pos)[0]; pos += 2
    edid = data[pos:pos+8].decode('ascii').rstrip('\0'); pos += 8
    field_a = struct.unpack_from('<I', data, pos)[0]; pos += 4
    form_id = struct.unpack_from('<I', data, pos)[0]; pos += 4
    pos += 2  # 跳过分隔符
    
    str_len = struct.unpack_from('<I', data, pos)[0]; pos += 4
    
    # 边界检查: 字符串数据
    if pos + str_len > len(data):
        logger.warning("截断: 字符串数据超出文件末尾")
        break
    
    raw_str = data[pos:pos+str_len]; pos += str_len
    
    # UTF-16LE 解码
    try:
        text = raw_str.decode('utf-16-le')
    except UnicodeDecodeError:
        logger.warning("UTF-16LE 解码失败，跳过条目")
        continue
    
    # 尾部长度检查
    if pos + 4 > len(data): break
    trail_len = struct.unpack_from('<I', data, pos)[0]; pos += 4
    
    # 边界检查 + 合理性检查
    if trail_len > 100 or pos + trail_len > len(data):
        logger.warning("截断: 尾部数据异常或超出文件末尾")
        break
    
    trail_hash = data[pos:pos+trail_len]; pos += trail_len
    
    # 尾部固定字段
    if pos + 7 > len(data): break
    pos += 1  # 分隔符
    global_seq = struct.unpack_from('<I', data, pos)[0]; pos += 4
    extra = struct.unpack_from('<H', data, pos)[0]; pos += 2
    
    # Per-EDID index 从 field_a 低字节提取（与 XT XML REC id 一致）
    per_edid_index = (field_a & 0xFF) + 1
    
    entries.append(SST_Entry(
        edid=edid, form_id=form_id, text=text,
        index=per_edid_index, global_seq=global_seq,
        trail_hash=trail_hash, extra=extra,
    ))

return cls(entries=entries)
```

**边界条件**:
- `pos + needed > len(data)` → 每个字段读取前检查，超界则 `break` + warning
- `str_len` 过大（如 >100000）→ 依赖 `pos + str_len > len(data)` 自然捕获
- `trail_len > 100` → 合理性检查，防止异常解析
- 文件仅 header（header 后 pos=16，`pos < len(data)` 为 False）→ 返回空 entries
- 非 ASCII EDID → `decode('ascii', errors='replace')` 容错

**测试策略**:
- 加载 `tests/trans_exe/xt/_resourcepack_english_chinese.sst`，验证 entries 数量 = 26（1条截断被跳过）
- 检查前 3 条记录的 edid/form_id/text 正确性
- 用 `b"NOTSST..."` 测试魔数校验
- 用 `b"SSU8" + b"\x00"*12` 测试空文件

---

### 步骤 3: 新增 `TranslationEntry.create_from_sst_entry()` 工厂方法

**涉及文件**: `src/transbridge/converter/translation_entry.py`（修改）

**实现要点**:
- 在 `TranslationEntry` 类中新增 `@classmethod create_from_sst_entry()`
- 在文件顶部添加导入: `from src.transbridge.parser.sst_parser import SST_Entry`
- id 格式: `"{edid}:{form_id:08X}|{index}"` — 与 ESP 解析的 id 格式保持一致
- translation 设为空字符串，stage 设为 `STAGE_UNTRANSLATED`
- context 设为 edid（记录类型，如 "ACTIFULL"）

**字段映射表**:

| TranslationEntry 字段 | SST_Entry 字段 | 转换逻辑 |
|----------------------|---------------|---------|
| `id` | edid + form_id + index | `f"{edid}:{form_id:08X}\|{index}"` |
| `key` | = id | 直接赋值 |
| `original` | text | 直接赋值 |
| `translation` | — | `""` (空字符串) |
| `stage` | — | `STAGE_UNTRANSLATED` (0) |
| `context` | edid | 直接赋值 |
| `editor_id` | edid | 直接赋值 |

**伪代码**:
```python
@classmethod
def create_from_sst_entry(cls, sst_entry: "SST_Entry") -> "TranslationEntry":
    id_value = f"{sst_entry.edid}:{sst_entry.form_id:08X}|{sst_entry.index}"
    return cls(
        id=id_value,
        key=id_value,
        original=sst_entry.text,
        translation="",
        stage=STAGE_UNTRANSLATED,
        context=sst_entry.edid,
        editor_id=sst_entry.edid,
    )
```

**边界条件**:
- edid 可能含不可打印字符 → `rtrim('\0')` 已在 SST_Parser 中处理
- form_id 为 0 → 仍正常序列化为 `"00000000"`，不特殊处理

**测试策略**:
- 构造 `SST_Entry(edid="ACTIFULL", form_id=0x731B5BB3, text="Button", index=4, ...)` → 验证生成的 TranslationEntry 各字段值

---

### 步骤 4: 编写验证脚本

**涉及文件**: 临时脚本（可放在 `scripts/test_sst_parser.py` 或直接在 Python 交互环境运行）

**实现要点**:
1. 导入 `SST_Parser` 和 `TranslationEntry`
2. 加载测试文件 `tests/trans_exe/xt/_resourcepack_english_chinese.sst`
3. 打印解析统计：条目总数、各 EDID 类型计数
4. 打印前 5 条记录详情（edid, form_id(hex), text, index）
5. 转换为 TranslationEntry 并打印前 3 条
6. 验证第 27 条截断被正确处理

**伪代码**:
```python
import sys
sys.path.insert(0, "src")

from transbridge.parser.sst_parser import SST_Parser
from transbridge.converter.translation_entry import TranslationEntry

# 1. 解析
parser = SST_Parser.from_file("tests/trans_exe/xt/_resourcepack_english_chinese.sst")
print(f"解析条目数: {len(parser)}")

# 2. EDID 统计
from collections import Counter
edid_counts = Counter(e.edid for e in parser.entries)
for edid, count in sorted(edid_counts.items()):
    print(f"  {edid}: {count}")

# 3. 前5条记录
for i, e in enumerate(parser.entries[:5]):
    print(f"[{i}] edid={e.edid} form_id=0x{e.form_id:08X} text={e.text!r} index={e.index}")

# 4. 转为 TranslationEntry
for i, e in enumerate(parser.entries[:3]):
    te = TranslationEntry.create_from_sst_entry(e)
    print(f"[{i}] id={te.id} original={te.original!r} translation={te.translation!r} context={te.context}")

# 5. 导出
parser.to_json_file("/tmp/sst_test_output.json")
parser.to_csv_file("/tmp/sst_test_output.csv")
print("Done.")
```

**预期输出**:
```
解析条目数: 26
WARNING: 截断: 尾部数据异常或超出文件末尾 (第27条)
  ACTIFULL: 3
  CONTFULL: 3
  DOORFULL: 4
  FLORFULL: 3
  FLORRNAM: 3
  INGRFULL: 1
  MISCFULL: 9
[0] edid=ACTIFULL form_id=0x731B5BB3 text='Button' index=4
...
```

**测试策略**:
- 手动运行脚本，核对输出与预期一致
- 确认 warning 日志输出第 27 条截断信息

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/parser/sst_parser.py` | **新建** | SST_Entry dataclass + SST_Parser 类（SSU8 + SSU9 双格式） |
| `src/transbridge/converter/translation_entry.py` | 修改 | 新增 `create_from_sst_entry()` + 顶部导入 `SST_Entry` |
| `scripts/sst2json.py` | 新建 | CLI 转换工具，支持 `--stats`/`--pretty`/`--csv`/`-o`，输出含 global_seq 和 per-EDID index |

### 后续修正（2026-05-08）

| 文件 | 操作 | 说明 |
|------|------|------|
| `sst_parser.py` | 改 | `SST_Entry.index` 从全局 seq 改为 per-EDID index；新增 `global_seq` 字段；`_parse_ssu8()` 从 `field_a` 低字节提取 index；`_parse_ssu9()` 从 `unk12` 低 16 位提取 index；EDID 后缀扩展至 18 个；str_idx=0x4000 过滤 |
| `sst2json.py` | 改 | `--pretty` 和 `--stats` 输出新增 `global_seq` |

## 风险与注意事项

- **风险 1**: 不同 xTranslator 版本导出的 SST 格式可能有细微差异 → **缓解**: 魔数校验 + 异常容错，发现不兼容格式时 warning 而非崩溃
- **风险 2**: SST 文件可能包含大量条目（数万条），全量加载到内存 → **缓解**: 当前实现全量加载，后续如有性能问题可改为流式迭代（`_iter_entries` 类似 XT_XmlParser）
- **注意 1**: `trail_hash` 是 bytes 类型，JSON 序列化时需转为 hex 字符串
- **注意 2**: SST 是单语言文件（非翻译对），`translation` 字段留空，等待后续迁移源 Story 处理合并逻辑
- **注意 3**: `from_file()` 读取整个文件到内存（`Path.read_bytes()`），适合 SST 文件通常较小（<10MB）的场景
