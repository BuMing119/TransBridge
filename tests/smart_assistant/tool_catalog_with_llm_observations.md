# Smart Assistant 工具清单 & LLM 视角返回用例

> 本文档列出 AI 助手可调用的全部 **46 个活跃工具**（不含 5 个已废弃工具），
> 并给出每个工具在调用后 **大模型实际看到的信息**（即 `ToolResult.to_observation()` 输出）。
>
> **注意**：LLM 实际收到的是紧凑 JSON（`separators=(",",":")`），本文档为人类可读性做了缩进美化。

---

## 工具结果编码格式

所有工具返回 `ToolResult`，通过 `to_observation(tool_name, max_chars=2000)` 转为 LLM 可见文本：

```
[OK] tool_name: 人类可读摘要
  data: { <JSON> }
  pagination: { <JSON> }
  suggest: tool1, tool2
  warnings: [ ... ]
```

三种前缀：
- `[OK]` — 成功（`success=True`）
- `[PARTIAL]` — 部分成功（`success=True, partial=True`）
- `[FAIL]` — 失败（`success=False`）

data 大列表自动摘要：`entries`/`projects`/`tasks`/`collections`/`history` 等键值的列表会被替换为 `xxx_count` + `xxx_sample`（前 2 条）。
超出 2000 字符时，末尾追加 `...(truncated)`。

---

# 一、命名空间 `default` — 状态/集合/项目查询（7 个工具）

## 1. `get_app_state` — 应用状态

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] get_app_state: 完成
  data: {
    "active_collection": "Plugin_English",
    "esp_file": "Plugin.esp",
    "eet_file": "BDD_WL.eet",
    "xt_file": "HLIORemi_english_chinese.xml",
    "project": "MyProject",
    "variant": "default",
    "filters": {
      "stage": null,
      "category": null,
      "label": null,
      "search_query": null,
      "search_field": "original"
    },
    "collection_count": 3,
    "has_active_collection": true,
    "paratranz_configured": true
  }
```

### LLM 看到的信息（无活跃集合）

```
[OK] get_app_state: 完成
  data: {
    "active_collection": null,
    "esp_file": "Plugin.esp",
    "eet_file": null,
    "xt_file": null,
    "project": "MyProject",
    "variant": "default",
    "filters": {
      "stage": null,
      "category": null,
      "label": null,
      "search_query": null,
      "search_field": "original"
    },
    "collection_count": 1,
    "has_active_collection": false,
    "paratranz_configured": false
  }
```

---

## 2. `list_collections` — 列出集合

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有多个集合）

```
[OK] list_collections: 已加载 3 个集合
  data: {
    "collections": [
      {
        "key": "esp_0",
        "label": "Plugin_English",
        "esp_name": "Plugin.esp",
        "entry_count": 4521,
        "is_active": true
      },
      {
        "key": "eet_0",
        "label": "BDD_WL",
        "esp_name": null,
        "entry_count": 892,
        "is_active": false
      },
      {
        "key": "json_0",
        "label": "imported",
        "esp_name": null,
        "entry_count": 120,
        "is_active": false
      }
    ]
  }
```

### LLM 看到的信息（成功，空）

```
[OK] list_collections: 已加载 0 个集合
  data: { "collections": [] }
```

---

## 3. `switch_collection` — 切换集合

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `collection_name` (str,可选), `slot_index` (int,可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] switch_collection: 已切换集合: Plugin_English → BDD_WL
  data: { "active_collection": "BDD_WL" }
```

### LLM 看到的信息（失败：未找到）

```
[FAIL] switch_collection: 未找到集合: NonExistent
```

### LLM 看到的信息（失败：未指定）

```
[FAIL] switch_collection: 请指定 collection_name 或 slot_index
```

---

## 4. `get_current_filters` — 当前筛选

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有筛选）

```
[OK] get_current_filters: 当前有 2 个活跃筛选条件
  data: {
    "filter_state": {
      "stage": [0],
      "label": ["急需审核"],
      "category": null,
      "search_query": null,
      "search_field": "original"
    },
    "active_filter_count": 2
  }
```

### LLM 看到的信息（成功，无筛选）

```
[OK] get_current_filters: 当前有 0 个活跃筛选条件
  data: {
    "filter_state": {
      "stage": null,
      "label": null,
      "category": null,
      "search_query": null,
      "search_field": "original"
    },
    "active_filter_count": 0
  }
```

---

## 5. `get_statistics` — 翻译统计

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有数据）

```
[OK] get_statistics: 总计 4521 条，已翻译 3120 条 (69%)
  data: {
    "total": 4521,
    "translated": 3120,
    "untranslated": 1401,
    "translation_rate": 69.0,
    "stage_distribution": {
      "已翻译": 3120,
      "未翻译": 1401,
      "有疑问": 0,
      "已检查": 0,
      "已审核": 0
    },
    "category_distribution": {
      "NPC_": 1520, "INFO": 980, "BOOK": 320, "DIAL": 280,
      "QUST": 210, "ACTI": 180, "FACT": 150, "MGEF": 120,
      "SPEL": 98,  "PERK": 85,  "GLOB": 72,  "WEAP": 65,
      "ARMO": 58,  "AMMO": 42,  "ALCH": 38,  "SCRL": 32,
      "MISC": 28,  "CONT": 25,  "FLOR": 22,  "INGR": 15
    }
  }
```

### LLM 看到的信息（成功，空集合）

```
[OK] get_statistics: 当前未加载翻译集合
  data: { "total": 0, "translated": 0 }
```

---

## 6. `list_local_projects` — 本地项目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] list_local_projects: 共 2 个本地项目
  data: {
    "projects": [
      { "name": "MyTranslation" },
      { "name": "LegacyProject" }
    ]
  }
```

### LLM 看到的信息（成功，空）

```
[OK] list_local_projects: 共 0 个本地项目
  data: { "projects": [] }
```

---

## 7. `get_current_project` — 当前项目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] get_current_project: 完成
  data: {
    "name": "MyTranslation",
    "variant": "default",
    "collection": "Plugin_English"
  }
```

### LLM 看到的信息（无活跃项目）

```
[OK] get_current_project: 当前无活跃项目
  data: { "active_project": null }
```

---

# 二、命名空间 `editor` — 筛选/搜索/编辑/标签（14 个工具）

## 8. `filter_by_stage` — 按阶段筛选

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `stages` (list[int], 必填) — 0=未翻译 1=已翻译 2=有疑问 3=已检查 5=已审核 9=已锁定 -1=已隐藏 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] filter_by_stage: 已按阶段筛选: [0, 2]
  data: { "stages": [0, 2] }
  suggest: get_visible_entries, get_statistics
```

### LLM 看到的信息（失败：非法 stage）

```
[FAIL] filter_by_stage: 无效的 stage 值: [99]，合法值: [-1, 0, 1, 2, 3, 5, 9]
```

---

## 9. `filter_by_category` — 按分类筛选

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `categories` (list[str], 必填) — 如 `["NPC_", "INFO", "BOOK"]` |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] filter_by_category: 已按分类筛选: ['NPC_', 'INFO']
  data: { "categories": ["NPC_", "INFO"] }
  suggest: get_visible_entries, get_statistics
```

---

## 10. `filter_by_label` — 按标签筛选

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `label_names` (list[str], 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] filter_by_label: 已按标签筛选: ['急需审核']
  data: { "labels": ["急需审核"] }
  suggest: get_visible_entries, get_statistics
```

---

## 11. `search_entries` — 搜索条目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `query` (str, 必填), `field` (str, 可选, 默认"original") — id/key/original/translation/context/all |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] search_entries: 已搜索: 'dragon' (字段: original)
  data: { "query": "dragon", "field": "original" }
  suggest: get_visible_entries
```

### LLM 看到的信息（失败：非法字段）

```
[FAIL] search_entries: 无效的搜索字段: tag，可选: id, key, original, translation, context, all
```

---

## 12. `clear_all_filters` — 清除筛选

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] clear_all_filters: 已清除所有筛选条件
  data: { "filters_cleared": true }
  suggest: get_visible_entries, get_statistics, filter_by_stage
```

---

## 13. `get_visible_entries` — 获取可见条目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `limit` (int, 可选, 默认50, 最大200), `offset` (int, 可选, 默认0) |
| 需确认 | 否 |

### LLM 看到的信息（成功，小结果集 — 无截断）

```
[OK] get_visible_entries: 显示 12 条
  data: {
    "entries": [
      {
        "id": "NPC_001",
        "key": "NPC_001_name",
        "original": "Whiterun Guard",
        "translation": "雪漫卫兵",
        "stage": 1
      },
      {
        "id": "NPC_002",
        "key": "NPC_002_name",
        "original": "Stormcloak Soldier",
        "translation": "风暴斗篷士兵",
        "stage": 1
      },
      {
        "id": "INFO_001",
        "key": "INFO_001_text",
        "original": "I used to be an adventurer like you...",
        "translation": "我曾经也和你一样是个冒险者...",
        "stage": 1
      },
      {
        "id": "INFO_002",
        "key": "INFO_002_text",
        "original": "Let me guess, someone stole your sweetroll?",
        "translation": "",
        "stage": 0
      },
      {
        "id": "BOOK_001",
        "key": "BOOK_001_title",
        "original": "The Lusty Argonian Maid",
        "translation": "粗野的亚龙人女仆",
        "stage": 1
      },
      {
        "id": "QUST_001",
        "key": "QUST_001_name",
        "original": "The Golden Claw",
        "translation": "黄金龙爪",
        "stage": 1
      },
      {
        "id": "QUST_002",
        "key": "QUST_002_obj",
        "original": "Retrieve the Dragonstone",
        "translation": "取回龙石",
        "stage": 1
      }
    ],
    "total_count": 12,
    "truncated": false
  }
  pagination: {
    "page": 1,
    "page_size": 50,
    "total_count": 12,
    "returned_count": 12,
    "has_more": false
  }
  suggest: select_entries, edit_translation, set_stage
```

### LLM 看到的信息（成功，结果被截断 — 数据超限）

> 此时 `_serialize_data()` 触发大数据摘要，entries 列表被替换为 `entries_count` + `entries_sample`。

```
[OK] get_visible_entries: 显示 50 条（共 2340 条，已截断）
  data: {
    "entries_count": 2340,
    "entries_sample": [
      {
        "id": "NPC_001",
        "key": "NPC_001_name",
        "original": "Whiterun Guard",
        "translation": "雪漫卫兵",
        "stage": 1
      },
      {
        "id": "NPC_002",
        "key": "NPC_002_name",
        "original": "Stormcloak Soldier",
        "translation": "风暴斗篷士兵",
        "stage": 1
      }
    ],
    "total_count": 2340,
    "truncated": true
  }
  pagination: {
    "page": 1,
    "page_size": 50,
    "total_count": 2340,
    "returned_count": 50,
    "has_more": true
  }
  suggest: get_visible_entries, search_entries
  truncated: true
```

### LLM 看到的信息（成功，空结果）

```
[OK] get_visible_entries: 显示 0 条
  data: {
    "entries_count": 0,
    "total_count": 0,
    "truncated": false
  }
  pagination: {
    "page": 1,
    "page_size": 50,
    "total_count": 0,
    "returned_count": 0,
    "has_more": false
  }
```

---

## 14. `select_entries` — 选择条目

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_ids` (list[str], 必填), `action` (str, 可选, select/deselect/clear, 默认select) |
| 需确认 | 否 |

### LLM 看到的信息（成功，选中）

```
[OK] select_entries: 选中完成，当前已选 15 条
  data: { "selected_count": 15 }
```

### LLM 看到的信息（成功，取消选中）

```
[OK] select_entries: 取消选中完成，当前已选 8 条
  data: { "selected_count": 8 }
```

### LLM 看到的信息（成功，清空）

```
[OK] select_entries: 清空选择完成，当前已选 0 条
  data: { "selected_count": 0 }
```

### LLM 看到的信息（失败）

```
[FAIL] select_entries: 无效操作: delete，可选: select, deselect, clear
```

---

## 15. `edit_translation` — 编辑翻译

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_id` (str, 必填), `new_translation` (str, 必填), `new_stage` (int, 可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功，修改译文+stage）

```
[OK] edit_translation: 已更新 NPC_001
  data: {
    "entry_id": "NPC_001",
    "old_translation": "雪漫卫兵",
    "new_translation": "白漫城守卫",
    "stage": 1,
    "stage_changed": false
  }
```

### LLM 看到的信息（成功，仅修改译文，stage 不变）

```
[OK] edit_translation: 已更新 NPC_002
  data: {
    "entry_id": "NPC_002",
    "old_translation": "",
    "new_translation": "风暴斗篷士兵",
    "stage": 0,
    "stage_changed": false
  }
```

### LLM 看到的信息（成功，同时修改 stage）

```
[OK] edit_translation: 已更新 INFO_001
  data: {
    "entry_id": "INFO_001",
    "old_translation": "",
    "new_translation": "我曾经也和你一样是个冒险者...",
    "stage": 1,
    "stage_changed": true
  }
```

### LLM 看到的信息（失败）

```
[FAIL] edit_translation: 条目不存在: NPC_99999
```

---

## 16. `set_stage` — 批量设置阶段

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_ids` (list[str], 必填), `stage` (int, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功，全部更新）

```
[OK] set_stage: 已将 50 条条目设为 stage=1
  data: { "updated_count": 50 }
```

### LLM 看到的信息（部分成功）

```
[PARTIAL] set_stage: 已将 48 条条目设为 stage=1（2 条未找到）
  data: {
    "updated_count": 48,
    "not_found": ["NPC_99999", "INFO_88888"]
  }
  failed: 2 items
  failed_details: [
    { "entry_id": "NPC_99999", "reason": "条目不存在" },
    { "entry_id": "INFO_88888", "reason": "条目不存在" }
  ]
```

### LLM 看到的信息（失败：非法 stage）

```
[FAIL] set_stage: 无效的 stage 值: 99，合法值: [-1, 0, 1, 2, 3, 5, 9]
```

---

## 17. `list_labels` — 列出标签

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有标签）

```
[OK] list_labels: 共 3 个标签
  data: {
    "labels": [
      { "id": "a1b2c3d4", "name": "急需审核", "color": "#F56C6C", "count": 23 },
      { "id": "e5f6g7h8", "name": "机翻可疑", "color": "#E6A23C", "count": 5 },
      { "id": "i9j0k1l2", "name": "已确认",   "color": "#67C23A", "count": 340 }
    ]
  }
```

### LLM 看到的信息（成功，空标签库）

```
[OK] list_labels: 标签库为空，请先创建标签
  data: { "labels": [] }
```

---

## 18. `create_label` — 创建标签

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `name` (str, 必填), `color` (str, 可选, 默认"#409EFF") |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] create_label: 已创建标签: 急需审核
  data: {
    "label_id": "a1b2c3d4",
    "name": "急需审核",
    "color": "#F56C6C"
  }
```

### LLM 看到的信息（失败）

```
[FAIL] create_label: 标签名不能为空
```

---

## 19. `assign_label` — 分配标签

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_ids` (list[str], 必填), `label_name` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] assign_label: 已为 5 条条目分配标签 '急需审核'
  data: { "assigned_count": 5 }
```

### LLM 看到的信息（失败：标签不存在）

```
[FAIL] assign_label: 标签不存在: 不存在标签名
```

---

## 20. `remove_label` — 移除标签

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_ids` (list[str], 必填), `label_name` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] remove_label: 已从 3 条条目移除标签 '急需审核'
  data: { "removed_count": 3 }
```

---

## 21. `batch_assign_label` — 批量分配标签

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `label_name` (str, 必填) |
| 需确认 | **是** |

### LLM 看到的信息（成功）

```
[OK] batch_assign_label: 已为筛选范围内 1401 条条目批量分配标签 '待翻译'
  data: {
    "assigned_count": 1401,
    "filter_total": 1401
  }
```

---

# 三、命名空间 `translator` — 翻译执行控制（9 个工具）

## 22. `start_translation` — 启动翻译

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `mode` (str, 可选, translate/polish/mixed, 默认translate), `entry_ids` (list[str], 可选) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功，指定模式）

```
[OK] start_translation: 翻译任务已启动 (mode=translate)
  data: {
    "task_id": "task_1715800000_abc123",
    "mode": "translate"
  }
```

### LLM 看到的信息（成功，指定条目列表）

```
[OK] start_translation: 翻译任务已启动 (mode=mixed)
  data: {
    "task_id": "task_1715800100_def456",
    "mode": "mixed",
    "scope": {
      "stages": [0],
      "labels": [],
      "categories": [],
      "action": "include"
    }
  }
```

### LLM 看到的信息（失败：API Key 未配置）

```
[FAIL] start_translation: API Key 未配置
  error_category: config
  error_code: API_KEY_MISSING
  recovery_action: 请在 AI 翻译设置中配置 API Key
```

### LLM 看到的信息（失败：非法模式）

```
[FAIL] start_translation: 无效模式: rewrite，可选: translate, polish, mixed
```

### LLM 看到的信息（失败：集合未加载）

```
[FAIL] start_translation: 当前没有加载翻译集合
  error_category: input
  error_code: COLLECTION_NOT_LOADED
```

---

## 23. `start_polish` — 启动润色

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `entry_ids` (list[str], 必填), `intensity` (str, 可选, light/medium/heavy, 默认medium) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] start_polish: 润色任务已启动 (intensity=medium, 50条)
  data: {
    "task_id": "task_1715800200_ghi789",
    "intensity": "medium",
    "entry_count": 50
  }
```

### LLM 看到的信息（失败：未指定条目）

```
[FAIL] start_polish: 请指定要润色的 entry_ids
```

---

## 24. `stop_task` — 停止任务

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `task_id` (str, 必填) |
| 需确认 | **是** |

### LLM 看到的信息（成功）

```
[OK] stop_task: 任务 task_1715800000_abc123 已发送停止信号
  data: {
    "task_id": "task_1715800000_abc123",
    "stopped": true
  }
```

### LLM 看到的信息（失败：未指定）

```
[FAIL] stop_task: 请指定要停止的 task_id（使用 stop_all_tasks 停止所有任务）
```

### LLM 看到的信息（失败：任务不存在）

```
[FAIL] stop_task: 任务不存在或已完成: task_gone
  data: {
    "task_id": "task_gone",
    "stopped": false
  }
```

---

## 25. `stop_all_tasks` — 停止所有任务

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] stop_all_tasks: 已停止 2 个任务
  data: { "stopped_count": 2 }
```

### LLM 看到的信息（成功，无活跃任务）

```
[OK] stop_all_tasks: 已停止 0 个任务
  data: { "stopped_count": 0 }
```

---

## 26. `get_task_status` — 查询任务状态

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `task_id` (str, 可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功，指定 task_id — 运行中）

```
[OK] get_task_status: 任务 task_1715800000_abc123: running
  data: {
    "status": "running",
    "progress": {
      "current": 120,
      "total": 1401,
      "message": "正在翻译 NPC_121..."
    },
    "metadata": {
      "mode": "translate",
      "type": "translation"
    }
  }
```

### LLM 看到的信息（成功，指定 task_id — 已完成）

```
[OK] get_task_status: 任务 task_1715800000_abc123: completed
  data: {
    "status": "completed",
    "success_count": 1398,
    "failed_count": 3,
    "skipped_count": 0,
    "metadata": {
      "mode": "translate",
      "type": "translation"
    }
  }
```

### LLM 看到的信息（成功，不传 task_id — 所有任务摘要）

```
[OK] get_task_status: 活跃任务: 1 / 总任务: 3
  data: {
    "active_count": 1,
    "total_count": 3,
    "tasks": [
      {
        "task_id": "task_001",
        "status": "completed",
        "metadata": { "mode": "translate" }
      },
      {
        "task_id": "task_002",
        "status": "running",
        "metadata": { "mode": "polish" }
      },
      {
        "task_id": "task_003",
        "status": "cancelled",
        "metadata": { "mode": "translate" }
      }
    ]
  }
```

---

## 27. `get_translation_config` — 翻译配置

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，完整配置）

```
[OK] get_translation_config: 完成
  data: {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key_configured": true,
    "temperature": 0.3,
    "max_tokens": 4096,
    "target_lang": "chinese",
    "game_profile": "skyrim_se",
    "term_priority": ["paratranz", "dynamic", "json"],
    "post_process": {
      "enabled": true,
      "consistency_check": true,
      "format_validation": true,
      "quality_gate": true,
      "refinement": true,
      "polish": false,
      "arbitration": true
    },
    "term_database": {
      "path": "data/Plugin_terms.json",
      "entry_count": 350
    },
    "paratranz": {
      "token_configured": true,
      "api_url": "https://paratranz.example.com/api"
    },
    "available_profiles": ["openai", "anthropic", "local"],
    "base_url_host": "api.openai.com"
  }
```

### LLM 看到的信息（成功，API Key 未配置）

```
[OK] get_translation_config: 完成
  data: {
    "provider": "openai",
    "model": "gpt-4o",
    "api_key_configured": false,
    "temperature": 0.3,
    "max_tokens": 4096,
    "target_lang": "chinese",
    "game_profile": "skyrim_se",
    "term_priority": ["paratranz", "dynamic", "json"],
    "post_process": {
      "enabled": true,
      "consistency_check": true,
      "format_validation": true,
      "quality_gate": true,
      "refinement": true,
      "polish": false,
      "arbitration": true
    },
    "term_database": {
      "path": null,
      "entry_count": 0
    },
    "paratranz": {
      "token_configured": false,
      "api_url": "https://paratranz.example.com/api"
    },
    "available_profiles": ["openai", "anthropic", "local"],
    "base_url_host": "api.openai.com"
  }
```

---

## 28. `set_translation_config` — 设置翻译配置

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `profile` (str, 可选), `model` (str, 可选), `temperature` (float, 可选), `max_tokens` (int, 可选), `target_lang` (str, 可选), `game_profile` (str, 可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功，切换 profile）

```
[OK] set_translation_config: 已更新配置: (profile=anthropic)
  data: {
    "changed_fields": [],
    "profile": "anthropic"
  }
```

### LLM 看到的信息（成功，修改参数）

```
[OK] set_translation_config: 已更新配置: model, temperature
  data: {
    "changed_fields": ["model", "temperature"],
    "profile": null
  }
```

### LLM 看到的信息（成功，无变更）

```
[OK] set_translation_config: 未做任何修改
```

### LLM 看到的信息（失败：未知 profile）

```
[FAIL] set_translation_config: 未知 profile: deepseek。可用方案: ['openai', 'anthropic', 'local']
```

---

## 29. `set_scope` — 设置作用域

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `stages` (list[int], 可选), `labels` (list[str], 可选), `categories` (list[str], 可选), `action` (str, 可选, include/exclude/only, 默认include) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] set_scope: 翻译作用域已更新: action=include
  data: {
    "stages": [0],
    "labels": [],
    "categories": [],
    "action": "include"
  }
```

### LLM 看到的信息（成功，指定多条件）

```
[OK] set_scope: 翻译作用域已更新: action=include
  data: {
    "stages": [0, 2],
    "labels": ["急需翻译"],
    "categories": ["NPC_", "INFO"],
    "action": "include"
  }
```

---

## 30. `get_scope_preview` — 作用域预览

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] get_scope_preview: 作用域匹配: 1401/4521 条
  data: {
    "matched": 1401,
    "total": 4521,
    "scope": {
      "stages": [0],
      "labels": [],
      "categories": [],
      "action": "include"
    }
  }
```

### LLM 看到的信息（成功，无集合）

```
[OK] get_scope_preview: 当前无翻译集合
  data: { "matched": 0, "total": 0 }
```

---

# 四、命名空间 `proofreader` — 后处理/质量（6 个工具）

## 31. `run_consistency_check` — 一致性检查

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] run_consistency_check: 术语一致性检查已启动
  data: { "task_id": "task_1715800300_jkl012" }
```

### LLM 看到的信息（失败：无集合）

```
[FAIL] run_consistency_check: 当前没有加载翻译集合
```

---

## 32. `run_format_validation` — 格式校验

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] run_format_validation: 格式校验已启动
  data: { "task_id": "task_1715800400_mno345" }
```

---

## 33. `run_llm_refinement` — LLM 修复

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | 无 |
| 需确认 | **是**（预估费用） |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] run_llm_refinement: LLM修复已启动
  data: { "task_id": "task_1715800500_pqr678" }
```

---

## 34. `run_llm_polish` — LLM 润色

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | 无 |
| 需确认 | **是**（预估费用） |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] run_llm_polish: LLM润色已启动
  data: { "task_id": "task_1715800600_stu901" }
```

---

## 35. `run_llm_arbitration` — LLM 裁决

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | 无 |
| 需确认 | **是**（预估费用） |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] run_llm_arbitration: LLM裁决已启动
  data: { "task_id": "task_1715800700_vwx234" }
```

---

## 36. `get_quality_report` — 质量报告

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有报告）

```
[OK] get_quality_report: 最近报告(术语一致性检查): 检查4521条, 发现问题23个, 自动修复18个
  data: {
    "reports": [
      {
        "phase": "术语一致性检查",
        "total_checked": 4521,
        "issue_count": 23,
        "auto_fixed": 18,
        "needs_review": [
          "NPC_045",
          "INFO_102",
          "BOOK_003",
          "QUST_007",
          "DIAL_012"
        ],
        "issues": [
          {
            "entry_id": "NPC_045",
            "issue_type": "term_inconsistency",
            "severity": "medium",
            "message": "术语 'Whiterun' 翻译不一致: 雪漫/白漫城"
          }
        ],
        "timestamp": 1715800800.123
      }
    ]
  }
```

### LLM 看到的信息（成功，无报告）

```
[OK] get_quality_report: 暂无质量报告
  data: { "reports": [] }
```

---

# 五、命名空间 `paratranz` — ParaTranz 平台集成（10 个工具）

## 37. `list_projects` — 列出项目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `view` (str, 可选, all/mine, 默认mine) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] list_projects: 找到 5 个项目
  data: {
    "projects": [
      { "id": 123, "name": "Skyrim SE 简体中文",        "visibility": "public" },
      { "id": 456, "name": "Legacy of the Dragonborn",  "visibility": "private" },
      { "id": 789, "name": "Beyond Skyrim - Bruma",     "visibility": "public" },
      { "id": 101, "name": "Falskaar",                  "visibility": "public" },
      { "id": 202, "name": "Wyrmstooth",                "visibility": "private" }
    ]
  }
```

### LLM 看到的信息（失败）

```
[FAIL] list_projects: 获取项目列表失败: Connection refused
```

---

## 38. `get_project_info` — 项目信息

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `project_id` (str, 可选, 不传则用当前选中项目) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] get_project_info: 完成
  data: {
    "id": 123,
    "name": "Skyrim SE 简体中文",
    "visibility": "public",
    "member_count": 8
  }
```

### LLM 看到的信息（失败：未指定）

```
[FAIL] get_project_info: 请指定 project_id
```

---

## 39. `compare_with_remote` — 对比远程

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `project_id` (str, 可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] compare_with_remote: 对比完成: 仅本地15 仅远程3 不同8
  data: {
    "only_local": 15,
    "only_remote": 3,
    "different": 8,
    "same": 4495,
    "details": [
      { "key": "NPC_001_name",  "status": "different" },
      { "key": "INFO_005_text", "status": "only_local" },
      { "key": "BOOK_008_title", "status": "only_local" }
    ]
  }
```

---

## 40. `upload_entries` — 上传条目

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `project_id` (str, 可选), `force_overwrite` (bool, 可选, 默认false), `entry_ids` (list[str], 可选, 默认全部) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功，全部上传）

```
[OK] upload_entries: 已上传 4521/4521 条
  data: {
    "uploaded": 4521,
    "total": 4521,
    "failed_items": []
  }
```

### LLM 看到的信息（成功，部分失败）

```
[OK] upload_entries: 已上传 4518/4521 条，失败 3 条
  data: {
    "uploaded": 4518,
    "total": 4521,
    "failed_items": [
      { "key": "NPC_99999",  "error": "HTTP 404" },
      { "key": "INFO_88888", "error": "Timeout" },
      { "key": "BOOK_77777", "error": "Rate limit" }
    ]
  }
```

---

## 41. `download_entries` — 下载条目

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `project_id` (str, 可选) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功，有对比摘要）

```
[OK] download_entries: 已下载 4521 条（新增15 更新8）
  data: {
    "downloaded_count": 4521,
    "diff_summary": {
      "new_from_remote": 15,
      "updated": 8
    }
  }
```

### LLM 看到的信息（成功，无本地集合对比）

```
[OK] download_entries: 已下载 4521 条
  data: {
    "downloaded_count": 4521,
    "diff_summary": null
  }
```

---

## 42. `sync_terms` — 同步术语

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `project_id` (str, 可选) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] sync_terms: 已获取 350 个术语
  data: { "term_count": 350 }
```

### LLM 看到的信息（成功，空）

```
[OK] sync_terms: 已获取 0 个术语
  data: { "term_count": 0 }
```

---

## 43. `export_artifact` — 导出工件

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `project_id` (str, 可选) |
| 需确认 | 否 |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] export_artifact: 工件导出请求已提交
  data: {
    "id": "export_12345",
    "status": "processing",
    "download_url": "https://paratranz.example.com/exports/12345.zip"
  }
```

---

## 44. `get_upload_history` — 上传历史

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `project_id` (str, 可选), `limit` (int, 可选, 默认20) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] get_upload_history: 完成
  data: {
    "history": [
      {
        "id": 1,
        "time": "2026-05-15T10:30:00Z",
        "entry_count": 100,
        "user": "admin"
      },
      {
        "id": 2,
        "time": "2026-05-14T18:00:00Z",
        "entry_count": 50,
        "user": "admin"
      }
    ]
  }
```

---

## 45. `get_paratranz_project` — PT 当前项目

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | 无 |
| 需确认 | 否 |

### LLM 看到的信息（成功，有选中项目）

```
[OK] get_paratranz_project: 当前 ParaTranz 项目: Skyrim SE 简体中文 (id=123)
  data: {
    "id": 123,
    "name": "Skyrim SE 简体中文",
    "visibility": "public"
  }
```

### LLM 看到的信息（成功，未选中）

```
[OK] get_paratranz_project: 未选择 ParaTranz 项目
  data: { "selected_project": null }
```

---

## 46. `switch_paratranz_project` — 切换 PT 项目

| 属性 | 值 |
|------|-----|
| 权限 | write |
| 参数 | `project_id` (int, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] switch_paratranz_project: 已切换到项目: Legacy of the Dragonborn (id=456)
  data: {
    "id": 456,
    "name": "Legacy of the Dragonborn",
    "visibility": "private"
  }
```

### LLM 看到的信息（失败：项目不存在）

```
[FAIL] switch_paratranz_project: 切换项目失败: Project not found: 99999
```

---

# 六、命名空间 `parser` — 文件解析（6 个工具）

## 47. `parse_esp` — 解析 ESP

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] parse_esp: 已解析 ESP: Plugin.esp
  data: { "entry_count": 4521 }
```

### LLM 看到的信息（失败：文件不存在）

```
[FAIL] parse_esp: 文件不存在: Plugin.esp
```

### LLM 看到的信息（失败：非法扩展名）

```
[FAIL] parse_esp: 不支持的文件类型: .txt，允许: ['.esp', '.esl', '.esm', '.json', '.sst', '.strings', '.xml']
```

### LLM 看到的信息（失败：绝对路径 / 路径遍历）

```
[FAIL] parse_esp: 不允许使用绝对路径
[FAIL] parse_esp: 拒绝路径遍历攻击
```

---

## 48. `parse_eet` — 解析 EET

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] parse_eet: 已解析 EET: BDD_WL.eet
  data: { "entry_count": 892 }
```

---

## 49. `parse_xt` — 解析 XT

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] parse_xt: 已解析 XT: HLIORemi_english_chinese.xml
  data: { "entry_count": 385 }
```

---

## 50. `parse_sst` — 解析 SST

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] parse_sst: 已解析 SST: ccbgssse007-chrysamere_english_chinese.sst
  data: { "entry_count": 24 }
```

---

## 51. `import_json` — 导入 JSON

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] import_json: 已从 JSON 导入 500 条条目
  data: { "entry_count": 500 }
```

---

## 52. `import_strings` — 导入 Strings

| 属性 | 值 |
|------|-----|
| 权限 | read |
| 参数 | `path` (str, 必填) |
| 需确认 | 否 |

### LLM 看到的信息（成功）

```
[OK] import_strings: 已从 strings 导入 120 条
  data: { "entry_count": 120 }
```

---

# 七、命名空间 `writer` — 文件写回（4 个工具）

**全部为 admin 权限 + 需确认 + 长运行。**

## 53. `write_to_esp` — 写回 ESP

| 属性 | 值 |
|------|-----|
| 权限 | admin |
| 参数 | `path` (str, 可选, 不传则用当前 ESP 路径) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] write_to_esp: 已写回 4521 条译文到 ESP
  data: {
    "written_count": 4521,
    "path": "Plugin.esp"
  }
```

### LLM 看到的信息（失败：无活跃槽位）

```
[FAIL] write_to_esp: 没有活跃的集合槽位
```

### LLM 看到的信息（失败：无插件）

```
[FAIL] write_to_esp: 当前槽位无已解析的插件
```

---

## 54. `write_to_eet` — 写回 EET

| 属性 | 值 |
|------|-----|
| 权限 | admin |
| 参数 | `path` (str, 可选, 不传则用已解析的 EET 源路径) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] write_to_eet: 已写回译文到 EET XML
  data: { "path": "BDD_WL.eet" }
```

### LLM 看到的信息（失败：无路径）

```
[FAIL] write_to_eet: 请提供 EET 输出路径或先解析 EET 源文件
```

---

## 55. `write_to_xt` — 写回 XT

| 属性 | 值 |
|------|-----|
| 权限 | admin |
| 参数 | `path` (str, 可选, 不传则用已解析的 XT 源路径) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功）

```
[OK] write_to_xt: 已写回译文到 XT XML
  data: { "path": "HLIORemi_english_chinese.xml" }
```

---

## 56. `write_to_strings` — 写回 Strings

| 属性 | 值 |
|------|-----|
| 权限 | admin |
| 参数 | `path` (str, 可选, 别名 output_dir) |
| 需确认 | **是** |
| 长运行 | 是 |

### LLM 看到的信息（成功，输出多个 strings 文件）

```
[OK] write_to_strings: 已写回 4521 条译文到 3 个 strings 文件
  data: {
    "written_count": 4521,
    "strings_files": 3
  }
```

### LLM 看到的信息（失败：无路径）

```
[FAIL] write_to_strings: 请提供输出路径 (path 或 output_dir)
```

---

# 附 A：废弃工具（5 个，仅列出供参考）

| 工具名 | 命名空间 | 替代工具 |
|--------|----------|---------|
| `lookup_terms` | translator | 使用 `search_entries` |
| `translate_entries` | translator | `start_translation` |
| `check_quality` | proofreader | `run_consistency_check` / `run_format_validation` |
| `export_json` | default | （无直接替代，已移除） |
| `write_back` | default | `write_to_esp` / `write_to_eet` / `write_to_xt` |

所有废弃工具 `deprecated=True`，LLM 在 system prompt 中会看到标注，不应用于新调用。

---

# 附 B：`get_task_status` 返回的任务状态流转

工具后台任务（翻译/润色/后处理）通过 `TaskManager` 管理，`get_task_status` 返回的状态字段流转如下：

```
pending  →  running  →  completed
                    →  failed
                    →  cancelled
```

`running` 状态下 `progress` 字段包含：

```json
{
    "current": 120,
    "total": 1401,
    "message": "正在翻译 NPC_121...",
    "success_count": 100,
    "failed_count": 0
}
```

---

# 附 C：路径安全规则

所有 `parser` 和 `writer` 工具共享路径校验逻辑（`_validate_path`）：

| 规则 | parser（输入） | writer（输出） |
|------|:---:|:---:|
| 禁止绝对路径 | ✓ | ✓ |
| 禁止 `..` 路径遍历 | ✓ | ✓ |
| 扩展名白名单（`.esp/.esm/.esl/.xml/.json/.strings/.sst`） | ✓ | ✗ |
| 文件必须存在 | ✓ | ✗ |

输出端不检查扩展名和存在性，因为写入目标可能尚不存在。

---

# 附 D：`ToolResult` 完整字段说明

```python
@dataclass
class ToolResult:
    success: bool                      # 执行是否成功
    message: str                       # 人类可读摘要
    data: dict[str, Any] | None        # 结构化数据
    partial: bool = False              # 是否部分成功
    failed_items: list[dict] | None    # 失败条目详情
    truncated: bool = False            # 输出是否被截断
    error_category: str | None         # "network"|"auth"|"input"|"permission"|"config"|"internal"
    error_code: str | None             # 错误码，如 "API_KEY_MISSING"
    recovery_action: str | None        # 建议的恢复操作
    warnings: list[str] | None         # 非致命警告
    pagination: dict | None            # 分页信息
    execution_meta: dict | None        # 执行元数据
    tool_suggestions: list[str] | None # 推荐的后续工具
```

`to_observation()` 序列化规则：
- 只输出非空/非 None 的字段
- data 用紧凑 JSON（`separators=(",",":")`），人类不可读，但节省 token
- 超过 2000 字符时智能截断
- 大列表（entries/projects/tasks 等）自动摘要为 count + sample
