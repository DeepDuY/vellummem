# TODO

## 待办

### memory_query 返回分组信息
- [ ] `memory_query` 结果每个条目增加 `group_ids` 字段（当前无分组信息）
- [ ] 或加回 `memory_list_groups()` 工具（之前被误删为死代码）

### 分组阈值优化
- [ ] 当前 0.35 阈值下 1 组（全量），0.8 默认阈值下 0 组
- [ ] 考虑 `build_groups()` 的默认阈值是否需要调整

### 其他
- [ ] `CONTEXT_SEPARATORS` / `CHUNK_SIZE=8000` 硬编码常量（已标记忽略，待确认）
- [ ] 安装 `sentence-transformers`
