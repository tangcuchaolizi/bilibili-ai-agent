"""
FastAPI 接口的 Pydantic 数据模型
统一定义请求体 / 响应体结构，提供自动校验与 OpenAPI 文档
"""
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# ==================== 通用响应 ====================

class ApiResponse(BaseModel, Generic[T]):
    """统一响应格式"""
    code: int = Field(0, description="业务状态码：0=成功，非0=HTTP错误码")
    message: str = Field("ok", description="提示信息")
    data: Optional[T] = Field(None, description="业务数据")


# ==================== 系统 ====================

class HealthData(BaseModel):
    status: str = Field(..., description="服务状态", examples=["ok"])
    service: str = Field(..., description="服务名称")
    version: str = Field(..., description="版本号")
    time: str = Field(..., description="服务器当前时间")


class PersonaData(BaseModel):
    """人设配置（不含任何凭证信息）"""
    name: str
    gender: str
    age: str
    city: str
    personality: str
    speaking_style: str
    topics: List[str]
    taboos: List[str]
    fans_name: str


class StatsData(BaseModel):
    """今日统计"""
    date: str
    dynamics: int = Field(..., description="今日发布动态数")
    replies: int = Field(..., description="今日评论回复数")
    private_replies: int = Field(..., description="今日私信回复数")


# ==================== 动态 ====================

class DynamicRecord(BaseModel):
    dynamic_id: str = Field(..., description="动态ID")
    content: str = Field(..., description="动态内容")
    time: str = Field(..., description="发布时间")
    dyn_rid: int = Field(0, description="评论区RID")
    comment_type: int = Field(17, description="评论类型（17文字/11图文）")


class DynamicPublishData(BaseModel):
    dynamic_id: str
    dyn_rid: int
    comment_type: int
    content: str
    time: str


class DynamicPreviewData(BaseModel):
    content: str = Field(..., description="AI生成的动态内容（未发布）")


# ==================== 评论 ====================

class RepliedCommentRecord(BaseModel):
    comment_rpid: str = Field(..., description="评论rpid")
    reply_content: str = Field(..., description="回复内容")
    time: str = Field(..., description="回复时间")


# ==================== 任务触发 ====================

class JobRunData(BaseModel):
    """手动触发任务的执行结果"""
    job: str = Field(..., description="任务标识")
    detail: str = Field(..., description="执行说明")
    replies_delta: int = Field(0, description="本次执行新增的回复条数")


# ==================== 私信 ====================

class SessionOverview(BaseModel):
    """私信会话总览项"""
    talker_id: str
    username: str
    total: int = Field(..., description="消息总数")
    pending: int = Field(..., description="待回复数")
    last_content: Optional[str] = Field(None, description="最近一条消息内容")
    last_is_self: int = Field(0, description="最近一条是否由本账号发出")
    last_ts: int = Field(0, description="最近一条消息时间戳")
    updated_at: str = ""


class ChatMessageItem(BaseModel):
    """聊天消息项（展示用）"""
    is_self: int = Field(..., description="1=本账号发出，0=对方发出")
    content: str
    image_url: str = Field("", description="图片地址（图片消息时非空）")
    timestamp: int = 0
    created_at: str = ""


class PendingMessageItem(BaseModel):
    """待回复消息项"""
    id: int
    msg_key: int
    content: str
    image_url: str = ""
    timestamp: int = 0


class SendMessageRequest(BaseModel):
    """发送私信请求体"""
    content: str = Field(..., min_length=1, max_length=500, description="私信内容，1-500字")


class SendMessageData(BaseModel):
    talker_id: str
    content: str


# ==================== 搜索 ====================

class SearchRequest(BaseModel):
    """联网搜索请求体"""
    query: str = Field(..., min_length=1, max_length=100, description="搜索关键词")
    count: int = Field(5, ge=1, le=10, description="返回结果条数，1-10")


class SearchData(BaseModel):
    query: str
    found: bool = Field(..., description="是否获得有效结果")
    result: str = Field(..., description="格式化结果文本（编号+标题+摘要），无结果时为空")


# ==================== 调度器 ====================

class JobInfo(BaseModel):
    id: str
    name: str
    next_run_time: Optional[str] = Field(None, description="下次执行时间（ISO格式）")


class SchedulerStatusData(BaseModel):
    running: bool
    jobs: List[JobInfo]
