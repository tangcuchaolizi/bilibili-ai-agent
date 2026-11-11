"""
调度引擎模块
使用 APScheduler 定时触发发动态、检查评论回复和私信回复
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from config_loader import (
    load_config,
    load_memory,
    add_dynamic_record,
    is_comment_replied,
    add_reply_record,
    get_recent_dynamics,
    get_daily_stats,
)
from chat_store import (
    get_session,
    sync_messages,
    get_unprocessed,
    get_chat_context,
    add_local_reply,
    mark_processed,
    migrate_memory_if_needed,
    count_replies_on_date,
    get_overview,
)
from ai_generator import AIContentGenerator
from bilibili_client import (
    post_dynamic,
    fetch_dynamic_comment_info,
    get_comments_by_rid,
    reply_to_comment,
    get_private_sessions,
    get_chat_history,
    send_private_msg,
)

def create_scheduler() -> AsyncIOScheduler:
    """创建并配置调度器（尚未启动）"""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    config = load_config()
    schedule = config["schedule"]

    # ---- 定时发动态 ----
    for time_str in schedule["post_times"]:
        hour, minute = time_str.split(":")
        scheduler.add_job(
            job_post_dynamic,
            CronTrigger(hour=int(hour), minute=int(minute)),
            id=f"post_dynamic_{hour}_{minute}",
            name=f"发动态 {time_str}",
            misfire_grace_time=300,
        )
        print(f"[调度] 发动态任务: {time_str}")

    # ---- 定时检查评论 ----
    check_interval = schedule["comment_check_interval_minutes"]
    scheduler.add_job(
        job_check_and_interact,
        "interval",
        minutes=check_interval,
        id="check_comments",
        name=f"检查评论(每{check_interval}分钟)",
    )
    print(f"[调度] 评论检查: 每 {check_interval} 分钟")

    # ---- 定时检查私信 ----
    pm_check_interval = schedule.get("private_msg_check_interval_minutes", 5)
    scheduler.add_job(
        job_check_private_messages,
        "interval",
        minutes=pm_check_interval,
        id="check_private_messages",
        name=f"检查私信(每{pm_check_interval}分钟)",
    )
    print(f"[调度] 私信检查: 每 {pm_check_interval} 分钟")

    return scheduler

async def job_post_dynamic():
    """发动态任务"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始生成动态...")
    try:
        generator = AIContentGenerator()
        content = generator.generate_dynamic()
        print(f"  生成内容: {content}")

        result = await post_dynamic(content)
        print(f"  API返回: {result}")

        # 从API返回第一层提取 dyn_id_str
        dyn_id_str = "unknown"
        if isinstance(result, dict):
            dyn_id_str = str(result.get("dyn_id_str", result.get("dyn_id", "unknown")))

        # 通过 Dynamic.get_info() 获取评论区真正的 rid 和 comment_type
        try:
            dyn_rid, comment_type = await fetch_dynamic_comment_info(dyn_id_str)
        except Exception as e:
            print(f"  [警告] 获取评论区信息失败: {e}")
            dyn_rid = 0
            comment_type = 17

        memory = load_memory()
        add_dynamic_record(memory, dyn_id_str, content, dyn_rid, comment_type)

        print(f"  动态发布成功! ID: {dyn_id_str}, RID: {dyn_rid}, comment_type: {comment_type}")

    except Exception as e:
        print(f"  [错误] 动态发布失败: {e}")

async def job_check_and_interact():
    """检查评论并回复"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始检查评论...")
    try:
        config = load_config()
        memory = load_memory()
        my_uid = str(config["bilibili"].get("dedeuserid", ""))

        # 获取最近动态（最多5条）
        recent_dynamics = get_recent_dynamics(memory, limit=5)

        if not recent_dynamics:
            print("  暂无历史动态记录，跳过")
            return

        print(f"  检查最近 {len(recent_dynamics)} 条动态的评论区...")

        generator = AIContentGenerator()
        total_new = 0

        for dyn in recent_dynamics:
            dyn_rid = dyn["dyn_rid"]
            comment_type = dyn.get("comment_type", 17)

            dyn_oid = int(dyn_rid)
            comments = await get_comments_by_rid(dyn_oid, comment_type)

            if not comments:
                print(f"  动态 [RID:{dyn_rid}] 暂无评论")
                continue

            print(f"  动态 [RID:{dyn_rid}] 有 {len(comments)} 条评论")

            for comment in comments:
                rpid = str(comment["rpid"])
                text = comment["text"]
                user = comment["user"]
                root_rpid = comment["root"]
                mid = str(comment.get("mid", ""))

                # 跳过自己发的评论
                if mid == my_uid:
                    continue

                if is_comment_replied(memory, rpid):
                    print(f"    [{user}](rpid:{rpid}): {text[:30]}... (已处理，跳过)")
                    continue

                total_new += 1
                print(f"    [新][{user}](rpid:{rpid}): {text[:50]}")

                # 回复评论
                try:
                    reply_text = generator.generate_reply(text, user, dyn.get("content", ""))
                    print(f"    回复: {reply_text}")

                    result = await reply_to_comment(dyn_oid, int(root_rpid), reply_text, comment_type)
                    if isinstance(result, dict) and result.get("code", 0) != 0:
                        raise Exception(f"接口返回错误代码：{result.get('code')}, 信息：{result.get('message')}")

                    add_reply_record(memory, rpid, reply_text)
                    memory = load_memory()

                    print(f"    回复成功!")
                except Exception as e:
                    print(f"    [错误] 回复失败: {e}")

        if total_new == 0:
            print("  没有发现新的未处理评论")
        else:
            print(f"  共发现 {total_new} 条新评论，处理完毕")

    except Exception as e:
        print(f"  [错误] 评论检查异常: {e}")


async def reply_pending_private_messages(talker_id, uname: str, my_uid: str, context_limit: int = 50) -> int:
    """
    处理单个会话的所有待回复私信（生成回复 → 发送 → 标记已处理）。
    返回成功回复的条数。供定时任务和 GUI 聊天面板复用。
    """
    pending = get_unprocessed(talker_id)
    if not pending:
        return 0

    print(f"  [{uname}] 有 {len(pending)} 条待回复私信")
    generator = AIContentGenerator()
    replied = 0

    for msg in pending:
        content = msg["content"]
        image_url = msg.get("image_url") or ""
        display = "[图片]" if image_url else content[:50]
        print(f"    [新][{uname}](id:{msg['id']}): {display}")

        try:
            chat_context = get_chat_context(talker_id, limit=context_limit)
            reply_text = generator.generate_private_reply(content, uname, chat_context, image_url=image_url or None)
            print(f"    回复: {reply_text}")

            result = await send_private_msg(int(talker_id), reply_text)
            if isinstance(result, dict) and result.get("code", 0) != 0:
                raise Exception(f"接口返回错误代码：{result.get('code')}, 信息：{result.get('message')}")

            add_local_reply(talker_id, my_uid, reply_text)
            mark_processed(msg["id"])
            replied += 1

            print(f"    回复成功!")
        except Exception as e:
            print(f"    [错误] 回复失败: {e}（下轮自动重试）")

    return replied


async def job_check_private_messages():
    """检查私信并回复（消息状态存储在 SQLite：chat_history.db）"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始检查私信...")
    try:
        config = load_config()
        memory = load_memory()
        my_uid = str(config["bilibili"].get("dedeuserid", ""))
        context_limit = int(config["schedule"].get("private_msg_context_limit", 50))

        # 旧版 memory.json 私信数据一次性迁移到数据库
        if migrate_memory_if_needed(memory):
            print("  [迁移] 已将 memory.json 中的私信记录导入数据库（历史消息不会重复回复）")

        sessions = await get_private_sessions()
        if not sessions:
            print("  暂无私信会话")
            return

        print(f"  共有 {len(sessions)} 个私信会话")

        total_new = 0

        for session in sessions:
            talker_id = str(session["talker_id"])
            uname = session.get("uname", "") or talker_id
            max_seqno = int(session.get("max_seqno", 0) or 0)

            existing = get_session(talker_id)

            if existing is None:
                # 首次跟踪该会话：历史消息全部入库并标记已处理，不回复
                messages = await get_chat_history(int(talker_id), 0)
                inserted = sync_messages(talker_id, uname, messages, my_uid, historical=True)
                print(f"  [{uname}] 首次跟踪会话，{inserted} 条历史消息已入库（不回复历史消息）")
                continue

            last_seqno = existing["last_seqno"]
            if max_seqno > last_seqno:
                messages = await get_chat_history(int(talker_id), last_seqno)
                if messages:
                    sync_messages(talker_id, uname, messages, my_uid, historical=False)

            total_new += await reply_pending_private_messages(talker_id, uname, my_uid, context_limit)

        if total_new == 0:
            print("  没有发现新的未回复私信")
        else:
            print(f"  共回复 {total_new} 条新私信，处理完毕")

    except Exception as e:
        print(f"  [错误] 私信检查异常: {e}")


def get_today_stats():
    """获取今日统计数据"""
    memory = load_memory()
    today = datetime.now().strftime("%Y-%m-%d")
    stats = get_daily_stats(memory, today)
    stats["private_replies"] = count_replies_on_date(today)
    return stats


def print_stats():
    """打印今日统计数据"""
    stats = get_today_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*50}")
    print(f"  今日统计 ({today})")
    print(f"{'='*50}")
    print(f"  📝 动态发布: {stats['dynamics']} 条")
    print(f"  💬 评论回复: {stats['replies']} 条")
    print(f"  💌 私信回复: {stats['private_replies']} 条")
    print(f"{'='*50}\n")


def print_private_overview():
    """打印私信会话总览（数据来自 SQLite）"""
    overview = get_overview()
    if not overview:
        print("\n[私信总览] 暂无任何私信记录")
        return
    print(f"\n{'='*60}")
    print(f"  私信总览（共 {len(overview)} 个会话，按最近活跃排序）")
    print(f"{'='*60}")
    for s in overview:
        uname = s["username"] or s["talker_id"]
        pending = f"，待回复 {s['pending']} 条" if s["pending"] else ""
        last = (s["last_content"] or "（无消息）").replace("\n", " ")
        if len(last) > 30:
            last = last[:30] + "..."
        who = "我" if s["last_is_self"] else "对方"
        ts = s["last_ts"]
        if ts:
            tstr = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        else:
            tstr = (s["updated_at"] or "")[5:16]
        print(f"  [{uname}] 共 {s['total']} 条消息{pending}")
        print(f"      最近: {who}: {last}  ({tstr})")
    print(f"{'='*60}\n")