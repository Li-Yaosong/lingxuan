# P1-06 · core/observation_state.py — 观察运行时状态对象

## 目标
把 MVP `group_observer.py` 的 7 个模块级全局 dict 收敛为一个有明确生命周期的**状态容器类**（实例化后注入 ObservationService），消除模块级可变全局。

## 前置依赖
- P0-02（ObservationEntry 等）、P0-06（Clock）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/observation_state.py`

## 详细规格
定义每群状态 + 全局容器：

```python
@dataclass
class GroupObserveState:            # 对齐 MVP GroupObserveState
    last_reply_at: float = 0.0
    cooldown_until: float = 0.0
    last_judge_result: str = ""
    last_reply_user_id: int = 0
    observe_in_flight: bool = False
    pending_observe: bool = False

class ObservationStore:
    """取代 MVP 的 _buffers/_debounce_tasks/_observe_callbacks/_last_observe_len/_group_states/_group_locks/_user_nicknames。"""
    def __init__(self, config, clock): ...
    # 缓冲
    def append_entry(self, group_id: int, entry: ObservationEntry) -> None: ...   # 非 bot；保留最近 GROUP_OBSERVE_WINDOW 条
    def append_bot_message(self, group_id: int, text: str) -> None: ...
    def buffer(self, group_id: int) -> list[ObservationEntry]: ...
    def recent(self, group_id: int, limit: int = 5) -> list[ObservationEntry]: ...
    def buffer_len(self, group_id: int) -> int: ...
    # 观察进度
    def mark_observed(self, group_id: int) -> None: ...          # 记录当前 buffer 长度
    def has_new_since_observe(self, group_id: int) -> bool: ...
    # 状态
    def state(self, group_id: int) -> GroupObserveState: ...
    def lock(self, group_id: int) -> asyncio.Lock: ...
    # 昵称缓存
    def remember_nickname(self, group_id: int, user_id: int, nickname: str) -> None: ...
    def nickname_for(self, group_id: int, user_id: int) -> str: ...
    # 防抖任务与回调句柄（供 ObservationService 管理）
    def set_debounce_task(self, group_id: int, task) -> None: ...
    def get_debounce_task(self, group_id: int): ...
```

要求：
- 用实例字段（dict）替代全局；`config`/`clock` 注入。
- `append_entry` 的裁剪窗口读 `GROUP_OBSERVE_WINDOW`。
- 时间相关一律走注入的 `clock`（便于测试），不直接 `time.time()`。
- 提供 `reset()`/`reset_group(gid)` 便于测试与将来生命周期管理。

## 验收标准
- 无模块级可变全局；所有状态在实例上。
- 缓冲裁剪、mark/has_new_since_observe 行为与 MVP 一致。

## 测试要求
`tests/core/test_observation_state.py`：
- append 超过窗口后只保留最近 N 条。
- mark_observed 后无新消息 `has_new_since_observe==False`；再 append 后为 True。
- nickname 记忆与读取。

## 约束
Core 层，可用 `asyncio.Lock`；不 import nonebot；时间走 clock。
