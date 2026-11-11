"""
获取指定动态的真正评论区 rid 和 comment_type
运行方式：python get_rid.py
"""
import asyncio
from bilibili_api import Credential
from bilibili_api.dynamic import Dynamic
import json

# 读取配置
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

bili = config["bilibili"]
credential = Credential(
    sessdata=bili["sessdata"],
    bili_jct=bili["bili_jct"],
    dedeuserid=bili["dedeuserid"],
)

async def main():
    # 从 memory.json 读取最新动态 ID
    with open("memory.json", "r", encoding="utf-8") as f:
        memory = json.load(f)

    if not memory["dynamic_history"]:
        print("memory.json 中没有动态记录")
        return

    latest = memory["dynamic_history"][-1]
    dyn_id_str = latest["dynamic_id"]
    old_rid = latest.get("dyn_rid", 0)

    print(f"动态 ID: {dyn_id_str}")
    print(f"旧 rid:  {old_rid}")

    dynamic = Dynamic(dynamic_id=int(dyn_id_str), credential=credential)
    info = await dynamic.get_info()
    basic = info.get("item", {}).get("basic", {})

    rid_str = basic.get("rid_str", "")
    comment_type = basic.get("comment_type", "")
    comment_id_str = basic.get("comment_id_str", "")

    print(f"\n动态详情基本信息:")
    print(f"  rid_str:        {rid_str}")
    print(f"  comment_type:   {comment_type}")
    print(f"  comment_id_str: {comment_id_str}")

    real_rid = await dynamic.get_rid()
    print(f"\nDynamic.get_rid() 返回: {real_rid}")

    # 尝试用不同的 type 获取评论
    from bilibili_api.comment import get_comments, CommentResourceType, OrderType

    types_to_test = [
        ("DYNAMIC (17)", CommentResourceType.DYNAMIC),
        ("DYNAMIC_DRAW (11)", CommentResourceType.DYNAMIC_DRAW),
        ("ARTICLE (12)", CommentResourceType.ARTICLE),
    ]

    for name, t in types_to_test:
        try:
            result = await get_comments(
                oid=real_rid,
                type_=t,
                page_index=1,
                order=OrderType.TIME,
                credential=credential,
            )
            replies = result.get("replies", [])
            print(f"\n  [{name}] 成功! 评论数: {len(replies)}")
            for r in replies[:3]:
                print(f"    - {r['member']['uname']}: {r['content']['message'][:30]}")
        except Exception as e:
            print(f"\n  [{name}] 失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())
