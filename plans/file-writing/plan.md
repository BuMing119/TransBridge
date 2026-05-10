# 文件写入

> **状态**: ✔️ 已实现
> **模块**: `src/transbridge/writer/`

## 概述

将 Collection 中的译文写回到目标文件格式：ESP/ESM 插件（inline + localised 模式）、EET XML（更新/新建）、XT XML（更新/新建）、纯 Strings 文件。

## Story 清单

| Story | 标题 | 状态 |
|-------|------|------|
| Story-01 | ESP/ESM 写入（inline 模式） | ✔️ |
| Story-02 | ESP/ESM 写入（localised 模式 + .strings 输出） | ✔️ |
| Story-03 | EET XML 更新写入 | ✔️ |
| Story-04 | EET XML 新建构建 | ✔️ |
| Story-05 | XT XML 更新写入 | ✔️ |
| Story-06 | XT XML 新建构建 | ✔️ |
| Story-07 | 纯本地化 Strings 输出（不修改 ESP） | ✔️ |

## 关键文件

- `src/transbridge/writer/plugin_writer.py` — ESP/ESM 写入器
- `src/transbridge/writer/eet_xml_writer.py` — EET XML 更新器
- `src/transbridge/writer/eet_xml_builder.py` — EET XML 构建器
- `src/transbridge/writer/xt_xml_writer.py` — XT XML 更新器
- `src/transbridge/writer/xt_xml_builder.py` — XT XML 构建器
