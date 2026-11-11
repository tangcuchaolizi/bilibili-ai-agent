"""
AI 内容生成模块
使用 DeepSeek API 根据人设生成动态内容、评论回复和私信回复
"""
import json
import random
from datetime import datetime
from typing import Optional
from openai import OpenAI
from config_loader import load_config, load_memory, get_recent_dynamics_text
from web_search import WebSearcher, WEB_SEARCH_TOOL


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

今天日期：{today} {weather_note}

## 核心规则（必须严格遵守）

1. **保持人设一致**：你说的每一句话都要像{p['name']}会说的话，不能露出"我是AI"的痕迹
2. **内容真实感**：写的是日常生活中的小事，不要写宏大的观点或过于完美的总结
3. **口语化**：像发朋友圈一样自然
4. **长度适中**：动态控制在30-120字之间，不要太长也不要太短
5. **禁忌话题**：不能涉及 {', '.join(p['taboos'])},被问到就含糊其辞地带过
6. **不要用以下AI痕迹词**：综上所述、首先/其次/最后、不可否认、众所周知、作为AI等
7. **随机性**：同样的指令应该产生完全不同的内容，不要模板化

## 可以写的话题
{', '.join(p['topics'])}

## 互动风格
- 面对粉丝调侃可以傲娇反驳，但偶尔也会撒娇
- 可以吐槽生活小事
- 不要每次都回复感谢，要像朋友聊天一样自然"""

    def _decide_search_query(self, scenario: str, context_text: str) -> str:
        """
        搜索触发判断（低成本 LLM 预判调用，仅用于动态生成场景）。
        返回: 搜索词（需要搜索时）或空字符串（不需要时）
        """
        today = datetime.now().strftime("%Y年%m月%d日")
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": (
                        "你是一个搜索需求判断器。判断给定的场景是否需要联网获取实时信息。"
                        "以下情况需要搜索：涉及时效性信息（今日新闻、近期热点、最新更新）、"
                        "具体事实数据（天气、比分、发售日期、价格）、人物或作品的具体信息。"
                        f"当前日期：{today}。"
                        "如果需要搜索，只输出一个简短的中文搜索词（15字以内），不要输出任何其他文字。"
                        "如果不需要搜索，只输出：NO_SEARCH"
                    )},
                    {"role": "user", "content": f"场景：{scenario}\n内容：{context_text}"},
                ],
                max_tokens=30,
                temperature=0,
            )
            answer = response.choices[0].message.content.strip()
            answer = answer.strip('"').strip("'")
            if answer.upper().startswith("NO_SEARCH") or not answer:
                return ""
            return answer[:50]
        except Exception as e:
            print(f"  [搜索] 触发判断失败（按不搜索处理）: {e}")
            return ""

    def _search_reference(self, scenario: str, context_text: str) -> str:
        """
        动态生成场景的搜索流程：LLM判断话题是否需要实时信息 → 搜索 → 格式化为参考资料文本。
        未启用/限流/失败时降级为空字符串，不影响正常生成。
        """
        try:
            searcher = WebSearcher()
            if not searcher.enabled:
                return ""
            query = self._decide_search_query(scenario, context_text)
            if not query:
                print(f"  [搜索] {scenario}场景无需联网信息")
                return ""
            result = searcher.search(query)
            if not result:
                return ""
            return "## 搜索到的参考资料（仅供参考，不要照搬原文）\n" + result
        except Exception as e:
            print(f"  [搜索] 搜索流程异常（已跳过）: {e}")
            return ""

    def generate_dynamic(self) -> str:
        """生成一条日常动态"""
        memory = load_memory()
        recent = get_recent_dynamics_text(memory, limit=3)
        p = self.persona

        # 随机选择一个话题方向
        topic = random.choice(p["topics"])

        # 搜索触发：LLM判断话题是否需要实时资讯（如近期热点、新番动态），需要则搜索参考资料
        search_ref = self._search_reference("dynamic", f"想发一条关于「{topic}」的日常动态，需要最新的相关资讯作灵感")

        user_prompt = f"""请以{p['name']}的身份，写一条B站动态。

要求：
- 话题方向（但不限于）：{topic}
- 长度：30-120字
- 要像真实的人在分享生活，不是在写日记
- 加入一些具体的生活细节（时间、地点、食物名字等）
- 可以带一点小情绪或吐槽

{f"最近发过的动态（不要重复类似内容）：\n{recent}" if recent != "（还没有发过动态）" else ""}

{search_ref + chr(10) if search_ref else ""}只输出动态内容本身，不要输出任何其他文字、标题或解释。"""

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

    def generate_private_reply(self, user_message: str, username: str, chat_history: Optional[list] = None,
                               image_url: Optional[str] = None) -> str:
        """
        生成私信回复（支持联网搜索和图片理解）
        user_message: 用户最新发的消息（图片消息为 "[图片]"）
        username: 对方用户名
        chat_history: 聊天历史记录 [{"role": "user"/"assistant", "content": "..."}]
        image_url: 最新消息附带的图片地址（可选，传入后走视觉理解）
        """
        p = self.persona
        if chat_history is None:
            chat_history = []

        searcher = WebSearcher()

        # 场景要求附加到系统提示词
        system_prompt = self._build_system_prompt() + f"""

## 当前场景：B站私信（对方是{username}）
- 像和朋友私聊一样自然、口语化，可以带点小情绪
- 长度：10-80字，可以主动抛话题引导聊天
- 不要说"谢谢你的私信"之类的客套话
- 结合聊天记录回答，不要重复说过的话；第一次聊天可以稍微热情一点
- **绝对禁止编造不存在的聊天内容**：聊天记录里没有的事情就当没发生过
- 如果遇到你不了解的事实性问题（新闻、时效信息、具体数据等），可以调用 web_search 工具搜索后再回答，回复时保持自己的说话风格，不要照搬搜索结果，也不要提到你搜索过
- 如果对方发来图片，根据图片内容自然地回应（比如夸夸、吐槽、提问），就像朋友发图给你看一样
- 只输出回复内容本身，不要输出其他任何文字"""

        # 标准 messages 结构：system + 聊天历史 + 最新消息
        messages = [{"role": "system", "content": system_prompt}]
        for msg in chat_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": self._build_user_content(user_message, image_url)})

        kwargs = dict(model=self.model, messages=messages, max_tokens=300, temperature=0.92)
        if searcher.enabled:
            kwargs["tools"] = [WEB_SEARCH_TOOL]

        try:
            message = self._chat_with_tool_loop(kwargs, searcher)
        except Exception as e:
            if image_url:
                # 视觉理解失败（如模型/图片URL不支持）时降级为纯文本兜底，确保消息能被处理
                print(f"  [图片] 视觉理解失败，降级为纯文本回复: {e}")
                messages[-1] = {"role": "user", "content": f"{user_message}（对方发来一张图片，但你看不清图片内容，请自然地回应，不要提及看不清）"}
                kwargs["messages"] = messages
                message = self._chat_with_tool_loop(kwargs, searcher)
            else:
                raise

        content = (message.content or "").strip()
        if content.startswith('"') and content.endswith('"'):
            content = content[1:-1]
        return content

    @staticmethod
    def _build_user_content(user_message: str, image_url: Optional[str] = None):
        """构建用户消息内容：有图片时返回多模态结构，否则返回纯文本"""
        if image_url:
            return [
                {"type": "text", "text": user_message or "[图片]"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        return user_message

    def _chat_with_tool_loop(self, kwargs: dict, searcher: WebSearcher):
        """
        执行一次 LLM 调用；如模型请求联网搜索则执行搜索并二次生成。
        返回最终 message 对象。
        """
        messages = kwargs["messages"]
        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        # 模型决定联网搜索：执行搜索 → 基于结果二次生成
        if searcher.enabled and message.tool_calls:
            messages.append(message)
            for tc in message.tool_calls:
                if tc.function.name != "web_search":
                    continue
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = searcher.search(args.get("query", ""))
                if not result:
                    result = "（搜索未返回可用结果）"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=300,
                temperature=0.92,
            )
            message = response.choices[0].message
        return message
