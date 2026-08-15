# 词典系统重构 — 全量问题与优化清单

> 生成日期：2026-08-14
> 来源：技术议会（5 专家 × 2 轮辩论）对当前 `translation_memory/` 词典系统的事实核查结论。
> 目的：作为「词典粒度重构」的正式起点，列出全部隐患与优化点，供立项/排期/实现逐条清点。

---

## 一、背景与目标

用户设想：**「一个词典文件 = 一个模组文件（.esp/.esl/.esm，未来 .txt）的词条集合」**，词典是一个可分享拷贝的文件，scope（global/project）是标记该词典词条「可在什么范围使用」的**属性标签**，而非词条的切割维度。一个项目可关联多个词典；翻译新 mod 时可组合多个词典做多对多查询。

当前实现与设想的核心差距：词典词条**混装多 mod 来源**，数据结构里没有「词典 ↔ 来源 mod 文件」的一一对应，也没有文件级 model 边界。

---

## 二、隐患清单（🔴 会破坏正确性 / 需优先解决）

### 🔴 ISSUE-1：词条未记录「来源 mod」，历史数据无法按 mod 拆分

- **事实**：
  - `DictionaryEntry.source` 字段在 `save_from_collection` 中被硬编码为 `"collection"`（`manager.py`），从未填入 mod 文件名。
  - `TranslationEntry.id` = `editor_id:form_id|index~context`，**不含插件名**。
  - 唯一含插件名的字段 `TranslationEntry.form_id_with_plugin`（如 `0001A2B3|MyMod.esp`）在入典时被丢弃。
- **后果**：
  - 无法「按 mod 过滤/导出/分享」词典；
  - 历史 `global__skyrim_se.json` 混装多 mod，改粒度后**无法无损自动拆分**（归属信息已丢失）；
  - 「一个词条属于哪个 mod」在数据层不可判定。
- **根源**：`save_from_collection` 只传 `e.id`，未从 `form_id_with_plugin` 拆出 plugin 名写入 `source`/新字段。
- **优先级**：P0（一切后续能力的地基）。

### 🔴 ISSUE-2：多词典命中冲突无仲裁，`conflicts` 字段空转

- **事实**：
  - `QueryResult.conflicts` / `ApplyResult.conflicts` 字段定义了但**全文零赋值**；
  - 优先级常量 `SCOPE_RANK` 定义后**全库零使用**（grep 仅命中定义行）。
- **后果**：一项目挂多词典后，两词典对同一原文给出不同译文时，系统**静默选第一个**，用户无感知；共享词典协作场景尤其危险。
- **优先级**：P1（多词典能力落地时前置）。

### 🔴 ISSUE-3：`query()` 是「顺序短路」而非「并集仲裁」

- **事实**：`query()` 中键命中即 `return`、文本命中即 `return`（`manager.py`），与 ADR-014 §3.1 写的「文本命中收集候选后统一仲裁」不符。
- **后果**：多词典场景下永远只认「第一本命中的词典」，漏掉其他词典更优的匹配；「多对多组合查询」无法实现。
- **优先级**：P1。

### 🔴 ISSUE-4：`load()` 覆盖式装配，同名词典静默覆盖 + 数据丢失风险

- **事实**：`load()` 用 `self._dicts[(d.scope, d.scope_id)] = d` 覆盖内存字典，依赖文件名唯一性；损坏文件走 `.corrupt-{ts}` 改名。
- **后果**：改粒度为「一项目多 mod 词典」后，两个 mod 词典可能算出同一定位键，后者静默吞掉前者（磁盘-内存两次覆盖）。
- **优先级**：P2（数据安全，重构时一并解决）。

### 🔴 ISSUE-5：entry 主键 `_stable_id` 掺 scope，跨词典迁移「换 ID」

- **事实**：`_stable_id = sha1(scope|scope_id|seed)`，把 scope 编进词条主键。
- **后果**：同一句译文从 project 词典搬到 global 词典会生成全新 ID，去重/合并/追踪全失效；同名 mod 不同版本同原文被静默合并、无冲突提示。
- **优先级**：P1。

---

## 三、优化清单（🟡 设计有余地 / 与目标直接冲突）

### 🟡 ISSUE-6：定位键 `(scope, scope_id)` 语义超载，一项目只能一本词典

- **事实**：`scope_id` 在 global 是「游戏标签」(`skyrim_se`)、在 project 是「项目名」，既非 mod 名也非词典唯一 ID；`_key()` 强制 project 非空，使第二本 project 词典复用同键被覆盖。
- **后果**：与「一个项目关联多个词典」直接矛盾。
- **优先级**：P1。

### 🟡 ISSUE-7：文件命名「每 scope 一本」，无法承载多词典

- **事实**：`_file_for()` 生成 `global.json` 或 `{scope}__{scope_id}.json`，每 scope 至多一个文件。
- **后果**：无法表达「同 mod 多 scope 副本」「同名词典」「分享拷贝的唯一命名」。
- **优先级**：P1。

### 🟡 ISSUE-8：`Schema_version` 硬编码为 1，无版本分派

- **事实**：`to_dict()` 写死 `schema_version: 1`，`from_dict()` 无版本分支。
- **后果**：加字段不改版本会导致旧文件新读/新文件旧读静默错读；无法区分「迁移可拆/不可拆」。
- **优先级**：P1（迁移前提）。

### 🟡 ISSUE-9：mod 来源溯源需分层（plugin vs txt/通用）

- **事实**：`form_id_with_plugin` 仅对 ESP/ESM/ESL 解析路径存在；txt/SST/XT 等非 plugin 来源无 FormID 语义。
- **后果**：笼统的「mod_file_id」对非 plugin 文件本质只是文件名；`TranslationEntry.id` 的 FormID 已剥离 plugin 名（`split("|")[0]`），不能在 `id` 里补 mod。
- **正确做法**：入典时从 `form_id_with_plugin` 拆出 plugin 名（plugin 来源）；txt/通用来源取文件 basename。
- **优先级**：P0（与 ISSUE-1 同源，是 ISSUE-1 的实现细节）。

### 🟡 ISSUE-10：多词典查询交互面临「手动勾选」体验倒退

- **事实**：当前 `_on_apply_dict` 凭项目名/游戏标签自动推导上下文，零操作；改为多词典后若要求每次手动勾选，是体验倒退。
- **优化**：引入「激活集/组合方案」默认值——按 mod（及其 master/依赖）预生成默认词典子集，仅非默认场景才让用户改。
- **优先级**：P2。

### 🟡 ISSUE-11：`merge()`、`add()` 幂等/冲突语义在多词典下未定义

- **事实**：`merge()` 按 `(scope, scope_id)` 键对齐、`setdefault` 平铺；`add()` 非 overwrite 时「保留旧译文 + 合并 tags」。
- **后果**：主键去 scope 后，两个文件同 `(mod, seed)` 词条合并时谁 win、imported_at 取谁、tags 如何并，无显式语义。
- **优先级**：P2。

### 🟡 ISSUE-12：`hits` 命中计数在新模型下语义失真

- **事实**：`hits` 落在索引值上（`key_index[ck]={"entry_id","hits"}`），是「该索引被查询命中的次数」。
- **后果**：多对多组合查询后，同一 entry 被多词典命中，hits 归属与去重统计口径需重定义；分享词典时 hits 是运行态噪音。
- **优先级**：P3（低，随多对多一起处理）。

### 🟡 ISSUE-13：`_normalize_cache` 模块级全局单例，无锁保护

- **事实**：`_normalize_cache` 是 `manager.py` 模块级全局 dict，跨词典共享、无锁。
- **后果**：重构为「一文件一词典、多实例并行」后，缓存失效语义与线程安全会暴雷。
- **优先级**：P3（并发场景才触发）。

---

## 四、议会建议的落地顺序（供参考，非强制）

1. **新立 ADR**：记录「词典粒度 + 双轨文件组织 + v2 迁移」决策，明确推翻 ADR-014 决策 3.1 的「每 scope 仅一本词典 / 同级冲突不存在」两条。
2. **阶段 A（身份补齐 + 迁移）**：
   - ISSUE-1 + ISSUE-9：入典记录 `source_mod`；
   - ISSUE-8：`schema_version` 升 2 + `from_dict` 版本分派；
   - 旧数据迁移：能拆则按 `form_id_with_plugin` 拆，不能拆则整本归 `legacy_global` 兜底不丢数据；
   - ISSUE-5：主键盐改 `(mod_file_id, seed)`，scope 出盐；引入稳定 `dictionary_id` + manifest 清单层。
3. **阶段 B（多对多查询 + 仲裁）**：
   - ISSUE-2/3：`query()` 改「跨激活集收集候选 → 统一仲裁」，回填 `conflicts`；
   - ISSUE-6/7：定位键/文件命名升级；
   - ISSUE-10：激活集默认推导；
   - 键索引升级为 `(mod, complete_key)` 复合键。

---

## 五、开发工作量估算（议会估值，供排期参考）

- 开发专家修正估算：**5–8 人日**（原 3–5 偏乐观），触及 `manager.py` 定位键/主键盐/query/load 骨架 + GUI scope 下拉全链路。
- 代码影响面：约 500–900 行，跨 7–9 源文件 + 2 测试文件 + 1 迁移脚本。
- 阶段拆分：阶段 A 约 3–4 人日，阶段 B 约 2–4 人日（仲裁+激活集需新立 ADR）。

---

## 六、验收红线（质量专家提出，须纳入验收）

1. **迁移无丢失**：迁移前后 `query()` 全量 golden 比对（非单条断言）。
2. **组合查询 conflicts 非空**：含译文 + 来源 + 胜者 + hits 独立统计。
3. **主键跨词典稳定**：同一词条跨词典迁移不改 ID。
4. **UI 多词典关联不丢词典**：挂载/解绑不产生孤儿文件或悬空引用。
5. **新立 ADR**：明确废除 ADR-014 决策 3.1 的「每 scope 仅一本词典 / 同级冲突不存在」。
