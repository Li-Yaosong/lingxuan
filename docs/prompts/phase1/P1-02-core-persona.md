# P1-02 · core/persona.py — PersonaService

## 目标
把 MVP `persona.py` 迁为 Core 的 `PersonaService`，去掉对 `config` 模块级常量的直接依赖，改为经 `ConfigProvider` 读取。

## 前置依赖
- P0-05（ConfigProvider）、P1-01（实现，测试时可用 fake）。

## 需创建或修改的文件
- 新增 `src/lingxuan/core/persona.py`

## 详细规格
类 `PersonaService`：
- 构造注入 `config: ConfigProvider`。
- `get_system_prompt(is_group: bool = False) -> str`：
  - 读取 `BOT_NAME`、`BOT_PERSONA`。
  - `persona = BOT_PERSONA if BOT_PERSONA else DEFAULT_PERSONA`。
  - 群聊追加 `GROUP_PERSONA_SUFFIX`。
  - 行为与 MVP `persona.get_system_prompt` 完全一致。
- `DEFAULT_PERSONA` / `GROUP_PERSONA_SUFFIX`：从 MVP `persona.py` 原样搬运文案；其中 `{BOT_NAME}` 改为在 `get_system_prompt` 内用当前配置值格式化（不要在模块加载时固化 BOT_NAME）。

## 验收标准
- `BOT_PERSONA` 空 → 返回含默认人设；非空 → 用自定义人设。
- 群聊时返回值包含群聊 suffix；私聊不含。
- BOT_NAME 变更后再次调用能反映新名字（因为运行时读 config）。

## 测试要求
`tests/core/test_persona.py`：用 `FakeConfigProvider` 覆盖 BOT_NAME/BOT_PERSONA，断言四种组合。

## 约束
Core 层，禁止 import nonebot / 旧 `lingxuan.config` 常量。
