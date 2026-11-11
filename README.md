# B站 AI 智能体（本地笔记本版）

一个基于 DeepSeek + bilibili-api-python 的 B站自动化智能体，适合在笔记本上随开随用。可以：
- 每天定时发布"日常化"的动态（像真人一样分享生活）
- 自动回复评论区的新评论
- 自动回复私信

---

## 项目特点（本地模式）

- **随开随用**：启动时可以选择"立刻发一条"或"只开后台定时"
- **笔记本友好**：运行时内存仅 50-80 MB，完全不吃性能
- **零配置一键启动**：双击 `启动智能体.bat` 自动安装依赖、检查配置
- **记事本式管理**：所有记忆存储在 JSON 文件中

---

## 项目结构

```
bilibili-ai-agent/
├── start.bat             # Windows 双击启动脚本（默认GUI，console参数进终端模式）
├── bot.py                # 终端模式入口
├── bot_gui.py            # 图形界面（Tkinter，含私信持续聊天面板）
├── api.py                # FastAPI RESTful 服务层
├── api_models.py         # API 请求/响应 Pydantic 模型
├── panel.html            # 简易控制面板（访问 / 时的大按钮界面）
├── ai_generator.py       # AI 内容生成（DeepSeek，支持图片理解+联网搜索）
├── bilibili_client.py    # B站 API 交互
├── scheduler_engine.py   # 定时调度引擎
├── chat_store.py         # 私信聊天存储（SQLite）
├── web_search.py         # 联网搜索（必应RSS免费/博查可选）
├── config_loader.py      # 配置和记忆管理
├── config.example.json   # 配置文件模板
├── config.json           # 你的配置文件（需手动创建）
├── memory.json           # 记忆存储（自动生成）
├── chat_history.db       # 私信聊天数据库（自动生成）
├── test_chat_store.py    # 存储层测试脚本
├── test_web_search.py    # 联网搜索+图片回复测试脚本
├── test_api.py           # API 接口测试脚本
├── requirements.txt      # Python 依赖
└── venv/                 # 虚拟环境（自动创建）
```

---

## 快速开始

### 方式一：双击启动（推荐新手）

1. 复制 `config.example.json` 为 `config.json`，填入你的信息（见下方配置说明）
2. **双击 `start.bat`** —— 默认打开图形界面（GUI）
3. 第一次会自动创建虚拟环境并安装依赖
4. 之后每次双击即可运行

> 如需旧版终端模式：`start.bat console`

GUI 界面包含两个标签页：
- **控制台**：发动态、检查评论/私信、统计、私信总览、后台定时开关，实时日志显示
- **私信聊天**：选择会话查看历史记录，可持续多轮对话（每10秒自动拉取新消息并回复），也可以手动插话

### 方式二：命令行启动

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境
venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 确保 config.json 已配置好

# 5. 运行
python bot.py
```

### 方式三：API 服务模式（FastAPI）

```bash
uvicorn api:app --host 127.0.0.1 --port 8100
# 或双击 start.bat api
```

启动后浏览器打开 **http://127.0.0.1:8100** 会看到一个**简洁控制面板**（大按钮点一下就能用，无需懂技术）；
技术接口文档在 **http://127.0.0.1:8100/docs**（Swagger UI，适合对接其他工具时用）。
（端口用 8100 是为了避开 SillyTavern 等工具默认占用的 8000）

所有接口统一响应格式：`{"code": 0, "message": "ok", "data": {...}}`，code 非 0 时 code 即 HTTP 状态码。

| 分组 | 方法 | 路径 | 说明 |
|------|------|------|------|
| 系统 | GET | /api/health | 健康检查 |
| 系统 | GET | /api/config/persona | 查看人设配置（不含凭证） |
| 系统 | GET | /api/stats/today | 今日动态/评论/私信统计 |
| 动态 | GET | /api/dynamics | 最近发布的动态记录 |
| 动态 | POST | /api/dynamics | 生成并发布一条动态 |
| 动态 | POST | /api/dynamics/preview | 仅生成不发布（预览） |
| 评论 | GET | /api/comments/replied | 已回复评论记录 |
| 评论 | POST | /api/comments/check | 立即检查并回复评论 |
| 私信 | GET | /api/messages/sessions | 会话总览（消息数/待回复数） |
| 私信 | GET | /api/messages/sessions/{id} | 指定用户的聊天记录 |
| 私信 | GET | /api/messages/sessions/{id}/pending | 该会话待回复消息 |
| 私信 | POST | /api/messages/sessions/{id}/messages | 手动发送私信 |
| 私信 | POST | /api/messages/check | 立即检查并回复私信 |
| 搜索 | POST | /api/search | 联网搜索（必应RSS/博查） |
| 调度器 | GET | /api/scheduler/status | 定时任务状态 |
| 调度器 | POST | /api/scheduler/start | 启动后台定时 |
| 调度器 | POST | /api/scheduler/stop | 停止后台定时 |

接口测试：`python -B test_api.py`（40项，全部 mock 无需真实网络）

---

## 配置说明

### 第一步：复制配置模板

```bash
cp config.example.json config.json
```

### 第二步：获取 B站 Cookie

1. 在电脑浏览器上登录 [bilibili.com](https://bilibili.com)
2. 按 `F12` 打开开发者工具
3. 切换到 **Application（应用）** → **Cookies**
4. 找到并复制这三个值：
   - `SESSDATA`
   - `bili_jct`（这是 CSRF Token）
   - `DedeUserID`
5. 填入 `config.json` 的 `bilibili` 部分

### 第三步：获取 DeepSeek API Key

1. 访问 [platform.deepseek.com](https://platform.deepseek.com) 注册账号
2. 在 **API Keys** 页面创建一个新的密钥
3. 填入 `config.json` 的 `deepseek` 部分

### 第四步：自定义人设

编辑 `config.json` 中的 `persona` 部分，定义你的AI角色的：
- 名字、性别、年龄、城市
- 性格特征和说话风格
- 感兴趣的话题
- 禁忌话题

---

## 使用方式

启动后会有交互菜单：

```
[交互] 请选择操作：
  1. 立即发一条动态（发完退出）
  2. 立即检查并回复评论（测试）
  3. 发一条动态 + 启动后台定时
  4. 立即检查并回复私信（测试）
  5. 查看今日统计数据
  6. 只启动后台定时任务
  7. 查看私信总览
```

- **选项1**：立刻生成并发一条动态，发完就退出
- **选项2/4**：单次测试评论/私信回复流程
- **选项3（默认）**：立刻发一条 + 开后台定时，适合日常启动
- **选项5**：查看今日动态/评论/私信统计
- **选项6**：不发动态，只开后台，到配置的定时点自动发
- **选项7**：查看所有私信会话总览（消息数、最近消息、待回复数）

---

## 配置项说明

### 调度时间 (config.json -> schedule)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `post_times` | 每天发动态的时间点 | `["08:30", "12:15", "18:45", "22:00"]` |
| `comment_check_interval_minutes` | 检查新评论的间隔 | 15 |
| `private_msg_check_interval_minutes` | 检查新私信的间隔 | 5 |
| `private_msg_context_limit` | 私信回复时携带的上下文条数 | 50 |

### 联网搜索 (config.json -> web_search)

让 AI 获得实时信息能力，覆盖两个场景：
- **私信回复**：遇到人设知识范围外的问题（新闻、时效信息、具体数据等），模型自动联网搜索后再回答
- **动态生成**：AI 先判断话题是否需要实时资讯（如近期热点、新番动态），需要则搜索参考资料丰富内容

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `api_key` | 博查AI搜索 API 密钥，留空则禁用联网搜索 | `""` |
| `enabled` | 是否启用 | `true` |
| `max_per_day` | 每日搜索次数上限（防止费用失控） | 20 |
| `cache_minutes` | 相同查询的结果缓存时长（分钟），缓存命中不消耗次数 | 60 |

获取密钥：访问 [open.bochaai.com](https://open.bochaai.com) 注册 → 创建 API Key → 填入 config.json，无需改代码。搜索失败时自动降级，不影响正常回复和发布。

### 记忆系统

**动态和评论**存储在 `memory.json` 中：
- `dynamic_history`: 已发布的动态记录（最近50条）
- `replied_comments`: 已回复的评论（最近100条）
- `persona_state`: 当前人设状态

**私信聊天**存储在 SQLite 数据库 `chat_history.db` 中（自动创建）：
- 消息永久保存，无条数上限，上下文记忆更长
- 通过消息状态标记区分已处理/待处理，不会重复回复历史消息
- 旧版 memory.json 中的私信记录会在首次运行时自动迁移入库

运行测试：`python -B test_chat_store.py`（存储层）、`python -B test_web_search.py`（联网搜索链路，无需密钥）

---

## 费用估算

使用 DeepSeek-V4-Flash 模型：
- 每条动态生成约消耗 500-1000 tokens（输出）
- 每条评论回复约消耗 200-500 tokens（输出）
- **每次启动运行约 0.01-0.05 元**，月费不到 2 元（取决于使用频率）

---

## 注意事项

1. **SESSDATA 会过期**（约30天），过期后需要重新获取 Cookie 并更新 `config.json`
2. **不要设置过高的频率**，以免被B站风控（默认配置已经很保守了）
3. **先用小号测试**，确认稳定后再用主号
4. **内容合规**：人设中的 taboos 列表会传给AI，但不保证100%过滤，建议先观察几天
5. **旧模型将停用**：2026年7月24日起 `deepseek-chat` 将停用，代码已使用新名称 `deepseek-v4-flash`
6. **电脑关机就停止**：这是本地运行模式，关机/休眠后 Bot 会停止，下次开机后重新启动即可
