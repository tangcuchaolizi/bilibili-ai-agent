"""
调度引擎模块
使用 APScheduler 定时触发发动态、检查评论回复和私信回复
"""
import random
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from config_loader import (
    load_config,
    load_memory,
    save_memory,
    add_dynamic_record,
    is_comment_replied,
    add_reply_record,
    get_recent_dynamics,
    get_private_chat,
    update_private_chat_username,
    add_chat_message,
    is_private_msg_replied,
    add_private_reply_record,
    get_chat_context,
    update_chat_last_seqno,
    sync_chat_messages_from_api,
    is_comment_liked,
    add_like_record,
    get_daily_stats,
)
from ai_generator import AIContentGenerator
from bilibili_client import (
    post_dynamic,
    fetch_dynamic_comment_info,
    get_comments_by_rid,
    reply_to_comment,
    like_comment,
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
        minute_offset = random.randint(-5, 10)
        actual_minute = int(minute) + minute_offset
        if actual_minute < 0:
            actual_minute += 60
            hour = str(int(hour) - 1).zfill(2)
        elif actual_minute >= 60:
            actual_minute -= 60
            hour = str(int(hour) + 1).zfill(2)
        hour_int = int(hour) % 24

        scheduler.add_job(
            job_post_dynamic,
            CronTrigger(hour=hour_int, minute=actual_minute),
            id=f"post_dynamic_{hour}_{minute}",
            name=f"发动态 {time_str}",
            misfire_grace_time=300,
        )
        print(f"[调度] 发动态任务: 约 {hour}:{actual_minute:02d} (基准 {time_str}, 偏移{minute_offset:+d}分)")

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
    """检查评论并回复、点赞"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始检查评论...")
    try:
        config = load_config()
        memory = load_memory()
        reply_prob = config["schedule"]["reply_probability"]
        like_prob = config["schedule"]["like_probability"]
        my_uid = str(config["bilibili"].get("dedeuserid", ""))

        # 获取最近动态（最多5条）
        recent_dynamics = get_recent_dynamics(memory, limit=5)

        if not recent_dynamics:
            print("  暂无历史动态记录，跳过")
            return

        print(f"  检查最近 {len(recent_dynamics)} 条动态的评论区...")

        generator = AIContentGenerator()
        total_new = 0
        total_liked = 0

        for dyn in recent_dynamics:
            dyn_id_str = dyn["dynamic_id"]
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

                is_new = False
                is_replied = is_comment_replied(memory, rpid)
                is_liked_flag = is_comment_liked(memory, rpid)

                if not is_replied or not is_liked_flag:
                    total_new += 1
                    is_new = True
                    print(f"    [新][{user}](rpid:{rpid}): {text[:50]}")
                else:
                    print(f"    [{user}](rpid:{rpid}): {text[:30]}... (已处理，跳过)")
                    continue

                # 模拟真人延迟
                delay = random.randint(3, 15)
                print(f"    等待 {delay} 秒...")
                await asyncio.sleep(delay)

                # 概率点赞
                if not is_liked_flag and random.random() < like_prob:
                    try:
                        result = await like_comment(int(rpid), dyn_oid, comment_type, like=True)
                        if isinstance(result, dict) and result.get("code", 0) != 0:
                            raise Exception(f"接口返回错误代码：{result.get('code')}, 信息：{result.get('message')}")
                        add_like_record(memory, rpid)
                        memory = load_memory()
                        total_liked += 1
                        print(f"    点赞成功!")
                    except Exception as e:
                        print(f"    [错误] 点赞失败: {e}")

                # 概率回复
                if not is_replied and random.random() < reply_prob:
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
                elif not is_replied:
                    print(f"    跳过回复（随机）")

        if total_new == 0:
            print("  没有发现新的未处理评论")
        else:
            print(f"  共发现 {total_new} 条新评论，点赞 {total_liked} 条，处理完毕")

    except Exception as e:
        print(f"  [错误] 评论检查异常: {e}")


async def job_check_private_messages():
    """检查私信并回复"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 开始检查私信...")
    try:
        config = load_config()
        memory = load_memory()
        reply_prob = config["schedule"].get("private_msg_reply_probability", 1.0)
        my_uid = str(config["bilibili"].get("dedeuserid", ""))

        sessions = await get_private_sessions()
        if not sessions:
            print("  暂无私信会话")
            return

        print(f"  共有 {len(sessions)} 个私信会话")

        generator = AIContentGenerator()
        total_new = 0

        for session in sessions:
            talker_id = str(session["talker_id"])
            uname = session.get("uname", "")
            max_seqno = session.get("max_seqno", 0)

            update_private_chat_username(memory, talker_id, uname)

            chat_data = get_private_chat(memory, talker_id)
            last_seqno = chat_data.get("last_seqno", 0)

            if max_seqno <= last_seqno:
                print(f"  [{uname}] 无新消息")
                continue

            print(f"  [{uname}] 有新消息，获取聊天记录...")
            messages = await get_chat_history(int(talker_id), last_seqno)

            if not messages:
                update_chat_last_seqno(memory, talker_id, max_seqno)
                continue

            # 先把所有获取到的消息同步到memory（关键修复：确保AI有完整上下文）
            sync_chat_messages_from_api(memory, talker_id, messages, my_uid)
            memory = load_memory()

            new_messages = []
            for msg in messages:
                sender_uid = str(msg.get("sender_uid", 0))
                if sender_uid == my_uid:
                    continue
                msg_key = msg.get("msg_key", 0)
                if is_private_msg_replied(memory, talker_id, msg_key):
                    continue
                new_messages.append(msg)

            if not new_messages:
                update_chat_last_seqno(memory, talker_id, max_seqno)
                print(f"  [{uname}] 没有需要回复的新消息")
                continue

            total_new += len(new_messages)
            print(f"  [{uname}] 发现 {len(new_messages)} 条新私信")

            for msg in new_messages:
                msg_key = msg.get("msg_key", 0)
                text = msg.get("text", "")
                if not text:
                    continue

                print(f"    [新][{uname}](msg_key:{msg_key}): {text[:50]}")

                delay = random.randint(3, 15)
                print(f"    等待 {delay} 秒...")
                await asyncio.sleep(delay)

                if random.random() < reply_prob:
                    try:
                        chat_context = get_chat_context(memory, talker_id, limit=8)
                        reply_text = generator.generate_private_reply(text, uname, chat_context)
                        print(f"    回复: {reply_text}")

                        result = await send_private_msg(int(talker_id), reply_text)
                        if isinstance(result, dict) and result.get("code", 0) != 0:
                            raise Exception(f"接口返回错误代码：{result.get('code')}, 信息：{result.get('message')}")

                        add_private_reply_record(memory, talker_id, msg_key, reply_text)
                        memory = load_memory()

                        print(f"    回复成功!")
                    except Exception as e:
                        print(f"    [错误] 回复失败: {e}")
                else:
                    print(f"    跳过回复（随机）")
                    # 跳过回复也记录msg_key，避免重复处理同一条消息
                    chat = get_private_chat(memory, talker_id)
                    chat.setdefault("replied_msg_keys", []).append(msg_key)
                    chat["replied_msg_keys"] = chat["replied_msg_keys"][-100:]
                    save_memory(memory)

            update_chat_last_seqno(memory, talker_id, max_seqno)

        if total_new == 0:
            print("  没有发现新的未回复私信")
        else:
            print(f"  共发现 {total_new} 条新私信，处理完毕")

    except Exception as e:
        print(f"  [错误] 私信检查异常: {e}")


def get_today_stats():
    """获取今日统计数据"""
    memory = load_memory()
    today = datetime.now().strftime("%Y-%m-%d")
    stats = get_daily_stats(memory, today)
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
    print(f"  👍 评论点赞: {stats['likes']} 条")
    print(f"  💌 私信回复: {stats['private_replies']} 条")
    print(f"{'='*50}\n")