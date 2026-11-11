"""
B站交互模块
封装发动态、获取评论、回复评论、私信等操作
"""
import asyncio
from bilibili_api import Credential
from bilibili_api.dynamic import BuildDynamic, send_dynamic, Dynamic
from bilibili_api.comment import (
    get_comments,
    get_comments_lazy,
    send_comment,
    CommentResourceType,
    OrderType,
)
from bilibili_api.session import (
    get_sessions,
    fetch_session_msgs,
    send_msg,
    EventType,
)
from config_loader import load_config

def get_credential() -> Credential:
    """从配置文件创建B站认证"""
    config = load_config()
    bili = config["bilibili"]
    return Credential(
        sessdata=bili["sessdata"],
        bili_jct=bili["bili_jct"],
        dedeuserid=bili["dedeuserid"],
    )

async def post_dynamic(content: str) -> dict:
    """
    发布一条纯文本动态
    返回API响应（包含 dyn_id_str, dyn_rid 等）
    """
    credential = get_credential()
    dyn = BuildDynamic.create_by_args(text=content)
    result = await send_dynamic(dyn, credential)
    return result

async def fetch_dynamic_comment_info(dyn_id_str: str) -> tuple:
    """
    通过动态 ID 获取评论区信息
    返回: (rid, comment_type_value)
    rid: 评论区 oid
    comment_type_value: 评论类型值 (11=DYNAMIC_DRAW 图文, 17=DYNAMIC 文字)
    注意：API 返回的 dyn_rid 字段不等于 rid，必须通过 Dynamic.get_info() 获取
    """
    credential = get_credential()
    dynamic = Dynamic(dynamic_id=int(dyn_id_str), credential=credential)
    info = await dynamic.get_info()
    basic = info.get("item", {}).get("basic", {})
    rid = int(basic.get("rid_str", 0))
    comment_type = int(basic.get("comment_type", 17))
    return rid, comment_type

def get_comment_resource_type(comment_type_value: int) -> CommentResourceType:
    """根据 comment_type 值返回对应的 CommentResourceType"""
    if comment_type_value == 11:
        return CommentResourceType.DYNAMIC_DRAW
    elif comment_type_value == 12:
        return CommentResourceType.ARTICLE
    else:
        return CommentResourceType.DYNAMIC

async def get_comments_by_rid(dyn_rid: int, comment_type: int = 17) -> list:
    """
    通过动态 RID 获取动态下的所有一级评论
    dyn_rid: 动态 RID（评论区 oid）
    comment_type: 评论类型值 (11=DYNAMIC_DRAW, 17=DYNAMIC, 12=ARTICLE)
    返回: [{"rpid": int, "root": int, "text": str, "user": str}, ...]
    """
    credential = get_credential()
    try:
        comments_data = await get_comments_lazy(
            oid=dyn_rid,
            type_=get_comment_resource_type(comment_type),
            offset="",
            order=OrderType.TIME,
            credential=credential,
        )
        replies = comments_data.get("replies") or []
        result = []
        for reply in replies:
            root = reply.get("root", 0)
            if root == 0:
                root = reply["rpid"]
            result.append({
                "rpid": reply["rpid"],
                "root": root,
                "mid": reply["member"]["mid"],
                "text": reply["content"]["message"],
                "user": reply["member"]["uname"],
                "like": reply.get("like", 0),
            })
        return result
    except Exception as e:
        print(f"  [警告] 获取评论失败: {e}")
        return []

async def reply_to_comment(dyn_rid: int, root_rpid: int, text: str, comment_type: int = 17) -> dict:
    """
    回复一条评论
    dyn_rid: 动态 RID（评论区 oid）
    root_rpid: 要回复的评论的 rpid
    text: 回复内容
    comment_type: 评论类型值 (11=DYNAMIC_DRAW, 17=DYNAMIC, 12=ARTICLE)
    """
    credential = get_credential()
    result = await send_comment(
        text=text,
        oid=dyn_rid,
        type_=get_comment_resource_type(comment_type),
        root=root_rpid,
        parent=root_rpid,
        credential=credential,
    )
    return result


async def get_private_sessions() -> list:
    """
    获取私信会话列表
    返回: [{"talker_id": int, "uname": str, "last_msg": str, "max_seqno": int, "unread_count": int}, ...]
    """
    credential = get_credential()
    try:
        result = await get_sessions(credential, session_type=1)
        session_list = result.get("session_list", [])
        sessions = []
        for s in session_list:
            last_msg = ""
            last_msg_data = s.get("last_msg", {})
            if isinstance(last_msg_data, dict):
                content = last_msg_data.get("content", "")
                try:
                    import json
                    content_obj = json.loads(content)
                    last_msg = content_obj.get("content", content)
                except:
                    last_msg = content
            sessions.append({
                "talker_id": s.get("talker_id", 0),
                "uname": s.get("uname", ""),
                "last_msg": last_msg,
                "max_seqno": s.get("max_seqno", 0),
                "unread_count": s.get("unread_count", 0),
                "session_ts": s.get("session_ts", 0),
            })
        return sessions
    except Exception as e:
        print(f"  [警告] 获取私信会话列表失败: {e}")
        return []


async def get_chat_history(talker_id: int, begin_seqno: int = 0) -> list:
    """
    获取与指定用户的聊天历史
    返回: [{"msg_key": int, "msg_seqno": int, "sender_uid": int, "receiver_id": int,
            "text": str, "image_url": str, "timestamp": int, "msg_type": int}, ...]
    msg_type: 1=文字, 2=图片（此时 image_url 为图片地址，text 为 "[图片]"）
    """
    credential = get_credential()
    try:
        result = await fetch_session_msgs(
            talker_id=talker_id,
            credential=credential,
            session_type=1,
            begin_seqno=begin_seqno,
        )
        messages = result.get("messages", [])
        chat_list = []
        import json
        for msg in messages:
            content_str = msg.get("content", "")
            text = ""
            image_url = ""
            msg_type = msg.get("msg_type", 0)
            try:
                content_obj = json.loads(content_str)
            except (json.JSONDecodeError, TypeError):
                content_obj = {}
            if msg_type == 2:
                # 图片消息：content 为 {"url": ..., "width": ..., "height": ...}
                image_url = content_obj.get("url", "") if isinstance(content_obj, dict) else ""
                text = "[图片]"
            elif msg_type == 1:
                # 文字消息
                text = content_obj.get("content", content_str) if isinstance(content_obj, dict) else content_str
            # 其他类型（撤回/表情卡片等）：text 为空，仅用于推进 seqno
            chat_list.append({
                "msg_key": msg.get("msg_key", 0),
                "msg_seqno": msg.get("msg_seqno", 0),
                "sender_uid": msg.get("sender_uid", 0),
                "receiver_id": msg.get("receiver_id", 0),
                "text": text,
                "image_url": image_url,
                "timestamp": msg.get("timestamp", 0),
                "msg_type": msg_type,
            })
        return chat_list
    except Exception as e:
        print(f"  [警告] 获取聊天历史失败 (talker_id={talker_id}): {e}")
        return []


async def send_private_msg(receiver_id: int, text: str) -> dict:
    """
    给指定用户发送私信
    receiver_id: 接收者 UID
    text: 消息内容
    返回: API 响应
    """
    credential = get_credential()
    result = await send_msg(
        credential=credential,
        receiver_id=receiver_id,
        msg_type=EventType.TEXT,
        content=text,
    )
    return result