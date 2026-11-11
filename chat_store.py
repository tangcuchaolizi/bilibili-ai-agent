"""
私信聊天存储模块（SQLite）
解决两个核心问题：
1. 历史消息被误判为新消息 —— 通过 processed 状态标记 + msg_key 永久去重，
   首次跟踪会话时历史消息只入库不回复
2. 上下文记忆过短 —— 消息永久存储，上下文检索长度可配置（默认50条）

安全性：所有 SQL 均使用参数化查询，不拼接任何用户内容
性能：WAL 模式 + 针对性索引，单连接复用（单线程 asyncio 场景）
"""
import os
import time
import sqlite3
import threading
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, "chat_history.db")

_lock = threading.Lock()
_conn = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    talker_id   TEXT PRIMARY KEY,
    username    TEXT NOT NULL DEFAULT '',
    last_seqno  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_key    INTEGER NOT NULL DEFAULT 0,
    talker_id  TEXT NOT NULL,
    sender_uid TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    image_url  TEXT NOT NULL DEFAULT '',
    msg_seqno  INTEGER NOT NULL DEFAULT 0,
    timestamp  INTEGER NOT NULL DEFAULT 0,
    is_self    INTEGER NOT NULL DEFAULT 0,
    processed  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- API 消息的 msg_key 全局唯一（部分索引：本地消息 msg_key=0 不受约束）
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_msg_key
    ON messages(msg_key) WHERE msg_key > 0;
-- 待处理消息检索
CREATE INDEX IF NOT EXISTS idx_messages_talker_processed
    ON messages(talker_id, processed, id);
-- 上下文检索
CREATE INDEX IF NOT EXISTS idx_messages_talker_id
    ON messages(talker_id, id);
-- 每日回复统计
CREATE INDEX IF NOT EXISTS idx_messages_self_ts
    ON messages(is_self, timestamp);
"""


def get_conn() -> sqlite3.Connection:
    """获取模块级单例连接（懒加载，自动建表）"""
    global _conn
    with _lock:
        if _conn is None:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            _migrate_schema(conn)
            conn.commit()
            _conn = conn
    return _conn


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """旧库结构升级：为 messages 表补充缺失的列（幂等）"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "image_url" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_session(talker_id):
    """获取会话记录，不存在返回 None"""
    row = get_conn().execute(
        "SELECT * FROM sessions WHERE talker_id=?", (str(talker_id),)
    ).fetchone()
    return dict(row) if row else None


def sync_messages(talker_id, username: str, messages: list, my_uid, historical: bool = False) -> int:
    """
    将B站API返回的消息同步入库（事务）。
    historical=True: 首次跟踪该会话，所有消息标记 processed=1（历史消息只记录不回复）
    historical=False: 机器人自己的消息 processed=1，用户消息 processed=0（待回复）
    已存在的 msg_key 自动跳过（幂等）。
    返回: 新入库的消息条数
    """
    talker_id = str(talker_id)
    my_uid = str(my_uid)
    conn = get_conn()
    now = _now()
    inserted = 0

    with conn:
        conn.execute(
            "INSERT INTO sessions (talker_id, username, last_seqno, created_at, updated_at) "
            "VALUES (?,?,0,?,?) "
            "ON CONFLICT(talker_id) DO UPDATE SET "
            "username=excluded.username, updated_at=excluded.updated_at",
            (talker_id, username or "", now, now),
        )

        max_seqno = 0
        for msg in messages:
            seqno = int(msg.get("msg_seqno", 0) or 0)
            # 无论消息是否入库都推进 seqno（撤回/系统消息等不支持的类型不存储，
            # 但必须推进进度，否则每轮都会重复拉取同一条消息）
            max_seqno = max(max_seqno, seqno)

            content = str(msg.get("text", "") or "")
            image_url = str(msg.get("image_url", "") or "")
            if not content and not image_url:
                continue
            if image_url and not content:
                content = "[图片]"
            msg_key = int(msg.get("msg_key", 0) or 0)
            sender = str(msg.get("sender_uid", 0))
            ts = int(msg.get("timestamp", 0) or 0)
            is_self = 1 if sender == my_uid else 0

            if is_self:
                # 尝试关联本地插入的回复（msg_key=0），避免同一条回复在上下文中出现两次
                cur = conn.execute(
                    "UPDATE OR IGNORE messages SET msg_key=?, msg_seqno=?, timestamp=? "
                    "WHERE id=(SELECT MIN(id) FROM messages "
                    "          WHERE talker_id=? AND is_self=1 AND msg_key=0 AND content=?)",
                    (msg_key, seqno, ts, talker_id, content),
                )
                if cur.rowcount:
                    continue

            processed = 1 if (historical or is_self) else 0
            cur = conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(msg_key, talker_id, sender_uid, content, image_url, msg_seqno, timestamp, is_self, processed, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (msg_key, talker_id, sender, content, image_url, seqno, ts, is_self, processed, now),
            )
            if cur.rowcount:
                inserted += 1

        if max_seqno:
            conn.execute(
                "UPDATE sessions SET last_seqno=MAX(last_seqno,?), updated_at=? WHERE talker_id=?",
                (max_seqno, now, talker_id),
            )
    return inserted


def get_unprocessed(talker_id) -> list:
    """获取该会话所有待回复的用户消息（按时间正序）"""
    rows = get_conn().execute(
        "SELECT id, msg_key, content, image_url, timestamp FROM messages "
        "WHERE talker_id=? AND processed=0 ORDER BY id",
        (str(talker_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_processed(msg_id: int) -> None:
    """将一条消息标记为已处理"""
    with get_conn() as conn:
        conn.execute("UPDATE messages SET processed=1 WHERE id=?", (int(msg_id),))


def add_local_reply(talker_id, my_uid, content: str) -> None:
    """
    记录机器人本地发出的回复（msg_key=0，processed=1）。
    后续 API 回显该消息时会自动关联更新为真实 msg_key。
    """
    conn = get_conn()
    with conn:
        conn.execute(
            "INSERT INTO messages "
            "(msg_key, talker_id, sender_uid, content, msg_seqno, timestamp, is_self, processed, created_at) "
            "VALUES (0,?,?,?,0,?,1,1,?)",
            (str(talker_id), str(my_uid), content, int(time.time()), _now()),
        )


def get_chat_context(talker_id, limit: int = 50) -> list:
    """
    获取与指定用户的聊天上下文（按时间正序）
    返回: [{"role": "user"/"assistant", "content": "..."}, ...]
    """
    rows = get_conn().execute(
        "SELECT is_self, content FROM messages "
        "WHERE talker_id=? ORDER BY id DESC LIMIT ?",
        (str(talker_id), int(limit)),
    ).fetchall()
    return [
        {"role": "assistant" if r["is_self"] else "user", "content": r["content"]}
        for r in reversed(rows)
    ]


def get_chat_display(talker_id, limit: int = 100) -> list:
    """
    获取聊天记录用于界面展示（按时间正序）
    返回: [{"is_self": 0/1, "content": str, "image_url": str, "timestamp": int, "created_at": str}, ...]
    """
    rows = get_conn().execute(
        "SELECT is_self, content, image_url, timestamp, created_at FROM messages "
        "WHERE talker_id=? ORDER BY id DESC LIMIT ?",
        (str(talker_id), int(limit)),
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_overview() -> list:
    """
    获取所有私信会话总览（按最近活跃排序）
    返回: [{"talker_id", "username", "total", "pending", "last_content", "last_is_self", "last_ts"}, ...]
    """
    rows = get_conn().execute("""
        SELECT s.talker_id, s.username, s.updated_at,
               (SELECT COUNT(*) FROM messages m WHERE m.talker_id = s.talker_id) AS total,
               (SELECT COUNT(*) FROM messages m WHERE m.talker_id = s.talker_id AND processed = 0) AS pending,
               (SELECT m.content FROM messages m WHERE m.talker_id = s.talker_id ORDER BY m.id DESC LIMIT 1) AS last_content,
               (SELECT m.is_self FROM messages m WHERE m.talker_id = s.talker_id ORDER BY m.id DESC LIMIT 1) AS last_is_self,
               (SELECT m.timestamp FROM messages m WHERE m.talker_id = s.talker_id ORDER BY m.id DESC LIMIT 1) AS last_ts
        FROM sessions s
        ORDER BY COALESCE((SELECT MAX(id) FROM messages m WHERE m.talker_id = s.talker_id), 0) DESC
    """).fetchall()
    return [dict(r) for r in rows]


def count_replies_on_date(date_str: str = None) -> int:
    """统计某日机器人发出的私信回复数（按消息原始时间戳）"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    start = int(time.mktime(datetime.strptime(date_str, "%Y-%m-%d").timetuple()))
    row = get_conn().execute(
        "SELECT COUNT(*) FROM messages WHERE is_self=1 AND timestamp>=? AND timestamp<?",
        (start, start + 86400),
    ).fetchone()
    return row[0]


def migrate_memory_if_needed(memory: dict) -> bool:
    """
    将 memory.json 中的旧 private_chats 数据一次性迁移到数据库（仅当数据库为空时）。
    迁移后的消息全部标记 processed=1（历史上已处理过，不会重复回复）。
    幂等：按 msg_key 去重。
    返回: 是否执行了迁移
    """
    chats = memory.get("private_chats") or {}
    if not chats:
        return False
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] > 0:
        return False

    now = _now()
    with conn:
        for talker_id, chat in chats.items():
            talker_id = str(talker_id)
            username = str(chat.get("username", "") or "")
            last_seqno = int(chat.get("last_seqno", 0) or 0)
            conn.execute(
                "INSERT OR IGNORE INTO sessions "
                "(talker_id, username, last_seqno, created_at, updated_at) VALUES (?,?,?,?,?)",
                (talker_id, username, last_seqno, now, now),
            )
            for m in chat.get("chat_history", []):
                content = str(m.get("content", "") or "")
                if not content:
                    continue
                msg_key = int(m.get("msg_key", 0) or 0)
                is_self = 1 if m.get("role") == "assistant" else 0
                ts = 0
                try:
                    ts = int(time.mktime(
                        datetime.strptime(m.get("time", ""), "%Y-%m-%d %H:%M").timetuple()))
                except (ValueError, TypeError):
                    pass
                conn.execute(
                    "INSERT OR IGNORE INTO messages "
                    "(msg_key, talker_id, sender_uid, content, msg_seqno, timestamp, is_self, processed, created_at) "
                    "VALUES (?,?,?,?,0,?,?,1,?)",
                    (msg_key, talker_id, "", content, ts, is_self, now),
                )
            conn.execute(
                "UPDATE sessions SET last_seqno=MAX(last_seqno,?) WHERE talker_id=?",
                (last_seqno, talker_id),
            )
    return True
