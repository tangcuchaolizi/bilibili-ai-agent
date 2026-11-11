# B站 AI 智能体（本地笔记本版）

一个基于 DeepSeek + bilibili-api-python 的 B站自动化智能体，适合在笔记本上随开随用。可以：
- 每天定时发布"日常化"的动态（像真人一样分享生活）
- 自动回复评论区的新评论
- 随机点赞粉丝评论

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
├── 启动智能体.bat        # Windows 双击启动脚本
├── bot.py                # 主程序入口
├── ai_generator.py       # AI 内容生成（DeepSeek）
├── bilibili_client.py    # B站 API 交互
├── scheduler_engine.py   # 定时调度引擎
├── config_loader.py      # 配置和记忆管理
├── config.example.json   # 配置文件模板
├── config.json           # 你的配置文件（需手动创建）
├── memory.json           # 记忆存储（自动生成）
├── requirements.txt      # Python 依赖
└── venv/                 # 虚拟环境（自动创建）
```

---

## 快速开始

### 方式一：双击启动（推荐新手）

1. 复制 `config.example.json` 为 `config.json`，填入你的信息（见下方配置说明）
2. **双击 `启动智能体.bat`**
3. 第一次会自动创建虚拟环境并安装依赖
4. 之后每次双击即可运行

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
[交互] 你想现在就发一条动态吗？
  1. 立即发一条动态
  2. 只启动后台定时（下次到点自动发）
  3. 发一条并启动后台（默认）
```

- **选项1**：心血来潮想立刻发动态，发完就退出
- **选项2**：不发动态，只开后台，到配置的定时点自动发
- **选项3（默认）**：立刻发一条 + 开后台，适合日常启动

---

## 配置项说明

### 调度时间 (config.json -> schedule)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `post_times` | 每天发动态的时间点 | `["08:30", "12:15", "18:45", "22:00"]` |
| `comment_check_interval_minutes` | 检查新评论的间隔 | 15 |
| `like_probability` | 点赞概率 (0-1) | 0.6 |
| `reply_probability` | 回复概率 (0-1) | 0.8 |

### 记忆系统

所有历史数据存储在 `memory.json` 中：
- `dynamic_history`: 已发布的动态记录（最近50条）
- `replied_comments`: 已回复的评论（最近100条）
- `liked_comments`: 已点赞的评论（最近100条）
- `persona_state`: 当前人设状态

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
