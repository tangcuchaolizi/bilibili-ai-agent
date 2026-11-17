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
python bot.py                # 终端交互菜单
python bot_gui.py            # 图形界面（或双击 start.bat）
uvicorn api:app --host 127.0.0.1 --port 8100   # API 服务（或 start.bat api），文档 http://127.0.0.1:8100/docs（8100 避开 SillyTavern 默认的 8000）

# 测试
python -B test_api.py           # API 接口测试（TestClient + mock，无需真实网络）

# Docker
docker compose up -d         # 容器运行（挂载 config.json + memory.json + chat_history.db）
```

项目有自包含测试脚本（test_api.py 等），未配置代码检查工具或格式化工具。

## 架构

### 模块依赖关系（调用方 → 被调用方）

```
bot.py → scheduler_engine.py, ai_generator.py, bilibili_client.py
bot_gui.py → scheduler_engine.py, bilibili_client.py, chat_store.py, config_loader.py
api.py → api_models.py, panel.html, scheduler_engine.py, ai_generator.py, bilibili_client.py, chat_store.py, config_loader.py, web_search.py
scheduler_engine.py → ai_generator.py, bilibili_client.py, config_loader.py, chat_store.py
ai_generator.py → config_loader.py, web_search.py
bilibili_client.py → config_loader.py
config_loader.py → config.json, memory.json
chat_store.py → chat_history.db (SQLite)
web_search.py → config.json, 必应中国RSS / 博查API (httpx)
```

### 核心模块

**`bot.py`** — 终端模式入口。打印交互菜单（7个选项：发动态、测评论、发动态+定时、测私信、统计、只定时、私信总览），然后选择发一条动态后退出，或启动 APScheduler 并无限循环。基于 asyncio 运行。

**`bot_gui.py`** — 图形界面入口（Tkinter，零新增依赖），由 `start.bat` 默认以 pythonw 无终端启动（`start.bat console` 回退终端模式）。两个标签页：控制台（全部动作按钮 + 实时日志，print 经 GuiLogger 同时写入 bot_gui.log）和私信聊天（会话列表 + 历史展示 + 每10秒轮询新消息自动回复 + 手动发送插话，支持持续多轮对话）。asyncio 事件循环跑在后台线程，GUI 操作经 `run_coroutine_threadsafe` 提交；**AsyncIOScheduler.start() 必须在事件循环线程内调用**（get_running_loop 限制）。

**`api.py`** — FastAPI RESTful 封装层（`start.bat api` 或 `uvicorn api:app` 启动）。根路径 `/` 返回 `panel.html` 简易控制面板（面向普通用户的大按钮界面），技术文档在 `/docs`。只编排调用现有模块，不含业务逻辑。统一响应格式 `{code, message, data}`（Pydantic 泛型 `ApiResponse[T]`，见 api_models.py）。端点：系统（health/persona/stats）、动态（列表/发布/预览）、评论（已回复记录/触发检查）、私信（会话总览/聊天记录/待回复/手动发送/触发检查）、搜索（/api/search）、调度器（status/start/stop，模块级单例）。CORS 全放开（本地工具），请求日志中间件写控制台 + api.log；异常统一格式（注意：需注册 `starlette.exceptions.HTTPException` 父类 handler 才能覆盖未知路由 404）。

**`api_models.py`** — API 的 Pydantic 请求/响应模型（Field 校验 + OpenAPI 示例）。

**`ai_generator.py`** — 封装 DeepSeek/OpenAI 客户端。根据 `persona` 配置（姓名、年龄、城市、性格、说话风格、话题、禁忌）构建系统提示词。生成 30–120 字的动态、10–60 字的评论回复、10–80 字的私信回复，温度设置为 0.9–0.95 以增加随机性。私信回复支持**图片理解**（`generate_private_reply(..., image_url=...)` 构建多模态消息，视觉调用失败自动降级纯文本重试）和**联网搜索**（function calling，模型自主触发，工具结果经 `_chat_with_tool_loop` 二次生成）。

**`bilibili_client.py`** — 封装 `bilibili-api-python`，实现：发布文字动态（`BuildDynamic` + `send_dynamic`）、通过 `Dynamic.get_info()` 获取评论信息、读取评论（`get_comments_lazy`）、回复评论（`send_comment`）、以及处理私信会话/聊天历史（`get_sessions`、`fetch_session_msgs`、`send_msg`）。私信消息按 msg_type 解析：1=文字、2=图片（content JSON 提取 `url` 存入 `image_url` 字段）、其他类型（撤回/系统消息）text 为空仅用于推进 seqno。通过配置文件中的 SESSDATA/bili_jct/DedeUserID 创建 `Credential` 进行认证。

**`scheduler_engine.py`** — 创建 `AsyncIOScheduler`，包含三种任务类型：
- **发布动态** — 每个 `post_times` 条目创建一个 `CronTrigger`。
- **检查评论** — 每 `comment_check_interval_minutes` 分钟运行一次，检查近期动态下的新评论并逐条回复。
- **检查私信** — 每 `private_msg_check_interval_minutes` 分钟运行一次。消息同步入 SQLite 后，仅回复 `processed=0` 的用户消息；首次跟踪的会话其历史消息只入库不回复；回复失败的消息下轮自动重试。

**`chat_store.py`** — 私信聊天的 SQLite 持久化层（`chat_history.db`）。两张表：`sessions`（talker_id、username、last_seqno）和 `messages`（msg_key 唯一索引、image_url、is_self、processed 状态标记、timestamp）。消息永久存储，上下文检索长度由 `private_msg_context_limit` 配置（默认50条）。机器人本地回复以 `msg_key=0` 插入，API 回显时自动关联去重。不支持的消息类型（撤回等）不入库但仍推进 last_seqno，防止每轮重复拉取。旧库自动 schema 迁移（`_migrate_schema` 补齐缺失列）。启动时自动将旧版 memory.json 的 private_chats 一次性迁移入库（幂等）。所有 SQL 参数化查询，WAL 模式。

**`web_search.py`** — 联网搜索模块，双后端设计：默认使用必应中国（cn.bing.com）RSS 接口（完全免费、无需注册、国内直连）；在 config.json 填入 `web_search.api_key` 后自动切换到博查（Bocha）API（中文搜索质量更好）。带每日次数上限（`max_per_day`，默认30，类级计数跨实例共享）、内存 TTL 结果缓存（`cache_minutes`，默认60分钟，缓存命中不消耗次数且不受限流影响；失败/空结果不缓存以便重试）、摘要截断（150字）避免信息过载。提供 DeepSeek function calling 的工具定义（`WEB_SEARCH_TOOL`）。

**`config_loader.py`** — 基于 JSON 的持久化层。管理 config.json（B站凭证、DeepSeek 凭证、人设、调度配置）和 memory.json（动态历史、已回复评论）。私信聊天数据已迁移至 SQLite（见 chat_store.py），memory.json 中的 private_chats 仅作为迁移前的历史备份保留。

**`test_api.py`** — API 接口测试（46项，TestClient + 全 mock，无需真实网络）。

### 数据流

1. **发布动态**：`bot.py` 或调度器 → `AIContentGenerator.generate_dynamic()`（先经低成本 LLM 预判调用判断话题是否需要实时信息，需要则用 WebSearcher 搜索并将参考资料注入 Prompt） → DeepSeek API → `bilibili_client.post_dynamic()` → 将返回的 dyn_id_str 记录到 `memory.json`
2. **回复评论**：调度器 → `get_comments_by_rid()` 获取近期动态的评论 → `is_comment_replied()` 去重 → `AIContentGenerator.generate_reply()` → `reply_to_comment()` → 记录到 memory
3. **回复私信**：调度器/GUI → `get_private_sessions()` → `get_chat_history()` → `chat_store.sync_messages()` 入库（去重+状态标记+图片提取） → `get_unprocessed()` → `AIContentGenerator.generate_private_reply()`（数据库上下文 + 图片视觉理解 + function calling 联网搜索） → `send_private_msg()` → `add_local_reply()` + `mark_processed()`

### 核心数据：memory.json + chat_history.db

memory.json（动态/评论相关）：
```json
{
  "dynamic_history": [{ "dynamic_id", "dyn_rid", "comment_type", "content", "time" }],
  "replied_comments": [{ "comment_rpid", "reply_content", "time" }],
  "persona_state": { "current_mood", "recent_topics", "mentioned_things" },
  "private_chats": {}
}
```

- `dynamic_history`：最多保留 50 条
- `replied_comments`：最多保留 100 条
- `private_chats`：已废弃，仅为旧数据迁移源保留

chat_history.db（SQLite，私信聊天）：
- `sessions` 表：talker_id (PK)、username、last_seqno
- `messages` 表：id 自增、msg_key（API消息唯一索引）、talker_id、content、msg_seqno、timestamp、is_self、processed（0=待回复/1=已处理）
- 消息永久存储，无条数上限；每日私信回复统计从该库查询

### 配置：config.json

完整 schema 见 `README.md` 的"配置说明"章节（含 config.json 模板）。主要字段：`bilibili`（SESSDATA、bili_jct、DedeUserID）、`deepseek`（api_key、model、base_url）、`persona`（name、gender、age、city、personality、speaking_style、topics、taboos、fans_name）、`schedule`（post_times、comment_check_interval_minutes、private_msg_check_interval_minutes、private_msg_context_limit）、`web_search`（api_key、enabled、max_per_day、cache_minutes）。

## 注意事项

- **B站认证令牌会过期**（约 30 天）。SESSDATA、bili_jct 和 DedeUserID 需要手动从浏览器 Cookie 中刷新。
- **RID ≠ dyn_rid**。API 的 `send_dynamic` 返回 `dyn_id_str` 但不返回评论 RID。代码会调用 `Dynamic.get_info()` → `item.basic.rid_str` 和 `item.basic.comment_type` 来获取真实 RID。如果此过程失败（例如 API 变更），评论互动会回退到 RID=0，静默失败。
- **评论类型重要**。文字动态使用 `CommentResourceType.DYNAMIC`（17），但图文动态使用 `DYNAMIC_DRAW`（11）。comment_type 按动态存储在 memory.json 中。
- **DeepSeek 模型名**：使用 `deepseek-v4-flash`（从旧版 `deepseek-chat` 更新，旧版将于 2026-07-24 停用）。
- **联网搜索默认免费可用**。默认后端为必应中国 RSS，无需任何密钥即可工作（国内直连）；填写博查 API Key（https://open.bochaai.com）后自动切换到博查后端获得更好的中文搜索质量。私信场景由模型通过 function calling 自主决定何时搜索；动态场景由低成本 LLM 预判调用决定（输出 NO_SEARCH 或搜索词）。搜索过程全程打印 `[搜索]` 日志，失败时优雅降级不影响主流程。
- **私信图片消息**。msg_type=2 的图片消息提取 image_url 入库（content 存 "[图片]" 占位），回复时走 DeepSeek 视觉理解（多模态消息），视觉调用失败自动降级纯文本，确保图片消息只被回复一次。
- **无错误恢复**：发布或回复失败时仅记录错误，无重试机制。
- **测试**：仅有 `test_api.py` 一个测试脚本（`test_chat_store.py`、`test_web_search.py`、`debug_comment.py`、`get_rid.py` 已删除）。