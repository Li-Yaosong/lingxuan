# P5-04 · plugins/builtin/group_entities.py — 群实体学习改内置插件

## 目标
把 MVP `group_entities.py` 的群实体学习改造为订阅 `on_inbound_message` 的内置插件，通过 `PluginServices` 走 Repository 写入，可在管理端一键启停。

## 前置依赖
- P5-01/02/03（Host/Loader/services + Hook 接入）、P2-05/06（user_profile/social_graph Repository）、P2-09（UserMemoryService）。

## 需创建或修改的文件
- 新增 `src/lingxuan/plugins/builtin/group_entities.py`
- 从 DialogueService/ObservationService 中**移除**原先直接调用 `learn_entities_from_entry` 的代码（改由插件在 on_inbound 完成）。

## 详细规格
插件对象 `name="group_entities"`, `version="1.0"`：
- `setup(host, config, services)`：保存 `services`（user_memory、session repo、social graph）；`host.subscribe(HookType.on_inbound_message, self.on_inbound)`。
- `async def on_inbound(ctx) -> ctx`：
  - 仅处理群消息（`ctx.inbound.session_id.kind=="group"`）。
  - 迁移 MVP `learn_entities_from_entry` 逻辑：
    - 同步发言者昵称到图（`sync_entity_to_graph`）。
    - 对 at_user_ids：含「小堞宝」等 → merge_entity + index_name；正则匹配介绍 → merge_entity。
    - 无 @ 但文本含「就是」自称介绍 → merge_entity。
    - `apply_rule_extraction`（规则 + 社会边 + 认知整合调度）。
  - **所有写入经 services（Repository/UserMemoryService），同一逻辑事务**，不直接碰 JSON / session.meta。
  - 返回 ctx（通常不改 inbound）。
- 配置段（可选）：如启用的关键词列表，放插件 config（PluginConfigRepository）。

## 验收标准
- 群消息触发实体学习，结果落 DB（session_entities + name_index + social_edges + user_profiles）。
- 管理端 disable 该插件后，不再学习实体（主对话不受影响）。
- 行为与 MVP `group_entities` 一致。

## 测试要求
`tests/plugins/test_group_entities_plugin.py`（fakes/InMemory repos）：
- 构造含介绍/自称/@ 的群 inbound，dispatch on_inbound 后断言实体/边/name_index 写入。
- disable 后不写入。

## 约束
插件经 services 写库；不直接文件 IO；启停可控。
