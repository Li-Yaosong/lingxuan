# P0-07 · core/models.py — 领域辅助与规则常量

## 目标
放置 Core 层跨 Service 复用的纯函数/值对象/规则常量（不含 IO、不含框架）。

## 前置依赖
- P0-02（领域类型）、P0-04（DTO）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/models.py`

## 详细规格
仅依赖标准库 + `protocols/`。收纳以下纯逻辑（从 MVP 提炼，保持行为一致）：

1. **关系阶段计算** `compute_stage(profile) -> str`：复现 MVP `_compute_stage`：
   - `close`：`interaction_count >= 30`
   - `familiar`：`seen_in_private and seen_in_group`，或 `interaction_count >= 10`
   - `acquaintance`：`interaction_count >= 3`，或存在非 identity 类别的 active fact
   - 否则 `stranger`
   - 阈值来自参数（默认 30/10/3），便于将来配置化。

2. **阶段中文标签** `stage_label(stage) -> str`（对齐 MVP `stage_label`）。

3. **显示名** `display_name(profile) -> str`：优先 `preferred_name`，否则第一个 alias，否则空。

4. **fact id 生成** `new_fact_id() -> str`：8 位 hex（`uuid4().hex[:8]`）。

5. **relation 枚举常量**：`RELATION_INTRODUCED_AS="introduced_as"` 等 4 个。

6. **fact 类别常量**：`identity/preference/skill/relation/general`。

> 若某规则的精确实现细节不确定，以 MVP `user_memory.py` 现有逻辑为准；本任务只是把这些纯函数搬到 Core 且去掉任何 IO/日志依赖。

## 验收标准
- 可 import；无框架/IO 依赖。
- 各纯函数有明确类型注解。

## 测试要求
`tests/core/test_models.py`：覆盖 `compute_stage` 的 4 个分支边界（interaction=2/3/10/30；有/无非 identity fact；私聊+群聊组合）。

## 约束
纯函数，无副作用、无 IO、无全局可变状态。
