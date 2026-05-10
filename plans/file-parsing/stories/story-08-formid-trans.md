# Story 08: FormID 转换工具

**所属方案**: `plans/file-parsing/plan.md`
**状态**: ✔️ 已实现

## 概述

FormID 格式转换和规范化工具。处理不同来源的 FormID 表示（十六进制字符串、整数、含插件名后缀等）。

## 关键设计

- **十六进制规范化**: 统一为 8 位大写十六进制字符串
- **插件名提取**: 从 `FormID|Plugin.esp` 格式中分离 FormID 和插件名
- **轻量级工具**: 纯函数形式，无类封装，供各解析器调用

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/parser/utils/fromid_trans.py` | FormID 转换工具函数 |
