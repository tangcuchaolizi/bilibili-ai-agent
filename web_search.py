"""
联网搜索模块
默认后端：必应中国（cn.bing.com）RSS 接口 —— 完全免费、无需注册、国内直连
可选后端：博查（Bocha）API —— 在 config.json 填入 api_key 后自动切换（中文搜索质量更好）

统一特性：
- 每日搜索次数上限（类级计数，跨实例共享），防止请求过度
- 结果内存缓存（TTL 可配置），相同查询不重复搜索
- 禁用/限流/网络失败时返回空字符串，优雅降级不影响主流程
- 全程打印 [搜索] 日志，过程对用户透明
"""
import time
import html
import xml.etree.ElementTree as ET
from datetime import date

import httpx
from config_loader import load_config

BING_RSS_URL = "https://cn.bing.com/search"
BOCHA_URL = "https://api.bochaai.com/v1/web-search"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


class WebSearcher:
    """联网搜索封装（同步调用，与 ai_generator 保持一致）"""

    # 类级共享：每日搜索计数（跨实例生效，进程重启归零）
    _today = None
    _count = 0
    # 类级共享：查询结果缓存 {query: (结果文本, 时间戳)}
    _cache = {}

    def __init__(self):
        config = load_config()
        ws = config.get("web_search", {})
        self.api_key = ws.get("api_key", "")
        # 有博查密钥则用博查（质量更好），否则用必应 RSS（免费）
        self.backend = "bocha" if self.api_key else "bing"
        self.enabled = ws.get("enabled", True)
        self.max_per_day = int(ws.get("max_per_day", 30))
        self.cache_minutes = int(ws.get("cache_minutes", 60))

    def _allow(self) -> bool:
        """每日搜索次数限流（类级计数，所有实例共享）"""
        today = date.today()
        if WebSearcher._today != today:
            WebSearcher._today = today
            WebSearcher._count = 0
        return WebSearcher._count < self.max_per_day

    def _search_bing(self, query: str, count: int) -> list:
        """必应中国 RSS 搜索，返回 [(标题, 摘要), ...]"""
        resp = httpx.get(
            BING_RSS_URL,
            params={"q": query, "format": "rss", "count": count},
            timeout=10,
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        items = []
        for it in root.findall(".//item")[:count]:
            title = html.unescape(it.findtext("title") or "").strip()
            desc = html.unescape(it.findtext("description") or "").strip()
            if title and desc:
                items.append((title, desc))
        return items

    def _search_bocha(self, query: str, count: int) -> list:
        """博查 API 搜索，返回 [(标题, 摘要), ...]"""
        resp = httpx.post(
            BOCHA_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "summary": True, "count": count},
            timeout=10,
        )
        if resp.status_code != 200:
            return []
        pages = resp.json().get("data", {}).get("webPages", {}).get("value", [])
        items = []
        for p in pages:
            title = (p.get("name") or "").strip()
            summary = (p.get("summary") or p.get("snippet") or "").strip()
            if title and summary:
                items.append((title, summary))
        return items

    @staticmethod
    def _format(items: list, max_desc_chars: int = 150) -> str:
        """将结果格式化为注入 Prompt 的编号文本（截断摘要避免信息过载）"""
        lines = []
        for i, (title, desc) in enumerate(items, 1):
            if len(desc) > max_desc_chars:
                desc = desc[:max_desc_chars] + "..."
            lines.append(f"{i}. {title}: {desc}")
        return "\n".join(lines)

    def search(self, query: str, count: int = 5) -> str:
        """
        执行联网搜索，返回格式化的结果文本（编号 + 标题 + 摘要）
        禁用/限流/失败时返回空字符串，调用方按"无结果"处理
        相同查询在缓存有效期内直接返回缓存，不消耗每日次数
        """
        query = (query or "").strip()
        if not self.enabled or not query:
            return ""

        # 先查缓存：命中直接返回，不消耗每日次数（缓存不受限流影响）
        cached = WebSearcher._cache.get(query)
        if cached and time.time() - cached[1] < self.cache_minutes * 60:
            print(f"  [搜索] 命中缓存: {query}")
            return cached[0]

        # 限流只拦截真实搜索
        if not self._allow():
            print(f"  [搜索] 已达每日上限（{self.max_per_day}次），跳过: {query}")
            return ""

        print(f"  [搜索] 正在搜索({self.backend}): {query}")
        try:
            if self.backend == "bocha":
                items = self._search_bocha(query, count)
            else:
                items = self._search_bing(query, count)

            if not items:
                print("  [搜索] 无结果")
                return ""

            result = self._format(items)
            WebSearcher._count += 1
            # 只缓存有效结果（失败/空结果不缓存，下次可重试）
            WebSearcher._cache[query] = (result, time.time())
            print(f"  [搜索] 获得 {len(items)} 条结果")
            return result
        except Exception as e:
            print(f"  [搜索] 搜索失败（已跳过，不影响回复）: {e}")
            return ""


# DeepSeek function calling 的工具定义
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "搜索互联网获取你不知道或不确定的信息。"
            "当用户问到时效性、新闻、具体数据、你不了解的事物等知识库外问题时调用；"
            "日常聊天、情感交流、关于你自己的话题不要调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，简洁明确"}
            },
            "required": ["query"],
        },
    },
}
