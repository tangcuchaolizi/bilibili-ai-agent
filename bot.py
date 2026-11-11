"""
B站 AI 智能体 - 主程序
启动调度引擎，支持本地笔记本随开随用模式
"""
import asyncio
from datetime import datetime
from scheduler_engine import (
    create_scheduler,
    job_post_dynamic,
    job_check_and_interact,
    job_check_private_messages,
    print_stats,
    print_private_overview,
)


def print_banner():
    banner = """
=======================================
      B站 AI 智能体 - 本地笔记本模式
=======================================
  - 定时发布 AI 生成的日常动态
  - 自动回复评论区的新评论
  - 自动回复私信消息
  - 每日数据统计
  - 模型: DeepSeek (deepseek-v4-flash)
  - 调度: APScheduler
  - 存储: JSON 文件
=======================================
"""
    print(banner)


async def main():
    print_banner()

    print("[交互]")
    print("  1. 立即发一条动态（发完退出）")
    print("  2. 立即检查并回复评论（测试）")
    print("  3. 发一条动态 + 启动后台定时")
    print("  4. 立即检查并回复私信（测试）")
    print("  5. 查看今日统计数据")
    print("  6. 只启动后台定时任务")
    print("  7. 查看私信总览")
    print()

    try:
        choice = input("请选择 (1-7, 默认3): ").strip() or "3"
    except (EOFError, KeyboardInterrupt):
        choice = "3"

    scheduler = create_scheduler()

    if choice == "1":
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 正在生成动态...")
        await job_post_dynamic()
        print("\n[完成] 动态已发布，程序退出。")
        return

    elif choice == "2":
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 正在检查并回复评论（测试）...")
        await job_check_and_interact()
        print("\n[完成] 测试结束，程序退出。")
        return

    elif choice == "3":
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 正在生成动态...")
        await job_post_dynamic()
        print()
        scheduler.start()

    elif choice == "4":
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 正在检查并回复私信（测试）...")
        await job_check_private_messages()
        print("\n[完成] 测试结束，程序退出。")
        return

    elif choice == "5":
        print_stats()
        print("[完成] 统计数据已展示，程序退出。")
        return

    elif choice == "7":
        print_private_overview()
        print("[完成] 私信总览已展示，程序退出。")
        return

    elif choice == "6":
        print()
        scheduler.start()

    else:
        print("[错误] 无效选项，程序退出。")
        return

    print(f"[运行中] 后台调度器已启动。按 Ctrl+C 停止。\n")
    print("=" * 50)

    jobs = scheduler.get_jobs()
    print(f"今日共有 {len(jobs)} 个定时任务:")
    for job in jobs:
        next_run = job.next_run_time
        print(f"  - {job.name} | 下次执行: {next_run.strftime('%H:%M:%S') if next_run else '未安排'}")
    print("=" * 50)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[停止] 正在关闭...")
        scheduler.shutdown(wait=False)
        print("[停止] 已安全关闭。")


if __name__ == "__main__":
    asyncio.run(main())
