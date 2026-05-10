# Story 05: 自动保存与启动恢复

**所属方案**: `plans/project-persistence/plan.md`
**技术模块**: `src/transbridge/ui/` (修改)
**状态**: 已确认
**创建日期**: 2026-05-08

## 前置依赖

### 上游 Story
- Story-04（同 plan）：版本切换已实现，VariantStore.save/load 可用

### 引用的架构决策
- ADR-006: workspace.json last_session 结构、自动保存策略

## 验收标准

- [ ] 启动时读取 workspace.json，恢复上次活跃项目+版本
- [ ] 启动时重新解析源文件，加载对应版本的 current.json
- [ ] 启动时恢复 Step2 筛选状态（stage/category/search）
- [ ] 定时自动保存：可配置间隔（默认 5 分钟），后台 QTimer
- [ ] 操作触发自动保存：编辑译文/修改标签后防抖 2 秒
- [ ] 脏标记管理：无修改时不触发保存
- [ ] 关闭应用时自动保存当前版本
- [ ] 手动保存入口：工具栏「保存」按钮 / Ctrl+S

## 数据流

```
启动恢复:
  MainWindow.__init__()
    → _restore_workspace()
        ├─ WorkspaceState.load("data/workspace.json")
        ├─ ws.active_project → ProjectHandle.load()
        │     ├─ 解析源文件 (ApiWorker 后台)
        │     ├─ VariantStore.load(proj.variant_dir(proj.active_variant) / "current.json")
        │     ├─ VariantStore.apply_to(entries)
        │     ├─ Step2 恢复筛选 (ws.last_session.step2_filter_*)
        │     └─ Step2 恢复 label_library (VariantStore.label_library)
        └─ ws.active_project 为空 → 空白启动

自动保存:
  AutoSaveManager:
    ├─ QTimer(interval=settings.auto_save_interval_minutes * 60000)
    │     → timeout → _auto_save()
    └─ QTimer(singleShot, interval=2000)  # 防抖
          → 操作触发 → restart → timeout → _auto_save()

  _auto_save():
    if not ctx.variant_store or not ctx.variant_store.dirty:
        return
    ctx.variant_store.collect_from(entries, entry_labels, label_library)
    ctx.variant_store.save()
    ctx.variant_store.dirty = False
    status_bar → "已自动保存"
```

## 关键接口

```python
class AutoSaveManager(QObject):
    """管理自动保存定时器"""
    
    def __init__(self, ctx: AppContext, parent=None):
        self._ctx = ctx
        self._interval_timer = QTimer(self)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._interval_timer.timeout.connect(self._auto_save)
        self._debounce_timer.timeout.connect(self._auto_save)
    
    def start(self, interval_minutes: int = 5): ...
    def stop(self): ...
    def trigger_debounce(self):
        """操作触发防抖——重启 2s 定时器"""
        self._debounce_timer.start(2000)
    
    def _auto_save(self):
        if not self._ctx.variant_store or not self._ctx.variant_store.dirty:
            return
        # 需要从 Step2 收集数据
        # variant_store.collect_from(entries, entry_labels, label_library)
        self._ctx.variant_store.save()
        self._ctx.variant_store.dirty = False

class MainWindow:
    def _restore_workspace(self) -> None:
        """启动恢复：workspace → project → variant → data → UI"""
    
    def _save_session_state(self) -> None:
        """保存会话状态到 workspace.last_session"""
        ws = self._ctx.workspace
        if ws:
            ws.last_session["project"] = self._ctx.active_project.name if self._ctx.active_project else None
            ws.last_session["variant"] = self._ctx.active_variant
            # ws.last_session["step2_filter_stage"] = ...
            ws.save()
```

## 实现步骤

### 步骤 1: 启动恢复流程

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- `_restore_workspace()` 在 `__init__()` 末尾调用（Config 加载后、UI 初始化后）
- 恢复流程：workspace → project → 解析源文件（异步）→ VariantStore → 筛选状态 → UI
- 解析源文件复用现有 `_run_parse_esp` 等逻辑
- 恢复失败（文件损坏/路径变更）→ 提示用户，以空白状态继续

**边界条件**:
- workspace.json 不存在（首次启动）→ 跳过恢复
- project.json 不存在（项目被删除）→ 清除活跃引用，提示用户
- 源文件路径变更 → 提示重新定位

### 步骤 2: AutoSaveManager 实现

**涉及文件**: `src/transbridge/ui/main_window.py`（修改，内嵌或独立文件）

**实现要点**:
- 定时器间隔从 workspace.settings.auto_save_interval_minutes 读取
- 防抖定时器 2s，每次编辑/标签操作后调用 trigger_debounce()
- 仅 dirty=True 时执行实际保存
- 状态栏显示"已自动保存"提示（3 秒后消失）

**边界条件**:
- variant_store 为 None → 跳过
- 保存过程中用户继续编辑 → 下次防抖触发时再保存（不阻塞 UI）
- 关闭应用时 stop() 定时器，在 closeEvent 中执行最终保存

### 步骤 3: Ctrl+S 手动保存

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 快捷键 Ctrl+S → `_on_manual_save()`
- 工具栏保存按钮
- 手动保存强制立即执行（不经过防抖）

### 步骤 4: 筛选状态恢复 + 会话状态保存

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）, `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 关闭时 `_save_session_state()`：保存当前筛选状态到 workspace.last_session
- 启动恢复时从 last_session 读取筛选状态 → 应用到 Step2
- 源文件哈希记录：project.json sources 中记录文件哈希，启动时对比检测变更

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/main_window.py` | 修改 | 启动恢复 + AutoSaveManager + Ctrl+S |
| `src/transbridge/ui/context.py` | 修改 | dirty 标记联动 |
| `src/transbridge/persistence/project.py` | 修改 | 源文件哈希记录 |

## 风险与注意事项

- **防抖与定时保存竞态**: 防抖和定时器可能同时触发。VariantStore.save() 本身是幂等的，dirty=False 后跳过即可
- **大集合保存耗时**: JSON 序列化 10 万条目约 0.5s，不阻塞 UI（在 QTimer 回调中执行，主线程但极短）。如需异步，可用 QThread
