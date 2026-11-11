import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bilibili_api import Credential
from bilibili_api.comment import get_comments, CommentResourceType, OrderType
from config_loader import load_config

config = load_config()
bili = config["bilibili"]
credential = Credential(
    sessdata=bili["sessdata"],
    bili_jct=bili["bili_jct"],
    dedeuserid=bili["dedeuserid"],
)

dyn_rid = 400548505

async def test():
    print("=== CommentResourceType 枚举值 ===")
    for e in CommentResourceType:
        print(f"  {e.name} = {e.value}")
    
    print(f"\n=== 测试 RID={dyn_rid} ===")
    
    test_types = [
        ("DYNAMIC_DRAW(11)", CommentResourceType.DYNAMIC_DRAW),
        ("DYNAMIC(17)", CommentResourceType.DYNAMIC),
        ("ARTICLE(12)", CommentResourceType.ARTICLE),
    ]
    
    for name, type_val in test_types:
        print(f"\n--- 测试 type={name} ---")
        try:
            data = await get_comments(
                oid=dyn_rid,
                type_=type_val,
                page_index=1,
                order=OrderType.TIME,
                credential=credential,
            )
            replies = data.get("replies")
            print(f"  replies类型: {type(replies)}, 值: {replies}")
            if replies:
                print(f"  评论数量: {len(replies)}")
                for r in replies[:2]:
                    print(f"    - {r['member']['uname']}: {r['content']['message'][:30]}")
        except Exception as e:
            print(f"  错误: {e}")

asyncio.run(test())
