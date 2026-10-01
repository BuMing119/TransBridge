# 补齐来源更新与序号同步需求

- 日期：2026-10-01
- Epic：source-file-update；关联 paratranz-context-order
- Story：01～03 需求追溯补齐
- 原因：用户指出 bm-pilot 已完成实现但遗漏正式需求文件；本次据既有授权与实际行为补录，不扩大代码范围。

## 文档变化

- `docs/requirements.md`：新增 FR19.14.1～FR19.14.8，明确通用源文件更新入口、差异预览、全部版本迁移、旧原文不可核实、备份/故障恢复、过期与重复确认、顺序衔接和历史边界；附可验证验收场景。
- 同文件新增 FR22.9～FR22.11：八位序号、完整集合拆分、严格回读、本地版本边界及主动同步确认后的仅 context 更新；记录真实远端/网页尚未验收。
- 原位修正 FR8.3/FR8.4/FR8.9：正式版本保存业务状态，原文和源顺序从来源解析；来源变化不得初始化空白数据覆盖已保存译文；共享来源基线的版本统一迁移，旧快照仍需兼容校验。
- 同文件需求变更历史加入本次记录。
- `plans/source-file-update/plan.md`、`plans/paratranz-context-order/plan.md`、`docs/adr/044-source-file-update.md`：加入权威需求编号及锚点引用。
- `docs/INDEX.md`、`docs/changelogs/INDEX.md`：最小更新本次需求追溯入口；既有实现增量不改写。

## 验证与边界

核对需求与当前 plan、实现及此前测试记录的一致性；检查新增编号唯一、显式锚点存在、关联文件可解析，运行 `git diff --check`。本轮只修改 Markdown，没有修改代码，未重跑 pytest/Ruff，也未重做真实远端或用户桌面验收。既有实现测试证据仍见 [实现记录](2026-10-01-001-source-file-update.md)。
