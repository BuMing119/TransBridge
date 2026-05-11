# Story 11: P1 ParaTranz 平台工具 (paratranz namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: download_entries单阶段重构(O7) +API surface确认(O10)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult`
- Story 02 → `TaskManager`

### 跨 Plan 依赖
- `paratranz/` → `ParatranzClient` 类（API 调用）
- `paratranz/config_manager.py` → `ParatranzConfig`

### 引用的架构决策
- ADR-012: write 权限 + require_confirmation（download/force_upload）
- FR7.6: 全局 401/403 错误处理

## 验收标准

- [ ] `list_projects` — 列出 ParaTranz 项目，permission: read
- [ ] `get_project_info` — 项目详情，permission: read
- [ ] `compare_with_remote` — 对比本地与远程差异（前 20 条详情），permission: read
- [ ] `upload_entries` — 上传条目，is_long_running，permission: write
- [ ] `download_entries` — 下载条目，is_long_running，require_confirmation: true
- [ ] `sync_terms` — 同步术语库，permission: write
- [ ] `export_artifact` — 导出工件，is_long_running，permission: write
- [ ] `get_upload_history` — 上传历史，permission: read
- [ ] 全部注册到 `paratranz` namespace

## 关键接口

```python
# tools/tool_paratranz.py

def _tool_compare_with_remote(args, ctx) -> ToolResult:
    """对比本地集合与 ParaTranz 远程条目的差异。"""
    collection = _get_collection(ctx)
    if not collection: return ToolResult.fail("当前没有加载翻译集合")
    client = ParatranzClient(ParatranzConfig.load_from_file())
    project_id = args.get("project_id") or client.default_project_id
    remote_entries = client.get_entries(project_id)
    # 按 entry.key 对比
    diffs = []
    local_keys = {e.key: e for e in collection}
    for remote_entry in remote_entries[:200]:
        local = local_keys.get(remote_entry.key)
        if not local:
            diffs.append({"type": "remote_only", "key": remote_entry.key, "remote_translation": remote_entry.translation})
        elif local.translation != remote_entry.translation:
            diffs.append({"type": "conflict", "key": remote_entry.key, "local": local.translation[:100], "remote": remote_entry.translation[:100]})
    return ToolResult.ok(data={
        "local_total": len(collection), "remote_total": len(remote_entries),
        "diff_count": len(diffs), "diffs": diffs[:20], "truncated": len(diffs) > 20
    })

def _tool_download_entries(args, ctx) -> ToolResult:
    """下载前强制展示对比摘要。require_confirmation=true。"""
    # 先执行对比
    compare_result = _tool_compare_with_remote(args, ctx)
    compare_data = compare_result.data
    summary = (f"本地 {compare_data['local_total']} 条，远程 {compare_data['remote_total']} 条，"
               f"差异 {compare_data['diff_count']} 条")
    # 返回对比摘要，框架层触发确认弹窗
    return ToolResult.ok(message=f"下载前对比: {summary}", data=compare_data)
```

## 实现步骤

### 步骤 1: 创建 `tool_paratranz.py` + 实现只读工具

**涉及文件**: `tools/tool_paratranz.py`（新建）

**实现要点**:
- `list_projects`: 封装 `ParatranzClient.list_projects()`，过滤 all/mine
- `get_project_info`: 封装 `ParatranzClient.get_project(project_id)`
- `compare_with_remote`: 详见上方关键接口
- `get_upload_history`: 封装客户端的上传历史 API

**边界条件**:
- API 401/403 → 受 FR7.6 全局错误处理拦截
- project_id 不传 → 使用配置文件中的默认项目

---

### 步骤 2: 实现写入工具（upload + download + sync + export）

**涉及文件**: 同上追加

**实现要点**:
- `upload_entries`: 后台线程执行上传，通过 TaskManager 管理
- `download_entries`: 后台线程执行下载，require_confirmation=true
- `sync_terms`: 同步执行术语下载（通常数据量小）
- `export_artifact`: 触发导出+等待完成+下载 zip

**边界条件**:
- 网络超时 → 重试 1 次，仍失败返回错误
- 下载覆盖 → require_confirmation 弹窗展示对比摘要，用户确认后继续
- `force_overwrite` 上传模式 → require_confirmation=true

### 步骤 3: 注册到 paratranz namespace

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_paratranz.py` | 新建 | 8 个 ParaTranz 工具 + 注册 |

## 风险与注意事项

- **注意**: `download_entries` 的对比摘要需足够详细让用户做 informed decision，至少含冲突条目数 + 3-5 条示例
- **注意**: ParaTranz API 调用可能因网络问题长时间阻塞，需设置合理的超时（30s）
