# Writer + Parser 工具 — LLM 使用参考（Batch 3）

> 格式参照 `claude-code-tools-reference.md`，纯使用面。

---

## 1. write_back

**描述:**
将译文写回源文件。通过 `target` 参数选择目标格式。需用户确认（admin 权限操作）。长运行操作（后台执行）。

已解析文件路径可通过 `get_app_state` 查询（返回 esp_file/eet_file/xt_file 字段）。target 自动推断依赖此状态。

何时用：
- 翻译完成后需要将译文保存到 ESP/ESM 插件
- 需要导出 EET XML 或 XT XML 翻译文件
- 需要导出 .strings 本地化文件

选择 target 的推断规则（按优先级）：先检查用户意图 → 有已解析的 ESP 插件 → `esp`；有已解析的 EET 文件 → `eet`；有已解析的 XT 文件 → `xt`；仅需导出 strings → `strings`。多源并存时建议让用户明确指定 target，而非自动推断。可通过 `get_app_state` 查看当前已解析文件状态，辅助判断合适的 target。

**参数:**
- `target` (必填): 写回目标。可选:
  - `"esp"` — 写回 ESP/ESM 插件（操作前需要先通过 parse_esp 解析过插件文件）
  - `"eet"` — 写回 EET XML（操作前需要先通过 parse_eet 解析过对应的 XML 源文件）
  - `"xt"` — 写回 XT XML（操作前需要先通过 parse_xt 解析过对应的 XML 源文件）
  - `"strings"` — 导出 .strings 本地化文件（操作前需要先通过 parse_esp 解析过插件文件，且需提供路径；path 和 output_dir 至少提供一个）
- `path` (可选): 输出文件/目录路径。不传则使用当前已解析的源文件路径（esp/eet/xt）。路径遍历（`../`）和绝对路径会被拒绝
- `output_dir` (可选): **仅 `target="strings"` 时可用**，指定 strings 导出时的输出目录路径，与 `path` 至少提供一个。其他 target 传此参数无效

**副作用:**
- 译文被永久写入到目标文件中

**使用规则:**
- 需用户确认后执行（admin 权限）
- esp/strings 目标：操作前需要先通过 parse_esp 解析过插件文件
- eet/xt 目标：操作前需要先通过 parse_eet/parse_xt 解析过对应的 XML 源文件
- 典型用法: `write_back target=esp`（就地写回）/ `write_back target=strings output_dir=./output`

**返回:**
- esp 目标: `{written_count, path}`
- strings 目标: `{written_count, strings_files}`
- eet/xt 目标: `{path}`

---

## Parser 工具（解析翻译文件）

### 核心概念：翻译集合槽位 (Collection Slot)

槽位是 TransBridge 内部管理翻译集合的容器。每个槽位包含：
- 一个翻译集合（TranslationEntryCollection，即条目列表）
- 显示名称（label，通常为源文件名）
- 源文件关联信息（部分工具会记录文件路径供 write_back 推断 target）

槽位的生命周期：
- 创建：通过 parser 工具的 `action="create_slot"` 创建
- 激活：创建时自动激活（成为后续操作的默认目标），或通过 `switch_collection` 切换
- 查询：通过 `list_collections` 查看所有已加载槽位
- 移除：在 UI 中手动移除（当前无工具支持）

### 重要区分：create_slot vs append（action 参数）

所有 5 个 Parser 工具均支持 `action` 参数，可选择解析结果的安置方式：

| action 值 | 行为 | 副作用 | 典型场景 |
|-----------|------|--------|---------|
| `"create_slot"` (默认) | 解析文件 + 创建新翻译集合槽位并激活 + 记录文件路径供 write_back 推断 target | 创建新槽位，切换活跃集合 | 首次加载翻译源文件 |
| `"append"` | 解析文件 + 将条目追加到当前活跃集合 | 修改当前集合内容 | 从 JSON/Strings 补数据，或合并多个同格式翻译文件 |

> **注意**: parse_sst 例外——SST 格式目前不支持 write_back，仅用于解析查看，不会为 write_back 推断 target。

**共同参数（5 工具共享）:**
- `path` (必填): 文件路径。不传会报错——需要用户提供具体路径
- `action` (可选): `"create_slot"`（默认）— 创建新翻译集合槽位并激活；`"append"` — 将解析结果追加到当前活跃集合

**共同权限（5 工具共享）:**
- permission: **`write`** — 解析操作会产生副作用（创建/修改翻译集合），通过 PermissionGuard 触发用户确认

**共同使用规则（5 工具共享）:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活，多数工具会记录文件路径供后续 write_back 推断 target（parse_sst / import_json 除外——这两者不记录文件路径，write_back 无法自动推断）
- `action="append"`：将解析结果追加到当前活跃集合（不创建新槽位）。前提：当前必须存在活跃集合。使用 `action="append"` 前，先通过 `get_app_state` 确认 `has_active_collection` 为 true
- 文件扩展名白名单: `.esp` / `.esm` / `.esl` / `.sst` / `.xml` / `.json` / `.strings`
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝
- 查询当前活跃集合: 通过 `list_collections` 查看所有已加载集合及激活状态，通过 `get_app_state` 查看 `active_collection`、`collection_count` 等全局状态

### 关键状态查询工具

以下工具在所有 parser 工具执行前后都可用，建议在操作前确认状态：

| 需要确认的状态 | 查询工具 | 说明 |
|--------------|---------|------|
| 当前活跃集合 | `get_app_state` (has_active_collection, active_collection) 或 `list_collections` | append 前必须确认 |
| 已解析文件路径 | `get_app_state` (esp_file/eet_file/xt_file) | write_back target 推断依赖此状态 |
| 已加载集合列表 | `list_collections` | 确认创建结果、选槽位切换目标 |

---

### 2. parse_esp

**描述:**
解析 ESP/ESM/ESL 插件文件，提取所有可翻译字符串。默认创建新翻译集合槽位并激活（`action="create_slot"`），也可追加到当前活跃集合（`action="append"`）。通过 `action="create_slot"` 解析后，后续 `write_back target=esp` 可以自动推断输出目标。

**权限:** `write`（产生副作用，通过 PermissionGuard 触发用户确认）

**参数:**
- `path` (必填): `.esp` / `.esm` / `.esl` 文件路径
- `action` (可选): `"create_slot"`（默认）— 创建新槽位并激活；`"append"` — 追加到当前活跃集合

**返回:**
- `action="create_slot"`: `{action, label, entry_count, activated}`
- `action="append"`: `{action, added_count, total_count, target_label}`

**使用规则:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活，支持后续 write_back target 推断（esp / strings）。新槽位自动激活，可通过 `list_collections` 确认
- `action="append"`：追加到当前活跃集合，当前必须存在活跃集合
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝
- 通过 `get_app_state` 可查看 `esp_file` 字段确认已解析的 ESP 文件路径

---

### 3. parse_eet

**描述:**
解析 EET XML 翻译文件（Elder Scrolls Translation 格式）。默认创建新翻译集合槽位并激活，也可追加到当前活跃集合。通过 `action="create_slot"` 解析后，后续 `write_back target=eet` 可以自动推断输出目标。

与 `parse_xt` 的区别：两者都处理 XML 文件，但格式不同——EET 是 Elder Scrolls Translation 工具导出格式，XT 是 xTranslator 工具导出格式。如果不确定格式，根据 XML 根元素判断：EET 文件根元素通常为 `<EET>`，XT 文件根元素通常为 `<XT>`。也可询问用户使用的翻译工具。

**权限:** `write`（产生副作用，通过 PermissionGuard 触发用户确认）

**参数:**
- `path` (必填): EET XML 文件路径
- `action` (可选): `"create_slot"`（默认）— 创建新槽位并激活；`"append"` — 追加到当前活跃集合

**返回:**
- `action="create_slot"`: `{action, label, entry_count, activated}`
- `action="append"`: `{action, added_count, total_count, target_label}`

**使用规则:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活，支持后续 write_back target=eet
- `action="append"`：追加到当前活跃集合，当前必须存在活跃集合
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝
- 通过 `get_app_state` 可查看 `eet_file` 字段确认已解析的 EET 文件路径

---

### 4. parse_xt

**描述:**
解析 XT XML 翻译文件（xTranslator 格式）。xTranslator 是上古卷轴 (Skyrim) MOD 社区常用的翻译工具，XT XML 为其导出的翻译文件格式。默认创建新翻译集合槽位并激活，也可追加到当前活跃集合。通过 `action="create_slot"` 解析后，后续 `write_back target=xt` 可以自动推断输出目标。

与 `parse_eet` 的区别：见 parse_eet。

**权限:** `write`（产生副作用，通过 PermissionGuard 触发用户确认）

**参数:**
- `path` (必填): XT XML 文件路径
- `action` (可选): `"create_slot"`（默认）— 创建新槽位并激活；`"append"` — 追加到当前活跃集合

**返回:**
- `action="create_slot"`: `{action, label, entry_count, activated}`
- `action="append"`: `{action, added_count, total_count, target_label}`

**使用规则:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活，支持后续 write_back target=xt
- `action="append"`：追加到当前活跃集合，当前必须存在活跃集合
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝
- 通过 `get_app_state` 可查看 `xt_file` 字段确认已解析的 XT 文件路径

---

### 5. parse_sst

**描述:**
解析 SST 二进制翻译文件。SST 是 Skyrim Special Edition 专用的二进制翻译文件格式。SSU8/SSU9 是其中两种子格式：SSU8 为单语言记录格式，SSU9 为双语言多字符串记录格式（含插件名头部）。默认创建新翻译集合槽位并激活，也可追加到当前活跃集合。解析后数据`仅用于查看`意味着：可以将条目导入集合进行浏览、筛选和统计，但**不能**通过 `write_back` 写回 SST 文件（SST 序列化功能当前已实现但被屏蔽）。如需将翻译写回，需通过其他格式（ESP/EET/XT/Strings）导出。

**权限:** `write`（产生副作用，通过 PermissionGuard 触发用户确认）

**参数:**
- `path` (必填): `.sst` 文件路径
- `action` (可选): `"create_slot"`（默认）— 创建新槽位并激活；`"append"` — 追加到当前活跃集合

**返回:**
- `action="create_slot"`: `{action, label, entry_count, activated}`
- `action="append"`: `{action, added_count, total_count, target_label}`

**使用规则:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活
- `action="append"`：追加到当前活跃集合，当前必须存在活跃集合
- 不支持 write_back（SST 序列化功能当前已实现但被屏蔽，如需写回请使用其他格式导出）
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝

---

### 6. import_json

**描述:**
从 JSON 文件导入翻译条目（支持标准格式和 DSD 格式）。默认创建新翻译集合槽位并激活，也可追加到当前活跃集合。

**权限:** `write`（产生副作用，通过 PermissionGuard 触发用户确认）

**参数:**
- `path` (必填): `.json` 文件路径
- `action` (可选): `"create_slot"`（默认）— 创建新槽位并激活；`"append"` — 追加到当前活跃集合

**支持的文件格式:**

标准格式 (Standard):
```json
[{"key": "NPC_:00012345", "original": "Hello", "translation": "你好", "stage": 1, "context": "NPC_:FULL"}]
```
字段说明: `key`(必填, 条目标识符), `original`(必填), `translation`(可选, 默认""), `stage`(可选, 0-5/9/-1), `context`(可选, 默认"")

DSD 格式: 使用嵌套 `data` 结构的字典格式，导入时自动转换为标准格式。具体结构因导出工具版本而异。

**返回:**
- `action="create_slot"`: `{action, label, entry_count, activated}`
- `action="append"`: `{action, added_count, total_count, target_label}`

**使用规则:**
- `action="create_slot"`（默认）：创建新翻译集合槽位并激活
- `action="append"`：追加到当前活跃集合，当前必须存在活跃集合
- 不记录文件路径（无法供后续 write_back 推断 target）
- `path` 包含路径遍历（`../` 或绝对路径）会被拒绝

---

### 7. import_strings（已移除）

> **此工具已从工具集中移除**。`strings_importer` 模块不存在，.strings 文件的导入功能当前不可用。如需导入 .strings 格式的翻译数据，请通过 UI 界面操作（Step1 → 导入Strings 按钮），或先将 .strings 转换为 JSON 格式再使用 `import_json` 工具。
