# S17：计数分流与可选双语位置判断

- 日期：2026-11-13
- Epic：terminology-localization-profiles；Story 17；FR5.18 / ADR-039。
- 归属：仅记录本对话实施的替换算法、可选模型定位及接线。此前的术语导入、比较、导出、配置等大量未提交修改不属于本次新增。

## 行为与边界

原文命中不再直接触发全条中文子串替换。程序比较原文边界命中数与中文独立旧名/已有目标区间，处理同词目标保护、目标长词归属和双语包含关系。计数一致且无未解决冲突时直接转换；计数不一致、同区间多词或重叠时保留整条并提示。人名不构成特殊类别，术语库无须完整；计数一致仍可能语义漏判。

工作台增加“AI 定位 / 取消定位”。显式启动后读取全部来源，只有规则未解决的条目调用已配置 AI；模型仅选择候选编号及理由，不生成译文。非法编号、重叠、未知、请求失败或输入超预算均保留正文。取消中断后续请求与传输，过期结果不发布；普通切换、保存、导出与比较不请求模型。

已校验结果仅存于会话，以完整请求值直接比较复用。预览、保存、导出共享冻结判断；切换目标时未匹配的旧判断不能借用。无新增依赖、数据库表或摘要，无真实数据迁移；本次实验未修改实际正文、方案关联或插件。未解决诊断仍会阻止独立版本保存。

## 文件与符号

以下生产路径均相对于 `src/transbridge/`：

- 新增 `application/terminology_profiles/occurrence_alignment.py`：候选、计数、请求、判断、完整返回校验与不可变 `FrozenAlignmentResolver`。
- 新增 `application/terminology_profiles/occurrence_matching.py`：原文次数、中文区间、完整目标归属和计数分流；保留与原文索引一致的匹配边界，不将底板回填 no-op 长词当作目标权威。
- 新增 `application/terminology_profiles/profile_alignment.py`：先收集疑点，再批量判断，校验后原子交付；复用既有确定判断，允许重试未知/失败项。
- 修改 `application/terminology_profiles/projection.py`：接入候选分流与判断，按原始坐标一次替换；有效显式绑定优先，已是目标的绑定仍检查坐标是否过期；保留快速原文门控，避免反复规范化长书籍。
- 修改 `application/terminology_profiles/entry_projection.py`、`transition_projection.py`：传递实际目标声明键，包括回到底板时的有效目标键；未配对作用域 shadow 不获得目标权威。
- 新增 `infra/terminology_occurrence_aligner.py`：结构化候选选择、完整序列化输入预算、有界并发、逐条失败隔离及取消后等待资源释放。
- 新增 `bootstrap/terminology_alignment.py`：冻结既有 AI 配置，复用客户端、推理策略、请求预算及 Token 计数；生命周期结束关闭客户端。
- 新增 `ui/workbench/terminology_alignment.py`：全来源输入冻结、延迟读取数据库、仅必要时初始化模型、后台调用与按钮状态；不复制替换算法。
- 修改 `ui/workbench/terminology_preview.py`：显式运行、进度、取消、generation 校验、冻结结果复用与导出捕获。
- 修改 `ui/workbench/terminology_profile_bar.py`、`terminology_profile_controller.py`：AI 按钮与冻结写出投影。
- 修改 `ui/workbench/step2.py`：仅公开 preview coordinator 属性；超限文件不新增业务职责。修改 `project_paging.py` 两个调用点区分翻页与正文变化，避免视口滚动取消全项目定位。
- 修改 `ui/coordinators/profile_variant_save.py`、`application/projects/profile_variant_commands.py`：保存复用捕获的 projector，不在保存事务中重新判断。
- 新增 `tests/application/terminology_profiles/test_occurrence_alignment.py`、`test_profile_alignment.py`：重复字、计数分流、已知/未知长词、同区间冲突、长目标、绑定、取消、非法返回与复用失效。
- 新增 `tests/infra/test_terminology_occurrence_aligner.py`、`tests/bootstrap/test_terminology_alignment_wiring.py`：假客户端协议、预算、顺序、取消、配置冻结和关闭。
- 新增 `tests/ui/workbench/test_terminology_alignment.py`：显式操作、无疑点不读配置、全来源延迟读取、主线程隔离、进度、取消和迟到结果。
- 修改 `tests/application/terminology_profiles/test_entry_projection.py`：计数一致重复、重叠译名经判断后单次应用、回到底板保护长目标。修改 `tests/application/projects/test_profile_variant_commands.py`：真实合成项目验证未解决时拒绝保存、冻结判断保存与预览一致。
- 修改 `tests/ui/workbench/test_profile_variant_save.py`：保存确实传递捕获的 projector。同步 FR5.18、ADR-039、S17 及直接索引，保留历史记录。

## 验证

最终联合回归 **247 项通过**（3 条既有 SWIG 弃用告警）：

```powershell
$env:TEMP = Join-Path (Get-Location) '.tmp-term-alignment-root-qa'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
.venv/Scripts/python.exe -m pytest -p no:cacheprovider --basetemp .tmp-term-alignment-root-qa/pytest-final-fixed tests/application/terminology_profiles tests/application/projects/test_profile_variant_commands.py tests/infra/test_terminology_occurrence_aligner.py tests/bootstrap/test_terminology_alignment_wiring.py tests/ui/workbench/test_terminology_alignment.py tests/ui/workbench/test_terminology_preview_async.py tests/ui/workbench/test_terminology_profile_switching.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/workbench/test_profile_variant_save.py tests/ui/operations/test_write_schemes.py tests/ui/operations/test_scheme_comparison.py tests/contracts/io/test_terminology_profile_write.py -q
.venv/Scripts/python.exe -m ruff check src tests
.venv/Scripts/python.exe -m ruff format --check src tests
git diff --check
```

`uv run pytest ...` 在解释器查询阶段因沙箱 uv 缓存写入被拒绝，改用同一仓库 `.venv`。系统临时目录也受限，故使用任务临时目录；数据库保存夹具的 `os.link` 仍被沙箱拒绝，最终合成项目联合回归经自动审批后在沙箱外运行。首次联合收集发现两个新增测试文件重名，bootstrap 文件已改为 `test_terminology_alignment_wiring.py` 后全绿。测试未接触真实项目。

真实“超越天际”20,075 条保存正文按底板→重光ank r2 **只读验证**：7,555 条原文命中变化映射，7,470 条有旧译名候选；7,304 条本地修改，82 条候选被规则保留，84 条需模型（候选条目的 1.12%，其中 56 条计数不一致）。未新增红卫人人、诺德人人、布莱顿人人或召唤系系；精确 Nord/Renod 对话保持正确，Cyrodilic Hood 正常更新，Conjuration Staff 的二次压力检查不加回“系”。

脚本总耗时 44.60 秒包含读取及额外统计，不是 UI 性能基准；数据库和所有 WAL/SHM/journal 存在性、大小、mtime 前后不变。运行中仅删除无调用 `overlaps` 函数，排除此函数后源码 AST 一致；较慢的早期版本运行已中止并单独标记不完整。报告、原始统计与源码快照保留在本对话外部可视化目录 `term-alignment-validation/`，未将真实翻译正文加入仓库。

UI 已做离屏 850px 布局检查，无控件重叠；不是原生交互验收。未调用真实模型、未运行全仓 pytest、未打包。临时 QA 目录于收尾清理。

## 遗留与兼容

无已知实现阻塞。计数一致不等于语义正确，未知长词和偶然同形仍可能漏判；模型合法返回也不等于语义正确。模型精度与真实服务兼容性尚未实测，过长输入会提示提高现有批次 Token 上限，不截断上下文。历史上“英文一次、中文多次即全部替换”的行为改为需判断；这可能增加保存前需处理的诊断。会话判断不跨应用重启保存。
