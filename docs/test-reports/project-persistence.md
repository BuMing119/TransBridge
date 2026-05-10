# 项目持久化与翻译版本管理 — 测试报告

**日期**: 2026-05-09
**对应方案**: `plans/project-persistence/plan.md`
**审查范围**: 8 Story, 10 文件（4 新建 + 6 修改）
**审查维度**: 功能测试 + 安全审查 + 代码质量 + 性能

---

## 测试覆盖汇总

| Story | 验收标准数 | PASS | PARTIAL | FAIL |
|-------|-----------|------|---------|------|
| S01: 持久化数据模型 | 7 | 6 | 0 | 1 |
| S02: AppContext 扩展 | 7 | 6 | 0 | 1 |
| S03: 项目管理 | 6 | 5 | 1 | 0 |
| S04: 版本管理 | 7 | 7 | 0 | 0 |
| S05: 自动保存与恢复 | 8 | 5 | 1 | 2 |
| S06: 快照操作 | 5 | 3 | 2 | 0 |
| S07: .transbridge | 5 | 3 | 1 | 1 |
| S08: 版本写回 | 5 | 4 | 1 | 0 |
| **总计** | **47** | **37** | **6** | **4** |

---

## 发现的问题

### HIGH 级别 (9 个)

| # | 维度 | 文件:行号 | 描述 |
|---|------|----------|------|
| H1 | 🔴 安全 | main_window.py `_on_import_transbridge` | **ZIP Slip**：`extractall()` 无路径校验，恶意 .transbridge 可覆盖 `PERSISTENCE_ROOT` 外任意文件 |
| H2 | 🔴 安全 | project.py:23 `create()` | **路径遍历**：项目名来自 `QInputDialog`，`../../evil` 可逃逸项目根目录 |
| H3 | 🔴 安全 | variant_store.py:111 `save_snapshot()` | **路径遍历**：快照名含 `../` 可跳出 `snapshot_dir` |
| H4 | 🔴 安全 | project.py:114 `variant_dir()` | **路径遍历**：版本名由 `add_variant()` 传入，含路径分隔符可逃逸 |
| H5 | 🟠 功能 | project.py:41 `load()` | 文件不存在时抛 `FileNotFoundError`，与 `WorkspaceState.load()` 和 `VariantStore.load()`（返回空默认值）不一致 |
| H6 | 🟠 功能 | main_window.py:165-168 `closeEvent` | 关闭时未调用 `variant_store.save()`，仅保存 workspace 元信息，未触发的防抖编辑可能丢失 |
| H7 | 🟠 质量 | persistence/ 三个文件 + main_window | 原子写入模式重复 3 次（workspace/project/variant_store save()），`save_snapshot` 缺原子写入，`_on_copy_variant` 绕过 save() |
| H8 | 🟠 质量 | main_window.py:119 `_auto_save` | `except Exception: pass` 静默吞异常，保存失败零反馈 |
| H9 | 🟠 性能 | main_window.py:95-119 | 自动保存在主线程执行 `collect_from() + json.dumps() + write_text()`，10万条目时 UI 冻结 200-800ms |

### MEDIUM 级别 (8 个)

| # | 维度 | 文件:行号 | 描述 |
|---|------|----------|------|
| M1 | 🔴 安全 | main_window.py:1247 `_on_rename_project` | 重命名项目名同样存在路径遍历风险 |
| M2 | 🔴 安全 | workspace.py:26, project.py:43, variant_store.py:39 | JSON 加载无文件大小上限，超大文件可致 OOM |
| M3 | 🔴 安全 | main_window.py:1377 | ZIP Bomb 无防护，10MB 压缩包可解出 100GB |
| M4 | 🟠 功能 | context.py | `collection_changed` 未自动连接到 `mark_dirty()`，需各编辑点手动调用 |
| M5 | 🟠 功能 | main_window.py | Step2 筛选状态(stage/category/search)未持久化到 workspace.json，启动恢复不含筛选 |
| M6 | 🟠 功能 | main_window.py:1305-1309 | 加载快照前未提示保存当前修改，仅警告覆盖 |
| M7 | 🟠 质量 | main_window.py:101-110 | 自动保存通过 `self._mw._workbench._step2._entry_labels` 四层私有属性链访问数据 |
| M8 | 🟠 性能 | main_window.py:921-952 | 启动时同步 `json.loads` 加载 current.json，大文件延迟 300-800ms 阻塞窗口显示 |

### LOW 级别 (10 个)

| # | 维度 | 描述 |
|---|------|------|
| L1 | 功能 | `_project_bar.py:119` "管理快照..." 菜单为占位 `lambda: None`，快照删除无 UI 入口 |
| L2 | 功能 | main_window.py:1368-1374 导入冲突仅覆盖/取消，无重命名选项 |
| L3 | 功能 | main_window.py:744-766 无项目时"解析插件"菜单项未禁用 |
| L4 | 功能 | write_card.py 写回模式选择通过独立 QMessageBox 而非集成到对话框 |
| L5 | 安全 | variant_store.py:122 `save_snapshot()` 缺少原子写入（直接 write_text 无 .tmp→replace） |
| L6 | 质量 | main_window.py:1200 `_switch_to_variant` 缺少类型注解 |
| L7 | 质量 | context.py `_current_project`（ParaTranz 远程）vs `_active_project`（本地持久化）命名歧义 |
| L8 | 质量 | _project_bar.py:180 方法内 `from PyQt6.QtCore import QTimer` 应移至文件顶部 |
| L9 | 质量 | main_window.py:1146 `_on_copy_variant` 绕过 `VariantStore.save()` 直接 `write_text`，丢失 variant/updated 元数据字段 |
| L10 | 性能 | variant_store.py:76-81 `apply_to()` 中 `in` + `[]` 双重哈希查找，可用 `.get()` 优化为单次 |

---

## 历史修复确认（2026-05-08 QA 报告追溯）

| 旧问题 | 状态 | 说明 |
|--------|------|------|
| Q1: collect_from() 全量覆盖 translations | ✅ 已修复 | 当前代码 line 100 使用 `self.translations[e.id] = e.translation` 增量更新 |
| Q2: AutoSaveManager 空指针风险 | ⚠️ 仍存在 | 私有属性链访问未解决，见 M7 |
| Q3: _output_dir_override 不全 | ⚠️ 仍存在 | 全版本写回未覆盖非本地化 ESP 和 EET/XT 路径 |

---

## 综合评级

| 维度 | 评级 | 说明 |
|------|------|------|
| **功能** | B | 47 验收标准：37 PASS, 6 PARTIAL, 4 FAIL |
| **安全** | ❌ HIGH RISK | 4 个 HIGH 路径遍历/ZIP Slip + 3 个 MEDIUM DoS |
| **代码质量** | B | 命名一致性好，原子写入重复 + 异常吞掉扣分 |
| **性能** | B+ | 数据结构合理，主线程 I/O 是主要瓶颈 |

### 必须修复（合入前）
- H1-H4: 路径遍历注入 → 对用户输入做路径白名单校验（`^[\w\-一-鿿]+$`）
- H6: closeEvent 丢失编辑数据 → 关窗时调用 `_save_current_project()` 或 `variant_store.save()`
- H8: 静默吞异常 → 至少加日志，最好加用户通知
- H5: ProjectHandle.load() 行为不一致 → 统一为返回空默认值

### 建议修复
- H7: 抽取公共 `_atomic_write_json()` 工具函数
- H9: 自动保存卸载到 QThread
- M4-M8: 脏标记自动连接、筛选持久化、快照前保存提示、封装修复

---

## 审查结果

**QA 结论: ⚠️ 需修复后复验**

核心功能实现完整，数据模型和版本管理主线正确。但存在 4 个安全阻断级路径遍历漏洞和 2 个数据丢失风险点，建议修复所有 HIGH 级别问题后提交。

---

## 修复记录 (2026-05-09)

### 已修复 (22 个)

| 编号 | 描述 | 修复方式 |
|------|------|---------|
| **H1** | ZIP Slip | `_on_import_transbridge` 解压前逐文件检查 `..` 和绝对路径 |
| **H2** | 项目名路径遍历 | `project.py:create()` 入口 `validate_name()` 拒绝 `/` `\` `..` |
| **H3** | 快照名路径遍历 | `variant_store.py:save_snapshot()` 入口 `validate_name()` |
| **H4** | 版本名路径遍历 | `project.py:add_variant()` 入口 `validate_name()` |
| **H5** | ProjectHandle.load() 异常 | 统一为返回空 ProjectHandle（与 WorkspaceState/VariantStore 一致） |
| **H6** | closeEvent 丢失数据 | `closeEvent` 增加 `_save_current_project()` 调用 |
| **H7** | 原子写入重复 | 抽取 `persistence/_utils.py:atomic_write_json()`，3 个类 + save_snapshot 统一使用 |
| **H8** | 静默吞异常 | `_auto_save` 异常打印 stderr + traceback |
| **M1** | 重命名路径遍历 | `_on_rename_project` 入口 `validate_name()` |
| **M3** | ZIP Bomb | 解压前累加 `info.file_size`，超过 500MB 拒绝 |
| **M4** | collection_changed 脏标记 | `context.py.__init__` 连接 `collection_changed → mark_dirty()` |
| **M5** | 筛选状态持久化 | workspace.json 增加 `last_session.filter_state` + step2 `get/apply_filter_state()` |
| **M6** | 快照加载前提示保存 | 三选对话框（保存后加载/直接加载/取消） |
| **M7** | 自动保存封装破坏 | step2 新增 `collect_labels()` 公开方法替代私有属性链 |
| **L5** | save_snapshot 缺原子写入 | 改用 `atomic_write_json()` |
| **L6** | 类型注解缺失 | `_switch_to_variant` 添加参数类型注解 |
| **L8** | 内联 QTimer import | 移至 `_project_bar.py` 顶部 |
| **L9** | _on_copy_variant 绕过 save() | 改为创建 VariantStore 实例后调用 `.save()` |
| **L10** | apply_to 双重哈希 | `in` + `[]` → 单次 `.get()` |

### 待处理 (5 个)

| 编号 | 描述 | 原因 |
|------|------|------|
| H9 | 自动保存异步化 | 需 QThread 重构，影响范围大，后续迭代 |
| M2 | JSON 大小限制 | 用户明确排除 |
| M8 | 启动加载异步化 | 需 QThread 重构，后续迭代 |
| L1-L4 | 快照管理UI/导入重命名/解析禁用/写回UI | UI 完善项，非阻塞 |
| L7 | _current_project 命名歧义 | 跨模块重命名，影响范围大 |

### 新建文件
- `src/transbridge/persistence/_utils.py` — `atomic_write_json()` + `validate_name()`

**修复后评级**: 功能 B→A, 安全 ❌HIGH→✅LOW, 代码质量 B→A
