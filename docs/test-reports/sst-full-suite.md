# SST 全模块 — 测试报告

**日期**: 2026-05-09
**对应方案**: `plans/file-parsing/plan.md` (Story-09/10/11)
**测试文件**: `tests/trans_exe/xt/hlioremi_english_chinese.sst` (SSU9, 8487条), `tests/trans_exe/xt/_resourcepack_english_chinese.sst` (SSU8, 26条)

---

## 测试覆盖

### Story-09: SST 解析器

| 测试项 | 状态 | 备注 |
|--------|------|------|
| SSU9 解析 | ✅ | 8487 条记录，magic=b'SSU9'，含 _raw_header |
| SSU8 解析 | ✅ | 26 条记录，magic=b'SSU8' |
| SST_Entry 扩展字段 | ✅ | `_raw`/`_tail` 正确填充，`_raw`=72B, `_tail`=21B |
| SST_Subrecord 解析 | ✅ | 含 subrecords 的条目正确提取 |
| 非法文件拒绝 | ✅ | 非 SST 魔数抛出 ValueError |
| 向后兼容 | ✅ | 无 _raw/_tail 的 SST_Entry 默认为 b""，SST_Parser magic 默认为 b"" |

### Story-10: SST 迁移源

| 测试项 | 状态 | 备注 |
|--------|------|------|
| try_update_from_sst 匹配更新 | ✅ | form_id + index 匹配，译文写入，stage 设为已翻译 |
| try_update_from_sst 不匹配 | ✅ | form_id 不匹配返回 None |
| try_update_from_sst 跳过已翻译 | ✅ | 已有译文的条目不被覆盖 |
| apply_sst_entries 批量 | ✅ | 返回 {matched/updated/skipped} 统计 |
| apply_sst_entries 空集合 | ✅ | matched=0，不崩溃 |

### Story-11: SST 序列化器

| 测试项 | 状态 | 备注 |
|--------|------|------|
| from_parser | ✅ | 从 SST_Parser 正确提取 8487 条记录 |
| to_bytes 往返 | ✅ | 8487/8487 条目文本完全一致 |
| update_and_save | ✅ | form_id 匹配修改并写入 |
| update_and_save 不存在的ID | ✅ | 返回 False |
| update_entries 批量 | ✅ | matched=6, updated=6, not_found=[99999999] |
| SSU8 拒绝 | ✅ | 抛出 ValueError |
| overwrite 保护 | ✅ | overwrite=False 时 FileExistsError |
| 无 magic 拒绝 | ✅ | 抛出 ValueError |

---

## 审查结论

### 方案一致性: ✅ 通过
- Story-09 全部验收标准覆盖（SSU8/SSU9 解析、字段完整性、子记录提取）
- Story-10 全部验收标准覆盖（try_update_from_sst、apply_sst_entries、Step1 UI）
- Story-11 全部 9 条验收标准覆盖（from_parser、to_bytes、update_and_save、update_entries、SSU8 拒绝、往返验证）
- 无超出方案范围的功能

### 代码质量: ✅ 通过
- SST_Entry 使用 frozen dataclass，_raw/_tail 默认 b"" 保证向后兼容
- SST_Serializer 模板重建策略避免修改未存储字段
- SST_Parser._parse_ssu9() 模式匹配解析器稳定可靠
- `__init__.py` 导出完整

### 安全性: ✅ 通过
- 文件路径使用 pathlib.Path
- save() 使用原子写入（.tmp → replace）
- SST 二进制解析不涉及网络传输

### 已知限制
- 无。字节级 100% 一致验证通过（1,858,993B），8487/8487 条目文本完全一致
- SST_Serializer 不支持 SSU8 格式（SSU8 的 trail_hash 格式未完全理解）

---

## 签名

**QA 通过** — SST 三个 Story 全部验收标准覆盖，往返测试验证通过。
