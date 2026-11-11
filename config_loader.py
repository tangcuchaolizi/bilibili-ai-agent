"""
配置加载模块
读取 config.json 和 memory.json，提供统一的配置访问接口
"""
import json
import os
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    config_path = os.path.join(PROJECT_DIR, "config.json")
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"找不到 config.json，请复制 config.example.json 为 config.json 并填入真实信息\n"
            f"路径: {config_path}"
        )
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_memory() -> dict:
    memory_path = os.path.join(PROJECT_DIR, "memory.json")
    if not os.path.exists(memory_path):
        return {
            "dynamic_history": [],
            "replied_comments": [],
            "conversation_context": [],
            "persona_state": {
                "current_mood": "普通",
                "recent_topics": [],
                "mentioned_things": [],
            },
            "private_chats": {},
        }
    with open(memory_path, "r", encoding="utf-8") as f:
        memory = json.load(f)
    if "private_chats" not in memory:
        memory["private_chats"] = {}
    return memory


def save_memory(memory: dict) -> None:
    memory_path = os.path.join(PROJECT_DIR, "memory.json")
    with open(memory_path, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def add_dynamic_record(memory: dict, dyn_id_str: str, content: str, dyn_rid: int = 0, comment_type: int = 17) -> None:
    """添加一条动态记录到记忆中，同时保存 dyn_rid 和 comment_type 用于评论"""
    memory["dynamic_history"].append({
        "dynamic_id": dyn_id_str,
        "dyn_rid": dyn_rid,
        "comment_type": comment_type,
        "content": content,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    memory["dynamic_history"] = memory["dynamic_history"][-50:]
    save_memory(memory)


def get_recent_dynamics(memory: dict, limit: int = 5) -> list:
    """获取最近发布的动态列表（包含 dyn_id_str、dyn_rid、comment_type）"""
    recent = memory["dynamic_history"][-limit:]
    # 兼容旧数据：没有 comment_type 的动态默认设为 17
    for d in recent:
        if "comment_type" not in d:
            d["comment_type"] = 17
    return [d for d in recent if d.get("dynamic_id") and d.get("dyn_rid")]


def add_reply_record(memory: dict, comment_rpid: str, reply_content: str) -> None:
    """添加一条回复记录"""
    memory["replied_comments"].append({
        "comment_rpid": comment_rpid,
        "reply_content": reply_content,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    memory["replied_comments"] = memory["replied_comments"][-100:]
    save_memory(memory)


def is_comment_replied(memory: dict, comment_rpid: str) -> bool:
    """检查评论是否已回复过"""
    replied_rpids = {str(r["comment_rpid"]) for r in memory["replied_comments"]}
    result = str(comment_rpid) in replied_rpids
    if not result:
        print(f"    [调试] is_comment_replied: rpid={comment_rpid} 不在已回复列表中")
        print(f"    [调试] 已回复列表前5个: {list(replied_rpids)[:5]}")
    return result


def get_recent_dynamics_text(memory: dict, limit: int = 5) -> str:
    """获取最近的动态内容（用于上下文参考）"""
    recent = memory["dynamic_history"][-limit:]
    if not recent:
        return "（还没有发过动态）"
    lines = []
    for d in recent:
        lines.append(f'[{d["time"]}] {d["content"]}')
    return "\n".join(lines)


def contains_bad_words(text: str, bad_words: list = None) -> bool:
    """
    检查文本是否包含不良关键词
    text: 要检查的文本
    bad_words: 不良关键词列表，默认为配置中的 taboos
    返回: True 如果包含不良关键词，False 否则
    """
    if bad_words is None:
        config = load_config()
        bad_words = config["persona"].get("taboos", [])
    
    text_lower = text.lower()
    for word in bad_words:
        if word.lower() in text_lower:
            return True
    return False


def get_daily_stats(memory: dict, date_str: str = None) -> dict:
    """
    获取指定日期的统计数据
    date_str: 日期字符串，格式 "YYYY-MM-DD"，默认为今天
    返回: {"dynamics": int, "replies": int}
    注意: 私信回复统计在 SQLite 中，见 chat_store.count_replies_on_date
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    stats = {
        "dynamics": 0,
        "replies": 0,
    }
    
    for dyn in memory.get("dynamic_history", []):
        if dyn.get("time", "").startswith(date_str):
            stats["dynamics"] += 1
    
    for reply in memory.get("replied_comments", []):
        if reply.get("time", "").startswith(date_str):
            stats["replies"] += 1
    
    return stats


def add_daily_stat_record(memory: dict, stat_type: str, count: int = 1) -> None:
    """
    添加每日统计记录
    stat_type: 统计类型 ("dynamic", "reply", "private_reply")
    count: 数量，默认1
    """
    today = datetime.now().strftime("%Y-%m-%d")
    memory.setdefault("daily_stats", {})
    if today not in memory["daily_stats"]:
        memory["daily_stats"][today] = {
            "dynamics": 0,
            "replies": 0,
            "private_replies": 0,
        }
    
    stat_map = {
        "dynamic": "dynamics",
        "reply": "replies",
        "private_reply": "private_replies",
    }
    
    key = stat_map.get(stat_type)
    if key:
        memory["daily_stats"][today][key] += count
    
    save_memory(memory)
