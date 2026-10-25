# 同步兼容新旧插件后缀路径

- 日期：2026-10-25
- Epic：paratranz-categorized-upload；Story：S20
- 需求：FR3.4.4、FR3.4.6；ADR-047
- [计划](../../../../plans/paratranz-categorized-upload/plan.md)

## 问题与结果

用户截图中云端存在 `ccafdsse001-dwesanctuary.json`，当前工程登记的是带 `.esm` 的插件。
实现此前只匹配当前 `插件.esm.json` 根名及分类目录，因此确有旧文件却提示没有当前路径。
用户明确要求两种命名都兼容、优先带插件后缀的新版本，并将入口改为“从paratranz上同步”。

现在根文件、当前精确分类和数字分片都支持 `.esp/.esm/.esl` 与省略插件后缀两种布局。
优先级按整个来源选择：任意新布局文件存在时只采用新布局，否则才采用旧布局；不混合两代内容。
新文件为空、读取失败或读中消失都不以旧译文掩盖。404 只刷新同代路径/分片，原地明确重试时重新选择。
大小写精确匹配优先，只有唯一候选时兼容大小写差异；不猜测两个不同插件或多个大小写变体的归属。
保持一次点击自动保存、还原点、原地重试、真实冲突复核、取消及未选来源保护。

## 真实文件变化

- 新增 `src/transbridge/paratranz/download_paths.py`：唯一共享的路径选择核心。
  `legacy_plugin_paths` 只移除根文件/第一层目录的插件后缀，保留当前相对分类及数字分片白名单。
  `unambiguous_legacy_paths` 用全部启用来源判定旧名，包含同 stem、其他当前根占用和数字分片撞名。
  `select_download_files` 先选代际，再处理实际大小写，返回真实文件路径/ID；不扩大到任意目录或旧 Quest/冲突文件。
  原纯路径匹配及校验从 `path_snapshot.py` 移入核心，旧位置保留导出，避免重复规则和外部导入破坏。
- `src/transbridge/paratranz/path_snapshot.py`：单来源适配器可选 legacy/blocked 路径，默认支持单来源后缀兼容，
  先选完整布局再读文件，不因某个新文件请求失败切到旧版；保持完整响应校验及请求次数边界。
- `src/transbridge/paratranz/batch_download_snapshot.py`：Scope 末尾追加兼容字段，旧三参数构造仍有效；
  批量读取过滤歧义旧名，整来源固定本次命名代际，404 仅同代重定位/重建分片，成功前缀不被当作完整成功。
  所有权检查保持来源隔离，避免逐文件重复扫描无关来源的整份 inventory。
- `src/transbridge/ui/operations/batch_download_session.py`：使用全部来源命名先计算兼容根，
  未选/已缓存来源同样参与旧名消歧；复核、重算和提交使用同一 Scope，保持快照复用和路径变化保护。
  缺文件提示列出查找的新旧候选根名，成功详情保留实际采用路径。
- `src/transbridge/ui/operations/categorized_download_scope.py`：旧通用计划/单来源入口使用同一核心，
  全部启用 authoritative 来源参与消歧；优先 namespace 定位登记来源，保留显式自定义完整根路径合同，
  缺 namespace 元数据时仅使用已知 canonical 根；未知归属保留独立显式范围，不猜另一登记来源。
  不加载未选插件，不改变普通上传路径。
- `src/transbridge/ui/workbench/cards/download_card.py`、`ui/shell/action_catalog.py`、`workbench/step3.py`：
  卡片标题、入口按钮及菜单精确为“从paratranz上同步”，保留“下载更新”等搜索别名；
  不改内窗“下载并保存”及自动流程。
- 新增 `tests/paratranz/test_download_paths.py`：18 项纯选择回归，覆盖后缀、整来源优先、
  分类/数字分片白名单、大小写、同 stem、当前根占用、数字分片跨来源歧义及优先新路径。
- `tests/paratranz/test_batch_download_snapshot.py`：补新文件为空仍优先、旧布局重定位遇到新布局出现时不混代，
  下次完整读取再优先新布局；原有取消、权限、读取次数及完整性回归保留。
- `tests/paratranz/test_current_path_snapshot.py`：扩至 33 项，覆盖旧根/旧分类、新布局根/分类/分片优先、
  大小写、未选/停用来源、旧名歧义、显式自定义根、缺少 namespace 元数据及未知显式范围。
- `tests/ui/operations/test_unified_download_service.py`：扩至 27 项，实际默认 facade → Qt/ApiWorker →
  真 Service/API/文件网关 → 磁盘，仅替换 HTTP 响应。
  验证旧根和大小写实际保存、新旧同存只请求新文件、新根/分类/分片失败不请求旧文件、未选同 stem 隔离、
  新文件 404 刷新后只剩旧文件时本次不落盘，明确重试才读取旧版并保存。
- `tests/ui/operations/test_batch_download_production.py`、`tests/ui/test_main_window_shell.py`：
  实际 Qt 卡片/菜单标题与旧别名断言更新，不以静态源码字符串代替真实入口验证。
- `docs/requirements.md`、`docs/adr/047-opt-in-categorized-project-upload.md`、
  `docs/dev/paratranz.md`、`docs/dev/paratranz-categorized-upload.md` 及当前计划：
  修正不兼容旧后缀/必须重传的过期要求，明确有限兼容、代际优先及新入口；只更新直接索引，不改历史增量。

## 复现与验证

真实默认 UI 旧根名用例先实跑两个大小写参数均失败：HTTP 未读取旧文件，本地仍未翻译；
兼容实现接入后同用例通过并真实保存。新文件错误的初版测试曾错误要求原本 clean 的版本变 dirty，
改为比较失败前后状态；生产保持本地未修改，没有为通过测试改变脏状态。
旧缺文件文案断言同步为列出新旧候选根名的实际诊断。

专项：共享核心/单来源/batch/真实默认链路 95 项通过；批量代际及核心 42 项通过；
单来源邻接 70 项通过；入口 UI/菜单/路由/主题 64 项通过。用例与最终整合重叠，不重复累加。

环境：现有 Windows、Python 3.12.12 和 uv 环境；未设置 FAISS_OPT_LEVEL。
TEMP/TMP 及 uv cache 仅使用有 owner 标记的本任务独占目录。

```powershell
uv --cache-dir .tmp-download-paths-20261025/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/application/projects tests/contracts/paratranz tests/persistence/v2 tests/paratranz tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_intent_router.py tests/ui/test_workbench_theme_migration.py -q --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-download-paths-20261025/final-integrated
uv --cache-dir .tmp-download-paths-20261025/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-download-paths-20261025/uv-cache run --no-sync ruff format --check src tests
```

最终整合 **1,655 passed、1 deselected**，152.55 秒，退出码 0，4 条既有弃用 warning。
全库 Ruff check 通过，格式检查 2,191 文件通过，退出码均为 0。
沿用已独立复现的旧上传排序断言排除；本地 HTTP server 沙箱连接受限的 `test_typed_client.py` 未重跑，
不相关 `test_terms_api_contract.py` 未运行。未运行全库 pytest、真实账号云端访问/写入、安装包构建或新性能基准。
截图用于确认文件名差异，不把截图等同于真实线上下载验收。

## 兼容与清理

旧文件只读兼容，无需移动、改名或重新上传；上传仍使用现有带后缀命名，不迁移云端记录/评论/历史。
不修改依赖、schema、文件内容编码、key/原文匹配和本地版本管理，保留用户其他工作区变更。
临时基线、测试输入和缓存确认所有权后清理，保留正式源码、测试和本记录。无已知未解决的本轮业务缺陷。
