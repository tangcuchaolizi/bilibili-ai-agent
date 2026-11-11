# CLAUDE.md

本文档为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概述

B站 AI 智能体 — 一个本地优先的自动化机器人，使用 DeepSeek API（兼容 OpenAI 接口）生成并发布 B站 动态、回复评论和私信，所有内容由可配置的人设驱动。设计在笔记本上运行，内存占用 50–80 MB。

## 命令

```bash
# 首次安装
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt

# 运行
python bot.py                # 交互菜单（立即发布、启动定时器、或两者兼有）

# 调试工具
python debug_comment.py      # 通过 RID 测试评论获取
python get_rid.py            # 查看最新动态的评论 RID + comment_type

# Docker
docker compose up -d         # 容器运行（挂载 config.json + memory.json）
```

项目未配置测试、代码检查工具或格式化工具。

## 架构

### 模块依赖关系（调用方 → 被调用方）

```
bot.py → scheduler_engine.py, ai_generator.py, bilibili_client.py
scheduler_engine.py → ai_generator.py, bilibili_client.py, config_loader.py
ai_generator.py → config_loader.py
bilibili_client.py → config_loader.py
config_loader.py → config.json, memory.json
```

### 核心模块

**`bot.py`** — 入口文件。打印交互菜单（4个选项），然后选择发一条动态后退出，或启动 APScheduler 并无限循环。基于 asyncio 运行。

**`ai_generator.py`** — 封装 DeepSeek/OpenAI 客户端。根据 `persona` 配置（姓名、年龄、城市、性格、说话风格、话题、禁忌）构建系统提示词。生成 30–120 字的动态、10–60 字的评论回复、10–80 字的私信回复，温度设置为 0.9–0.95 以增加随机性。通过读取记忆中的近期动态来避免重复。

**`bilibili_client.py`** — 封装 `bilibili-api-python`，实现：发布文字动态（`BuildDynamic` + `send_dynamic`）、通过 `Dynamic.get_info()` 获取评论信息、读取评论（`get_comments_lazy`）、回复评论（`send_comment`）、以及处理私信会话/聊天历史（`get_sessions`、`fetch_session_msgs`、`send_msg`）。通过配置文件中的 SESSDATA/bili_jct/DedeUserID 创建 `Credential` 进行认证。

**`scheduler_engine.py`** — 创建 `AsyncIOScheduler`，包含三种任务类型：
- **发布动态** — 每个 `post_times` 条目创建一个 `CronTrigger`（随机偏移 ±5–10 分钟，模拟自然行为）。
- **检查评论** — 每 `comment_check_interval_minutes` 分钟运行一次，检查近期动态下的新评论。对每条新评论，按 `reply_probability` 概率随机回复，并模拟人类延迟（5–30 秒）。
- **检查私信** — 每 `private_msg_check_interval_minutes` 分钟运行一次。从 API 同步聊天记录，按 msg_key 去重，以模拟延迟（3–15 秒）回复。

**`config_loader.py`** — 基于 JSON 的持久化层。管理 config.json（B站凭证、DeepSeek 凭证、人设、调度配置）和 memory.json（动态历史、已回复评论、已点赞评论、每个用户的私信聊天历史）。所有状态在任务启动时从磁盘加载，每次变更后保存。

**`get_rid.py`** — 调试工具：通过 `Dynamic.get_info()` 获取已发布动态的真实评论 RID 和 `comment_type`。用于验证 API 响应结构。

**`debug_comment.py`** — 调试工具：用已知 RID 测试全部三种 `CommentResourceType` 枚举（DYNAMIC=17、DYNAMIC_DRAW=11、ARTICLE=12），找出能正确获取评论的类型。

### 数据流

1. **发布动态**：`bot.py` 或调度器 → `AIContentGenerator.generate_dynamic()` → DeepSeek API → `bilibili_client.post_dynamic()` → 将返回的 dyn_id_str 记录到 `memory.json`
2. **回复评论**：调度器 → `get_comments_by_rid()` 获取近期动态的评论 → `is_comment_replied()` 去重 → `AIContentGenerator.generate_reply()` → `reply_to_comment()` → 记录到 memory
3. **回复私信**：调度器 → `get_private_sessions()` → `get_chat_history()` → `sync_chat_messages_from_api()` → 去重 → `AIContentGenerator.generate_private_reply()`（带上聊天上下文） → `send_private_msg()` → 记录

### 核心数据：memory.json

```json
{
  "dynamic_history": [{ "dynamic_id", "dyn_rid", "comment_type", "content", "time" }],
  "replied_comments": [{ "comment_rpid", "reply_content", "time" }],
  "liked_comments": [{ "comment_rpid", "time" }],
  "persona_state": { "current_mood", "recent_topics", "mentioned_things" },
  "private_chats": {
    "<talker_id>": { "username", "last_msg_key", "last_seqno", "chat_history": [{ "role", "content", "time", "msg_key" }], "replied_msg_keys": [] }
  }
}
```

- `dynamic_history`：最多保留 50 条
- `replied_comments`：最多保留 100 条
- `chat_history`（每个用户）：最多保留 20 条
- `replied_msg_keys`（每个用户）：最多保留 100 条

### 配置：config.json

完整 schema 见 `config.example.json`。主要字段：`bilibili`（SESSDATA、bili_jct、DedeUserID）、`deepseek`（api_key、model、base_url）、`persona`（name、gender、age、city、personality、speaking_style、topics、taboos、fans_name）、`schedule`（post_times、comment_check_interval_minutes、like_probability、reply_probability、private_msg_check_interval_minutes、private_msg_reply_probability）。

## 注意事项

- **B站认证令牌会过期**（约 30 天）。SESSDATA、bili_jct 和 DedeUserID 需要手动从浏览器 Cookie 中刷新。
- **RID ≠ dyn_rid**。API 的 `send_dynamic` 返回 `dyn_id_str` 但不返回评论 RID。代码会调用 `Dynamic.get_info()` → `item.basic.rid_str` 和 `item.basic.comment_type` 来获取真实 RID。如果此过程失败（例如 API 变更），评论互动会回退到 RID=0，静默失败。
- **评论类型重要**。文字动态使用 `CommentResourceType.DYNAMIC`（17），但图文动态使用 `DYNAMIC_DRAW`（11）。comment_type 按动态存储在 memory.json 中。
- **DeepSeek 模型名**：使用 `deepseek-v4-flash`（从旧版 `deepseek-chat` 更新，旧版将于 2026-07-24 停用）。
- **无错误恢复**：发布或回复失败时仅记录错误，无重试机制。
- **类人延迟**：回复评论前随机等待 5–30 秒，回复私信前等待 3–15 秒——这些都是硬编码的 sleep。
- **无测试**：项目没有任何测试用例。`debug_comment.py` 和 `get_rid.py` 是仅有的验证工具。