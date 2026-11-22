# Dynamic String Distributor JSON

TransBridge 将已翻译的插件词条或 DSD 来源导出为 JSON 数组。每条记录的 `form_id` 包含十六进制 FormID 和定义该记录的插件名，`type` 标明字段，`string` 是替换文本。DSD 接受完整 FormID、插件内本地 ID 和带 `0x` 的写法。原生 DSD 来源重导出时保留原来的写法。[上游格式说明](https://github.com/SkyHorizon3/SSE-Dynamic-String-Distributor/blob/main/docs/modules/ROOT/pages/index.adoc)

## 字段定位

- 名称和描述等普通字段使用插件、FormID 和字段类型定位。游戏设置 `GMST DATA` 还需要 `editor_id`。
- 对话 `INFO NAM1` 使用回应编号，任务目标 `QUST NNAM` 使用 QOBJ 编号。消息框按钮 `MESG ITXT` 按列表顺序从 0 编号。
- 技能 `PERK EPF2` 使用 EPF3 的片段编号。`PERK EPFD` 的文本在对应列表中从 0 编号。
- 区域地图名称 `REGN RDMP` 可以省略索引。插件中有对应区域数据项时，TransBridge 按官方 xEdit 脚本写出该项从 0 开始的位置。
- 任务日志 `QUST CNAM` 的 `index` 等于 INDX 阶段编号加上日志在该阶段内从 0 开始的序号。例如阶段 10 中的前两条日志使用 10 和 11。旧版 DSD 可以用 `original` 匹配原文；上游自 1.4.0 起弃用这种写法。已有工程只保存了原文定位时仍可按旧格式导出。[官方 xEdit 导出脚本](https://github.com/SkyHorizon3/SSE-Dynamic-String-Distributor/blob/main/xEdit/DSD%20-%20Export%20plugins.pas) · [上游测试样例](https://github.com/SkyHorizon3/SSE-Dynamic-String-Distributor/blob/main/tests/generalTestAll.json)

## 条件译文

DSD 自 1.5.0 起支持在指定字段的译文上设置 `conditions`。例如同一条对话可以附上玩家负重条件。编辑已有 DSD JSON 时，导出保留每条规则的条件和顺序。原文件中显式的 `"string": ""` 表示用空字符串替换，也会随来源保留。

```json
[
  {"form_id": "0x800|Example.esp", "type": "QUST CNAM", "index": 10, "string": "我已抵达。"},
  {"form_id": "0x900|Example.esp", "type": "MESG ITXT", "index": 0, "string": "接受"},
  {"form_id": "0xA00|Example.esp", "type": "INFO NAM1", "index": 2, "conditions": ["Self IsOverEncumbered NONE NONE == 1"], "string": "你带得太多了。"}
]
```

已按新版导入的 DSD 来源会将可选字段和条件随工程保存。若旧工程导入时已经丢失条件，需从原始 DSD JSON 重新载入，才能还原完整规则。

