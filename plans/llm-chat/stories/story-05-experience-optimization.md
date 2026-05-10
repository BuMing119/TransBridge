# Story 05: 体验优化

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建), `src/transbridge/ui/` (修改)
**状态**: ✅ 已确认
**创建日期**: 2026-05-06

## 前置依赖

### 上游 Story
- Story-01: SmartAssistantPanel / MainWindow 集成
- Story-03: ChatWidget 循环控制

### 跨 Plan 依赖
- `ui-workbench/plan.md` → `MainWindow.closeEvent()` — 持久化窗口状态

## 验收标准

- [ ] 关闭主窗口后重新打开，面板位置/宽度/可见状态正确恢复
- [ ] 面板关闭时 ChatWorker 被正确终止（wait + deleteLater）
- [ ] LLM 配置缺失时提示用户先配置 API
- [ ] 工具执行失败时显示友好错误消息
- [ ] 网络错误时提供重试提示
- [ ] 计划执行支持中途取消，线程安全
- [ ] ContextBuilder 正确收集当前 AppContext 状态（集合概况/选中条目数）

## 数据流

```
应用启动
  │  QSettings("TransBridge", "MainWindow")
  ├─ restoreGeometry(settings.value("geometry"))
  └─ restoreState(settings.value("state"))     → DockWidget 状态恢复

应用关闭
  │  MainWindow.closeEvent()
  ├─ panel.isVisible()? → ChatWorker.cancel() + wait(3000)
  ├─ settings.setValue("geometry", saveGeometry())
  └─ settings.setValue("state", saveState())

LLM 调用前
  │  ContextBuilder.build(ctx)
  │   ├─ 集合统计（总数/已翻译/待翻译/分类分布）
  │   ├─ 选中条目数
  │   └─ 当前插件名
  ▼  → 插入 system prompt 作为上下文

错误处理
  ├─ LLMConfig 未配置 → "请先在设置中配置 LLM API" 系统消息
  ├─ 网络错误 → "网络请求失败，请检查网络后重试" + [重试] 按钮
  └─ 工具失败 → ToolCard 显示 ❌ + 错误详情
```

## 关键接口

### context_builder.py

```python
class ContextBuilder:
    """构建 system prompt 中的当前上下文信息"""

    @staticmethod
    def build(ctx: AppContext) -> str:
        """
        返回追加到 system prompt 的上下文文本:
        ---
        当前工作环境:
        - 插件: {esp_stem}
        - 集合概况: 总计 N 条, 已翻译 M 条, 待翻译 K 条
        - 分类: NPC_ X 条, INFO Y 条, BOOK Z 条, ...
        - 当前选中: N 条
        """
```

### main_window.py (扩展)

```python
class MainWindow:
    def closeEvent(self, event):
        # 清理 ChatWorker
        if self._assistant_panel:
            panel = self._assistant_panel
            if panel._chat._worker and panel._chat._worker.isRunning():
                panel._chat._worker.cancel()
                panel._chat._worker.wait(3000)

        # 持久化
        settings = QSettings("TransBridge", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("state", self.saveState())
        super().closeEvent(event)

    def _restore_state(self):
        settings = QSettings("TransBridge", "MainWindow")
        if settings.contains("geometry"):
            self.restoreGeometry(settings.value("geometry"))
        if settings.contains("state"):
            self.restoreState(settings.value("state"))
```

## 实现步骤

### 步骤 1: QSettings 状态持久化

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- `closeEvent()`: 保存 geometry + window state + DockWidget 状态
- `__init__()` 末尾: 调用 `_restore_state()` 恢复
- 如无已保存状态 → 使用默认布局（panel 隐藏）

**边界条件**:
- 首次启动无 QSettings → 使用默认值
- 多屏环境下 geometry 保存的坐标在当前屏幕外 → Qt 自动修正
- panel 关闭时保存 → 下次启动仍为关闭状态

**测试策略**:
- 手动验证：展开面板→调整宽度→关闭→重新打开→状态恢复
- 手动验证：浮动面板→关闭→重新打开→面板回到原位

### 步骤 2: 创建 ContextBuilder

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/context_builder.py`（新建）

**实现要点**:
- 读取 ctx.collection 统计信息
- 读取 ctx.esp_path stem 作为插件名
- 读取 Step2 当前筛选/选中状态（如有）
- 格式化为 Markdown 文本，追加到 system prompt 尾部

**边界条件**:
- ctx.collection 为空 → "当前未加载任何集合"
- ctx.esp_path 为 None → "未选择插件"
- 分类计数为零 → 仍列出全部分类

**测试策略**:
- 手动验证：加载 ESP → 打开助手 → system prompt 含集合统计
- 手动验证：空集合 → prompt 提示"未加载集合"

### 步骤 3: 错误处理完善

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- ChatWorker error 信号 → 红色系统消息
- LLMConfig 校验：ChatWorker.run() 调用前检查 `LLMConfig.load_from_file()` → 若无 api_key → 错误提示
- 网络错误识别：error message 含 "timeout"/"connection"/"refused" → 添加 [重试] 按钮
- PlanCard/ToolCard 执行中长时间无响应 → 超时 120s 自动提示

**边界条件**:
- 重试按钮点击 → 重新调用 `_run_llm_round()`，不重新创建 ChatWorker（清理旧 worker）
- 连续网络错误 3 次 → 建议检查网络配置，不再自动重试

**测试策略**:
- 手动验证：删除 API key → 发送消息 → 提示配置
- 手动验证：断网 → 发送消息 → 显示网络错误 + 重试按钮

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/context_builder.py` | 新建 | ContextBuilder |
| `src/transbridge/ui/main_window.py` | 修改 | closeEvent QSettings 持久化 + ChatWorker 清理 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | 错误处理：LLM 配置缺失/网络错误/重试 |

## 风险与注意事项

- **QSettings 键冲突**: "TransBridge"/"MainWindow" 可能与其他组件冲突 → 使用项目统一前缀
- **restoreState 版本兼容**: DockWidget 的 objectName 需要稳定（不能因重构改名导致恢复失败）→ panel 的 objectName 在 `__init__` 中显式设置
- **ChatWorker.wait(3000) 阻塞主线程**: 在 closeEvent 中调用 wait 会短暂冻结 UI → 可接受（关闭时的最终清理），或改为 `QTimer.singleShot(0, cleanup)` 异步清理后退出
- **ContextBuilder 信息过时**: 用户在助手对话期间修改了 collection → 下次 `_run_llm_round` 时重新构建上下文（每次 LLM 调用前动态构建，而非面板打开时一次性）
