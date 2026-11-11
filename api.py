"""
FastAPI API 服务层
对现有项目功能的 RESTful 封装——只编排调用，不修改任何核心业务逻辑

启动:
    uvicorn api:app --host 127.0.0.1 --port 8000
或:
    python api.py
    start.bat api
文档:
    http://127.0.0.1:8000/docs (Swagger UI)
"""
import time
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from swagger_ui_bundle import swagger_ui_path

from api_models import (
    ApiResponse,
    ChatMessageItem,
    DynamicPreviewData,
    DynamicPublishData,
    DynamicRecord,
    HealthData,
    JobInfo,
    JobRunData,
    PendingMessageItem,
    PersonaData,
    RepliedCommentRecord,
    SchedulerStatusData,
    SearchData,
    SearchRequest,
    SendMessageData,
    SendMessageRequest,
    SessionOverview,
    StatsData,
)
from ai_generator import AIContentGenerator
from bilibili_client import send_private_msg
from chat_store import (
    add_local_reply,
    count_replies_on_date,
    get_chat_display,
    get_overview,
    get_unprocessed,
)
from config_loader import get_recent_dynamics, load_config, load_memory
from scheduler_engine import (
    create_scheduler,
    get_today_stats,
    job_check_and_interact,
    job_check_private_messages,
    job_post_dynamic,
)
from web_search import WebSearcher

API_LOG_FILE = "api.log"

# 调度器实例（模块级单例，由 /api/scheduler/* 端点管理）
_scheduler = None

app = FastAPI(
    title="B站 AI 智能体 API",
    description=(
        "对现有 B站 AI 智能体项目的 RESTful 封装。"
        "所有响应遵循统一格式：{code, message, data}，code=0 表示成功。"
        "注意：涉及 B站/DeepSeek 的操作端点（发布、检查回复等）为同步执行，"
        "可能需要数秒至数十秒，请耐心等待。"
    ),
    version="1.0.0",
    # 文档页使用本地静态资源（不依赖 CDN，离线可用）
    docs_url=None,
    redoc_url=None,
    openapi_tags=[
        {"name": "系统", "description": "健康检查、人设配置、每日统计"},
        {"name": "动态", "description": "AI 动态发布与记录查询"},
        {"name": "评论", "description": "评论检查回复与历史记录"},
        {"name": "私信", "description": "私信会话、聊天记录、发送与检查"},
        {"name": "搜索", "description": "联网搜索（必应RSS/博查）"},
        {"name": "调度器", "description": "后台定时任务管理"},
    ],
)


def ok(data) -> ApiResponse:
    """统一成功响应"""
    return ApiResponse(code=0, message="ok", data=data)


# ==================== 离线文档页（Swagger UI 本地资源） ====================

app.mount("/swagger-ui", StaticFiles(directory=swagger_ui_path), name="swagger-ui")


def _custom_openapi():
    """离线打包的 Swagger UI 只支持 OpenAPI 3.0，将文档降级为 3.0.2"""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
        openapi_version="3.0.2",
    )
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/docs", include_in_schema=False, summary="接口文档（本地资源版）")
async def swagger_ui_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app.title + " - Swagger UI",
        swagger_js_url="/swagger-ui/swagger-ui-bundle.js",
        swagger_css_url="/swagger-ui/swagger-ui.css",
        swagger_favicon_url="/swagger-ui/favicon-32x32.png",
    )


# ==================== 中间件 ====================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # 本地工具，放开跨域
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """请求日志：打印到控制台并追加到 api.log"""
    start = time.time()
    response = await call_next(request)
    ms = (time.time() - start) * 1000
    line = (f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
            f"{request.method} {request.url.path} -> {response.status_code} ({ms:.1f}ms)")
    print(line)
    try:
        with open(API_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return response


# ==================== 异常处理（统一响应格式） ====================

# 注册 StarletteHTTPException（fastapi.HTTPException 的父类），
# 可同时覆盖业务抛错与未知路由 404
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": str(exc.detail), "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "message": "请求参数校验失败",
            "data": {"errors": jsonable_encoder(exc.errors())},
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"服务器内部错误: {exc}", "data": None},
    )


# ==================== 系统 ====================

@app.get("/", include_in_schema=False, summary="简易控制面板")
async def root():
    """面向普通用户的图形化操作面板（无需懂 API）"""
    return FileResponse("panel.html")


@app.get("/api/health", tags=["系统"], response_model=ApiResponse[HealthData], summary="健康检查")
async def health():
    return ok(HealthData(
        status="ok",
        service="bilibili-ai-agent",
        version="1.0.0",
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))


@app.get("/api/config/persona", tags=["系统"], response_model=ApiResponse[PersonaData],
         summary="查看当前人设配置")
async def get_persona():
    config = load_config()
    return ok(PersonaData(**config["persona"]))


@app.get("/api/stats/today", tags=["系统"], response_model=ApiResponse[StatsData],
         summary="今日统计数据")
async def stats_today():
    stats = get_today_stats()
    return ok(StatsData(
        date=datetime.now().strftime("%Y-%m-%d"),
        dynamics=stats["dynamics"],
        replies=stats["replies"],
        private_replies=stats["private_replies"],
    ))


# ==================== 动态 ====================

@app.get("/api/dynamics", tags=["动态"], response_model=ApiResponse[List[DynamicRecord]],
         summary="获取最近发布的动态记录")
async def list_dynamics(limit: int = Query(10, ge=1, le=50, description="返回条数")):
    memory = load_memory()
    records = get_recent_dynamics(memory, limit=limit)
    return ok([DynamicRecord(**r) for r in records])


@app.post("/api/dynamics", tags=["动态"], response_model=ApiResponse[DynamicPublishData],
          summary="生成并发布一条动态",
          description="调用现有 job_post_dynamic 完整流程（AI生成 → 发布 → 获取评论区信息 → 记录）。")
async def publish_dynamic():
    before = len(load_memory().get("dynamic_history", []))
    await job_post_dynamic()
    history = load_memory().get("dynamic_history", [])
    if len(history) <= before:
        raise HTTPException(status_code=502, detail="动态发布失败，请查看服务日志排查（B站凭证或DeepSeek额度）")
    latest = history[-1]
    return ok(DynamicPublishData(**latest))


@app.post("/api/dynamics/preview", tags=["动态"], response_model=ApiResponse[DynamicPreviewData],
          summary="仅生成动态内容（不发布）",
          description="走与发布完全相同的生成链路（含联网搜索预判），便于预览效果。")
async def preview_dynamic():
    content = AIContentGenerator().generate_dynamic()
    return ok(DynamicPreviewData(content=content))


# ==================== 评论 ====================

@app.get("/api/comments/replied", tags=["评论"], response_model=ApiResponse[List[RepliedCommentRecord]],
         summary="获取已回复评论记录")
async def list_replied_comments(limit: int = Query(20, ge=1, le=100, description="返回条数")):
    memory = load_memory()
    records = memory.get("replied_comments", [])[-limit:]
    return ok([RepliedCommentRecord(**r) for r in records])


@app.post("/api/comments/check", tags=["评论"], response_model=ApiResponse[JobRunData],
          summary="立即检查并回复评论",
          description="检查最近动态下的新评论并逐条回复。返回本次新增的回复条数。")
async def check_comments():
    before = get_today_stats()["replies"]
    await job_check_and_interact()
    after = get_today_stats()["replies"]
    return ok(JobRunData(
        job="check_comments",
        detail="评论检查完成",
        replies_delta=after - before,
    ))


# ==================== 私信 ====================

@app.get("/api/messages/sessions", tags=["私信"], response_model=ApiResponse[List[SessionOverview]],
         summary="私信会话总览（按最近活跃排序）")
async def list_sessions():
    return ok([SessionOverview(**s) for s in get_overview()])


@app.post("/api/messages/check", tags=["私信"], response_model=ApiResponse[JobRunData],
          summary="立即检查并回复私信",
          description="同步B站私信 → 入库去重 → 回复待处理消息（含图片识别）。返回本次新增的回复条数。")
async def check_private_messages():
    before = count_replies_on_date()
    await job_check_private_messages()
    after = count_replies_on_date()
    return ok(JobRunData(
        job="check_private_messages",
        detail="私信检查完成",
        replies_delta=after - before,
    ))


@app.get("/api/messages/sessions/{talker_id}", tags=["私信"],
         response_model=ApiResponse[List[ChatMessageItem]],
         summary="获取与指定用户的聊天记录")
async def get_session_history(
    talker_id: str,
    limit: int = Query(100, ge=1, le=500, description="返回条数"),
):
    messages = get_chat_display(talker_id, limit=limit)
    return ok([ChatMessageItem(**m) for m in messages])


@app.get("/api/messages/sessions/{talker_id}/pending", tags=["私信"],
         response_model=ApiResponse[List[PendingMessageItem]],
         summary="获取该会话的待回复消息")
async def get_pending_messages(talker_id: str):
    return ok([PendingMessageItem(**m) for m in get_unprocessed(talker_id)])


@app.post("/api/messages/sessions/{talker_id}/messages", tags=["私信"],
          response_model=ApiResponse[SendMessageData],
          summary="以本账号身份发送私信")
async def send_message(talker_id: str, body: SendMessageRequest):
    config = load_config()
    my_uid = str(config["bilibili"].get("dedeuserid", ""))
    result = await send_private_msg(int(talker_id), body.content)
    if isinstance(result, dict) and result.get("code", 0) != 0:
        raise HTTPException(
            status_code=502,
            detail=f"B站接口返回错误: code={result.get('code')}, {result.get('message')}",
        )
    add_local_reply(talker_id, my_uid, body.content)
    return ok(SendMessageData(talker_id=talker_id, content=body.content))


# ==================== 搜索 ====================

@app.post("/api/search", tags=["搜索"], response_model=ApiResponse[SearchData],
          summary="联网搜索",
          description="默认使用必应中国RSS（免费无需配置）；配置博查api_key后自动切换。带每日限流与结果缓存。")
async def web_search(body: SearchRequest):
    searcher = WebSearcher()
    result = searcher.search(body.query, body.count)
    return ok(SearchData(query=body.query, found=bool(result), result=result))


# ==================== 调度器 ====================

@app.get("/api/scheduler/status", tags=["调度器"], response_model=ApiResponse[SchedulerStatusData],
         summary="调度器状态")
async def scheduler_status():
    running = bool(_scheduler and _scheduler.running)
    jobs = []
    if running:
        for j in _scheduler.get_jobs():
            nrt = getattr(j, "next_run_time", None)
            jobs.append(JobInfo(id=j.id, name=j.name, next_run_time=nrt.isoformat() if nrt else None))
    return ok(SchedulerStatusData(running=running, jobs=jobs))


@app.post("/api/scheduler/start", tags=["调度器"], response_model=ApiResponse[JobRunData],
          summary="启动后台定时任务",
          description="发动态 / 检查评论 / 检查私信，按 config.json 的 schedule 配置执行。幂等。")
async def scheduler_start():
    global _scheduler
    if _scheduler and _scheduler.running:
        return ok(JobRunData(job="scheduler", detail="调度器已在运行中"))
    _scheduler = create_scheduler()
    _scheduler.start()
    return ok(JobRunData(job="scheduler", detail="调度器已启动"))


@app.post("/api/scheduler/stop", tags=["调度器"], response_model=ApiResponse[JobRunData],
          summary="停止后台定时任务")
async def scheduler_stop():
    global _scheduler
    if not (_scheduler and _scheduler.running):
        return ok(JobRunData(job="scheduler", detail="调度器未在运行"))
    _scheduler.shutdown(wait=False)
    _scheduler = None
    return ok(JobRunData(job="scheduler", detail="调度器已停止"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8100)
