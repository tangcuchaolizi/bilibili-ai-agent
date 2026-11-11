"""
AI 内容生成模块
使用 DeepSeek API 根据人设生成动态内容、评论回复和私信回复
"""
import json
import random
from datetime import datetime
from openai import OpenAI
from config_loader import load_config, load_memory, get_recent_dynamics_text


class AIContentGenerator:
    """AI 内容生成器"""

    def __init__(self):
        config = load_config()
        ds_config = config["deepseek"]
        self.client = OpenAI(
            api_key=ds_config["api_key"],
            base_url=ds_config["base_url"],
        )
        self.model = ds_config["model"]
        self.persona = config["persona"]

    def _build_system_prompt(self) -> str:
        """构建系统 Prompt（人设核心）"""
        p = self.persona
        today = datetime.now().strftime("%Y年%m月%d日 %A")
        weather_note = "（你可以根据当前季节编造一个合理的天气感受）"

        return f"""你是{p['name']}，一个{p['age']}，在{p['city']}生活的{p['gender']}生。
你的性格是：{p['personality']}。
你说话的风格：{p['speaking_style']}。
你的粉丝叫你"{p['fans_name']}"。

今天日期：{today} {weather_note}

## 核心规则（必须严格遵守）

1. **保持人设一致**：你说的每一句话都要像{p['name']}会说的话，不能露出"我是AI"的痕迹
2. **内容真实感**：写的是日常生活中的小事，不要写宏大的观点或过于完美的总结
3. **口语化**：像发朋友圈一样自然
4. **长度适中**：动态控制在30-120字之间，不要太长也不要太短
5. **有趣味性**：让人看了觉得"嗯，这个猫娘还挺可爱的"
6. **禁忌话题**：绝对不能涉及 {', '.join(p['taboos'])}
7. **不要用以下AI痕迹词**：综上所述、首先/其次/最后、不可否认、众所周知、作为AI等
8. **随机性**：同样的指令应该产生完全不同的内容，不要模板化

## 可以写的话题
{', '.join(p['topics'])}

## 互动风格
- 面对粉丝调侃可以傲娇反驳，但偶尔也会撒娇
- 可以吐槽生活小事
- 不要每次都回复感谢，要像朋友聊天一样自然"""

    def generate_dynamic(self) -> str:
        """生成一条日常动态"""
        memory = load_memory()
        recent = get_recent_dynamics_text(memory, limit=3)
        p = self.persona

        # 随机选择一个话题方向
        topic = random.choice(p["topics"])

        user_prompt = f"""请以{p['name']}的身份，写一条B站动态。

要求：
- 话题方向（但不限于）：{topic}
- 长度：30-120字
- 要像真实的人在分享生活，不是在写日记
- 加入一些具体的生活细节（时间、地点、食物名字等）
- 可以带一点小情绪或吐槽

{f"最近发过的动态（不要重复类似内容）：\n{recent}" if recent != "（还没有发过动态）" else ""}

只输出动态内容本身，不要输出任何其他文字、标题或解释。"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=512,
            temperature=0.95,  # 高温度增加随机性
        )

        content = response.choices[0].message.content.strip()
        # 清理可能的多余引号
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        return content

    def generate_reply(self, comment_text: str, commenter_name: str, dynamic_text: str = "") -> str:
        """生成评论回复
        dynamic_text: 当前评论所在动态的内容，避免上下文串台
        """
        p = self.persona

        user_prompt = f"""你发布了一条动态："{dynamic_text}"

{commenter_name} 在这条动态下评论了："{comment_text}"

请以{p['name']}的身份，结合上面的动态内容自然地回复这条评论。

要求：
- 回复必须紧扣当前动态的主题，不要提及其他动态的内容
- 像朋友聊天一样自然，不要客套
- 长度：10-60字
- 符合{p['name']}的说话风格
- 可以是调侃、吐槽、撒娇或正常聊天
- 不要每次都说谢谢/感谢

只输出回复内容本身，不要输出其他任何文字。"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=256,
            temperature=0.9,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        return content

    def generate_private_reply(self, user_message: str, username: str, chat_history: list = None) -> str:
        """
        生成私信回复
        user_message: 用户最新发的消息
        username: 对方用户名
        chat_history: 聊天历史记录 [{"role": "user"/"assistant", "content": "..."}]
        """
        p = self.persona
        if chat_history is None:
            chat_history = []

        history_text = ""
        if chat_history:
            history_lines = []
            for msg in chat_history[-8:]:
                role_name = username if msg["role"] == "user" else p["name"]
                history_lines.append(f"{role_name}: {msg['content']}")
            history_text = "\n".join(history_lines)

        user_prompt = f"""你正在B站私信和{username}聊天。

{ f"之前的聊天记录：\n{history_text}\n" if history_text else "（这是你们第一次聊天，还没有聊天记录）\n" }
{username} 刚发了一条消息："{user_message}"

请以{p['name']}的身份，自然地回复这条私信。

要求：
- 像和朋友私聊一样自然、口语化
- 可以带点小情绪，不要太官方
- 长度：10-80字
- 符合{p['name']}的说话风格和人设
- 可以主动抛话题，引导继续聊天
- 不要说"谢谢你的私信"之类的客套话
- 结合上面的聊天记录回答，不要重复说过的话
- 如果是第一次聊天，可以稍微热情一点
- **绝对禁止编造不存在的聊天内容**：如果聊天记录为空或很少，请诚实地说"我们才刚认识呢"或"还没聊多少呢"，不要虚构养猫、吃饭等不存在的话题
- **只根据上面提供的聊天记录回答**，没有记录的事情就当没发生过

只输出回复内容本身，不要输出其他任何文字。"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=300,
            temperature=0.92,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        return content
