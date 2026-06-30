# 灵轩 - AI QQ 机器人

基于 NapCatQQ + NoneBot2 + OpenAI 兼容大模型 API 的 QQ AI 机器人。

## 快速开始

1. 复制 `.env.example` 为 `.env`，填入你的 API Key 和配置
2. 安装依赖：`pip install -e .`
3. 按照 [napcat/SETUP.md](napcat/SETUP.md) 配置 NapCatQQ
4. 启动机器人：`python -m lingxuan.bot`

## 功能

- 私聊对话：直接给机器人发消息即可
- 群聊对话：@机器人 或回复机器人消息触发
- 人设系统：灵轩拥有完整的性格和说话风格
- 对话记忆：记住每个用户/群的对话历史
- 管理员命令：`/灵轩 重置记忆`
