# P0-08 · settings_defaults.py — 配置单一事实源

## 目标
把「所有配置项」的元数据集中到一个文件：key、类型、默认值、分组、是否敏感、是否可热更新。这是 ConfigProvider 与管理端配置页的共同数据源。

## 前置依赖
- P0-01。

## 需创建或修改的文件
- 新增 `src/lingxuan/settings_defaults.py`

## 详细规格
仅标准库。定义一个配置项描述结构与一张表：

```python
@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: Literal["str", "int", "float", "bool", "int_list"]
    default: object
    group: str                 # api / bot / observe / chunk / feature / user_memory / storage / admin / security
    is_secret: bool = False
    hot_reloadable: bool = True
    description: str = ""

SETTINGS: list[SettingSpec] = [ ... ]
```

必须完整收录 `00-common-context.md` 第 4.4 节的**全部 33 个现有项 + 全部新增项**。要点：
- 类型与默认值严格对齐（如 `MEMORY_WINDOW` int 20，`GROUP_OBSERVE_DELAY` float 1.5，`ENABLE_*` bool true，`BOT_ADMINS` int_list 空）。
- `is_secret=True`：`OPENAI_API_KEY`、`SECRET_KEY`。
- `hot_reloadable=False`：`DRIVER`、`DB_URL`、`DATA_ROOT`、`ADMIN_HOST`、`ADMIN_PORT`、`SECRET_KEY`、`AUTO_MIGRATE`（这些需重启生效）。其余默认 `True`。
- 分组建议：api（DRIVER/OPENAI_*）、bot（BOT_*/MEMORY_WINDOW）、observe（GROUP_OBSERVE_*/GROUP_FOLLOWUP/GROUP_CHAT_*）、chunk（ENABLE_STREAM_CHUNK/GROUP_MSG_CHUNK_*/GROUP_CHUNK_DELAY_*）、feature（ENABLE_*）、user_memory（USER_*）、storage（DB_URL/DATA_ROOT/AUTO_MIGRATE）、admin（ADMIN_HOST/ADMIN_PORT）、security（SECRET_KEY/JWT_*）。

新增项默认值：`DB_URL="sqlite+aiosqlite:///data/lingxuan.db"`、`DATA_ROOT="./data"`、`AUTO_MIGRATE=true`、`ADMIN_HOST="127.0.0.1"`、`ADMIN_PORT=8081`、`SECRET_KEY=""`（空表示未配置，管理端需要时报错）、`JWT_ACCESS_TTL=900`、`JWT_REFRESH_TTL=604800`。

同时提供辅助：
```python
SETTINGS_BY_KEY: dict[str, SettingSpec]      # 由 SETTINGS 构建
def parse_value(spec: SettingSpec, raw: str) -> object   # 按 type 解析字符串（复用 MVP 的 _env_bool / _parse_admins 语义）
```
- `_env_bool` 语义：`"1"/"true"/"yes"/"on"`（大小写不敏感）为 True。
- `int_list` 语义：逗号分隔，忽略空白与非数字。

## 验收标准
- `len(SETTINGS)` ≥ 41（33 现有 + 8 新增）。
- 每个 `.env` 现有变量都能在 `SETTINGS_BY_KEY` 找到，默认值一致。
- `parse_value` 对 bool/int/float/int_list 正确。

## 测试要求
`tests/test_settings_defaults.py`：
- 断言 33 个现有 key 都存在且默认值正确（可用参数化）。
- `parse_value` 各类型用例（含 `"on"`→True、`"1,2, x,3"`→[1,2,3]）。

## 约束
纯数据 + 纯解析函数；不读 env、不读 DB（那是 ConfigProvider 的职责）。
