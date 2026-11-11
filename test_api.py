"""
API 层接口测试（FastAPI TestClient，外部调用全部 mock，无需真实网络）
运行: python -B test_api.py
"""
import json
import os
import sys
import shutil
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_tmpdir = tempfile.mkdtemp(prefix="api_test_")

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [通过] {name}")
    else:
        failed += 1
        print(f"  [失败] {name} {detail}")


# ---------- 测试环境：临时配置 + 临时数据库 ----------
TEST_CONFIG = {
    "bilibili": {"sessdata": "x", "bili_jct": "x", "dedeuserid": "999"},
    "deepseek": {"api_key": "sk-test", "model": "test-model", "base_url": "http://localhost"},
    "persona": {
        "name": "小星", "gender": "女", "age": "19岁", "city": "上海",
        "personality": "元气", "speaking_style": "口语化",
        "topics": ["日常"], "taboos": ["广告"], "fans_name": "粉丝",
    },
    "schedule": {"private_msg_context_limit": 50},
    "web_search": {"api_key": "", "enabled": True, "max_per_day": 30, "cache_minutes": 60},
}
with open(os.path.join(_tmpdir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(TEST_CONFIG, f, ensure_ascii=False)
with open(os.path.join(_tmpdir, "memory.json"), "w", encoding="utf-8") as f:
    json.dump({
        "dynamic_history": [],
        "replied_comments": [
            {"comment_rpid": "rp1", "reply_content": "谢谢支持", "time": "2026-10-14 08:00"}
        ],
        "private_chats": {},
    }, f, ensure_ascii=False)

import config_loader
import chat_store

config_loader.PROJECT_DIR = _tmpdir
chat_store.DB_PATH = os.path.join(_tmpdir, "chat.db")
chat_store._conn = None

# 预置聊天数据：1个会话，1条用户消息（待回复）+ 1条本账号回复
chat_store.sync_messages("888", "测试粉丝", [
    {"msg_key": 100, "msg_seqno": 1, "sender_uid": "888", "text": "在吗",
     "image_url": "", "timestamp": 1750000000, "msg_type": 1},
], "999", historical=False)
chat_store.add_local_reply("888", "999", "在的呀")

import api
import web_search
from fastapi.testclient import TestClient

client = TestClient(api.app, raise_server_exceptions=False)


# ---------- 假实现 ----------
class FakeJob:
    def __init__(self, jid, name):
        self.id = jid
        self.name = name
        self.next_run_time = None


class FakeScheduler:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.running = False

    def get_jobs(self):
        return [FakeJob("post_dynamic_08_30", "发动态 08:30")]


async def fake_post_dynamic():
    memory = config_loader.load_memory()
    config_loader.add_dynamic_record(memory, "dyn_001", "今天也是元气满满的一天", 12345, 17)


async def fake_noop():
    return None


async def fake_send_ok(receiver_id, text):
    return {"code": 0}


async def fake_send_err(receiver_id, text):
    return {"code": -111, "message": "csrf 校验失败"}


class FakeGenerator:
    def generate_dynamic(self):
        return "预览：周末去探店啦"


print("=" * 60)
print("一、系统端点")
print("=" * 60)

r = client.get("/")
check("GET / 返回简易控制面板", r.status_code == 200 and "控制面板" in r.text)

r = client.get("/docs")
check("GET /docs 返回文档页", r.status_code == 200 and "swagger" in r.text.lower())
check("文档页引用本地 swagger js", "/swagger-ui/swagger-ui-bundle.js" in r.text)
check("文档页引用本地 swagger css", "/swagger-ui/swagger-ui.css" in r.text)

js = client.get("/swagger-ui/swagger-ui-bundle.js")
check("本地 swagger js 文件可访问", js.status_code == 200 and len(js.text) > 10000)

css = client.get("/swagger-ui/swagger-ui.css")
check("本地 swagger css 文件可访问", css.status_code == 200 and len(css.text) > 1000)

r = client.get("/api/health")
d = r.json()
check("GET /api/health", r.status_code == 200 and d["code"] == 0 and d["data"]["status"] == "ok")

r = client.get("/api/config/persona")
d = r.json()
check("GET /api/config/persona 返回人设", r.status_code == 200 and d["data"]["name"] == "小星")
check("人设响应不含凭证", "sessdata" not in r.text and "api_key" not in r.text)

r = client.get("/api/stats/today")
d = r.json()
check("GET /api/stats/today 结构完整",
      r.status_code == 200 and set(d["data"].keys()) == {"date", "dynamics", "replies", "private_replies"})
check("今日私信回复=1（预置数据）", d["data"]["private_replies"] == 1, f"实际{d['data']['private_replies']}")

print()
print("=" * 60)
print("二、动态端点")
print("=" * 60)

r = client.get("/api/dynamics?limit=5")
check("GET /api/dynamics 初始为空", r.status_code == 200 and r.json()["data"] == [])

api.job_post_dynamic = fake_post_dynamic
r = client.post("/api/dynamics")
d = r.json()
check("POST /api/dynamics 发布成功", r.status_code == 200 and d["code"] == 0)
check("返回新动态记录", d["data"]["dynamic_id"] == "dyn_001" and d["data"]["content"] == "今天也是元气满满的一天")

r = client.get("/api/dynamics")
check("发布后列表有1条", len(r.json()["data"]) == 1)

api.job_post_dynamic = fake_noop  # 模拟发布失败（无新记录）
r = client.post("/api/dynamics")
check("发布失败返回502统一格式", r.status_code == 502 and r.json()["code"] == 502 and r.json()["data"] is None)

api.AIContentGenerator = lambda: FakeGenerator()
r = client.post("/api/dynamics/preview")
check("POST /api/dynamics/preview", r.status_code == 200 and r.json()["data"]["content"] == "预览：周末去探店啦")

print()
print("=" * 60)
print("三、评论端点")
print("=" * 60)

r = client.get("/api/comments/replied")
d = r.json()
check("GET /api/comments/replied", r.status_code == 200 and len(d["data"]) == 1 and d["data"][0]["comment_rpid"] == "rp1")

api.job_check_and_interact = fake_noop
r = client.post("/api/comments/check")
d = r.json()
check("POST /api/comments/check", r.status_code == 200 and d["data"]["job"] == "check_comments" and d["data"]["replies_delta"] == 0)

print()
print("=" * 60)
print("四、私信端点")
print("=" * 60)

r = client.get("/api/messages/sessions")
d = r.json()
check("GET /api/messages/sessions 返回1个会话", r.status_code == 200 and len(d["data"]) == 1)
check("会话含pending字段", d["data"][0]["pending"] == 1, f"实际{d['data'][0]}")

r = client.get("/api/messages/sessions/888?limit=10")
d = r.json()
check("GET 聊天记录返回2条", len(d["data"]) == 2, f"实际{len(d['data'])}")
check("消息含is_self字段", d["data"][1]["is_self"] == 1)

r = client.get("/api/messages/sessions/888/pending")
d = r.json()
check("GET 待回复消息", len(d["data"]) == 1 and d["data"][0]["content"] == "在吗")

api.send_private_msg = fake_send_ok
r = client.post("/api/messages/sessions/888/messages", json={"content": "手动问好"})
d = r.json()
check("POST 发送私信成功", r.status_code == 200 and d["data"]["content"] == "手动问好")
r = client.get("/api/messages/sessions/888?limit=10")
check("手动发送已入聊天记录", r.json()["data"][-1]["content"] == "手动问好")

api.send_private_msg = fake_send_err
r = client.post("/api/messages/sessions/888/messages", json={"content": "会失败"})
check("B站接口错误返回502", r.status_code == 502 and "csrf" in r.json()["message"])

r = client.post("/api/messages/sessions/888/messages", json={"content": ""})
check("空内容返回422统一格式", r.status_code == 422 and r.json()["code"] == 422)

r = client.post("/api/messages/sessions/888/messages", json={"content": "x" * 501})
check("超长内容返回422", r.status_code == 422)

api.job_check_private_messages = fake_noop
r = client.post("/api/messages/check")
check("POST /api/messages/check", r.status_code == 200 and r.json()["data"]["job"] == "check_private_messages")

print()
print("=" * 60)
print("五、搜索端点")
print("=" * 60)

web_search.WebSearcher.search = lambda self, q, count=5: "1. 标题: 摘要"
r = client.post("/api/search", json={"query": "上海天气", "count": 3})
d = r.json()
check("POST /api/search 成功", r.status_code == 200 and d["data"]["found"] is True and "标题" in d["data"]["result"])

web_search.WebSearcher.search = lambda self, q, count=5: ""
r = client.post("/api/search", json={"query": "无结果查询"})
check("搜索无结果 found=false", r.status_code == 200 and r.json()["data"]["found"] is False)

r = client.post("/api/search", json={"query": ""})
check("空查询返回422", r.status_code == 422)

r = client.post("/api/search", json={"query": "ok", "count": 99})
check("count超限返回422", r.status_code == 422)

print()
print("=" * 60)
print("六、调度器端点")
print("=" * 60)

r = client.get("/api/scheduler/status")
check("初始状态未运行", r.json()["data"]["running"] is False and r.json()["data"]["jobs"] == [])

api.create_scheduler = lambda: FakeScheduler()
r = client.post("/api/scheduler/start")
d = r.json()
check("启动调度器", r.status_code == 200 and d["data"]["detail"] == "调度器已启动")

r = client.get("/api/scheduler/status")
d = r.json()
check("运行中且含任务", d["data"]["running"] is True and len(d["data"]["jobs"]) == 1)

r = client.post("/api/scheduler/start")
check("重复启动幂等", "已在运行" in r.json()["data"]["detail"])

r = client.post("/api/scheduler/stop")
check("停止调度器", r.json()["data"]["detail"] == "调度器已停止")

r = client.get("/api/scheduler/status")
check("停止后未运行", r.json()["data"]["running"] is False)

print()
print("=" * 60)
print("七、通用行为")
print("=" * 60)

r = client.get("/api/not-exist")
check("未知路径404统一格式", r.status_code == 404 and r.json()["code"] == 404 and "data" in r.json())

r = client.options("/api/health", headers={
    "Origin": "http://localhost:3000",
    "Access-Control-Request-Method": "GET",
})
check("CORS预检通过", r.status_code == 200 and "access-control-allow-origin" in r.headers)

r = client.get("/docs")
check("Swagger 文档页可访问", r.status_code == 200 and "swagger" in r.text.lower())

r = client.get("/openapi.json")
spec = r.json()
check("OpenAPI 版本为 3.0.x（兼容离线 Swagger UI）", spec.get("openapi", "").startswith("3.0."))
paths = spec["paths"]
check("OpenAPI包含全部端点", all(p in paths for p in [
    "/api/health", "/api/dynamics", "/api/dynamics/preview", "/api/comments/check",
    "/api/messages/sessions", "/api/messages/sessions/{talker_id}",
    "/api/messages/sessions/{talker_id}/messages", "/api/messages/sessions/{talker_id}/pending",
    "/api/messages/check", "/api/search", "/api/scheduler/status",
    "/api/scheduler/start", "/api/scheduler/stop", "/api/stats/today", "/api/config/persona",
]), f"缺失: {[p for p in ['/api/search','/api/scheduler/status'] if p not in paths]}")

check("OpenAPI含分组标签", "私信" in [t["name"] for t in spec.get("tags", [])])

print()
print("=" * 60)
print(f"测试结果: 通过 {passed} 项, 失败 {failed} 项")
print("=" * 60)

shutil.rmtree(_tmpdir, ignore_errors=True)
sys.exit(1 if failed else 0)
