# NapCat 对接指南

灵轩通过 **OneBot v11 反向 WebSocket** 与 NapCat 通信。灵轩仓库只包含 Python 业务代码，**不包含、不修改 NapCat 源码**。

## 架构

```
QQ NT 客户端  ←→  NapCat（官方 Release，本机黑盒运行）
                      ↓ 反向 WS
              ws://127.0.0.1:8080/onebot/v11/ws
                      ↓
              灵轩（NoneBot2 + onebot-adapter-onebot）
```

## 许可说明

- **灵轩**（`src/lingxuan/`）为独立 Python 项目，仅消费标准 OneBot v11 协议，不属于「基于 NapCat 代码开发」。
- **NapCat** 采用限制性许可（[Limited Redistribution License](https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE)）：
  - 未经授权不得修改、再分发 NapCat 源码
  - 禁止商业使用
  - 本仓库不包含 NapCat 代码，请自行从 [官方 Release](https://github.com/NapNeko/NapCatQQ/releases) 下载
- 请遵守当地法律法规，仅用于个人消息推送等非商业场景。

## 1. 安装 NapCat

1. 从 [NapCatQQ Releases](https://github.com/NapNeko/NapCatQQ/releases) 下载 Windows 版（如 `NapCat.Shell.zip`）
2. 解压到本机任意目录（建议 `qq-bot/napcat/`，已在 `.gitignore` 中忽略）
3. 确保本机已安装 **QQ NT 版**

## 2. 配置 OneBot 反向 WebSocket

登录 NapCat 后，在配置目录（`NapCat.Shell/config/`）找到 `onebot11_<QQ号>.json`，参考本仓库模板：

[`docs/onebot11.lingxuan.example.json`](onebot11.lingxuan.example.json)

关键项：

| 配置项 | 值 |
|--------|-----|
| 类型 | 反向 WebSocket 客户端（`websocketClients`） |
| 地址 | `ws://127.0.0.1:8080/onebot/v11/ws` |
| `reportSelfMessage` | `false`（避免机器人自己的消息干扰群观察） |

## 3. 关闭 WebUI（无头模式）

编辑 `webui.json`（或 `webui_<QQ号>.json`），参考模板：

[`docs/webui.lingxuan.example.json`](webui.lingxuan.example.json)

```json
{
  "disableWebUI": true
}
```

关闭后 NapCat 不监听 6099 管理页。登录方式：

- 控制台 ASCII 二维码 + `cache/qrcode.png`
- 或在 NapCat 环境变量中配置快速登录（见官方文档）

## 4. 配置灵轩

1. 复制 `.env.example` 为 `.env`，填入 API Key 等
2. 确认 `DRIVER=~fastapi`（默认监听 8080 端口）
3. 安装依赖：`pip install -e .`

## 5. 启动顺序

1. 先启动灵轩：`python -m lingxuan.bot`
2. 再启动 NapCat（运行 `napcat.bat` 或 `launcher.bat`），扫码登录
3. NapCat 登录成功后会自动连接 NoneBot

## 6. 验证连接

灵轩控制台应出现类似日志：

```
[INFO] nonebot | OneBot v11 | Bot <QQ号> connected
```

此时私聊或群内 @ 机器人即可触发回复。

## 常见问题

**收不到消息？**  
检查 NapCat 反向 WS 地址是否为 `ws://127.0.0.1:8080/onebot/v11/ws`，确认灵轩与 NapCat 都在运行。

**连接成功但不回复？**  
查看灵轩控制台日志，确认消息处理器是否触发；检查 `.env` 中 `OPENAI_API_KEY` 是否配置。

**NapCat 启动闪退？**  
确保使用 QQ NT 版；查看 `NapCat.Shell/logs/` 日志。

## 更多资料

- [NapCat 官方文档](https://napneko.github.io/)
- [NoneBot2 文档](https://nonebot.dev/)
