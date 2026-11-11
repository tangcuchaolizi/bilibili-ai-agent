"""
B站 AI 智能体 - 图形界面（Tkinter，零新增依赖）
- 控制台标签页：发动态 / 检查评论 / 检查私信 / 统计 / 私信总览 / 后台定时开关 + 实时日志
- 私信聊天标签页：选择会话查看历史、持续多轮对话（自动回复 + 手动插话），无需反复启动

架构：Tkinter 主线程负责界面；asyncio 事件循环跑在后台线程执行网络和 AI 任务；
日志通过队列从后台线程安全地刷到界面，同时写入 bot_gui.log 便于排查。
"""
import asyncio
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, scrolledtext, messagebox

from config_loader import load_config, load_memory
from chat_store import (
    get_session,
    sync_messages,
    get_unprocessed,
    get_chat_display,
    add_local_reply,
    migrate_memory_if_needed,
    get_overview,
)
from bilibili_client import get_private_sessions, get_chat_history, send_private_msg
from scheduler_engine import (
    create_scheduler,
    job_post_dynamic,
    job_check_and_interact,
    job_check_private_messages,
    reply_pending_private_messages,
    print_stats,
    print_private_overview,
)

LOG_FILE = "bot_gui.log"
CHAT_POLL_SECONDS = 10  # 聊天面板轮询间隔


class GuiLogger:
    """接管 print 输出：同时写入日志队列（供界面显示）和 bot_gui.log 文件"""

    def __init__(self, log_queue: queue.Queue):
        self._queue = log_queue
        self._file = open(LOG_FILE, "a", encoding="utf-8")

    def write(self, text: str):
        if not text:
            return
        self._file.write(text)
        self._file.flush()
        self._queue.put(text)

    def flush(self):
        self._file.flush()


class BotGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("B站 AI 智能体")
        self.root.geometry("920x640")

        self.log_queue = queue.Queue()
        sys.stdout = GuiLogger(self.log_queue)
        sys.stderr = sys.stdout

        self.config = load_config()
        self.my_uid = str(self.config["bilibili"].get("dedeuserid", ""))
        self.persona_name = self.config["persona"].get("name", "AI")

        # asyncio 后台线程
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

        self.scheduler = None
        self.current_talker = None  # 当前聊天面板选中的 talker_id
        self._chat_sessions = []    # 会话列表缓存 [(talker_id, uname), ...]

        self._build_ui()
        self.root.after(150, self._poll_log_queue)
        self.root.after(500, self._init_data)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 基础设施 ----------

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_async(self, coro, on_done=None):
        """把协程提交到后台线程执行，完成后在主线程回调 on_done(result)"""
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def _callback(fut):
            try:
                result = fut.result()
            except Exception as e:
                result = e
                print(f"[错误] 任务执行失败: {e}")
            if on_done:
                self.root.after(0, lambda: on_done(result))

        future.add_done_callback(_callback)

    def _poll_log_queue(self):
        try:
            while True:
                text = self.log_queue.get_nowait()
                self.log_text.configure(state="normal")
                self.log_text.insert("end", text)
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log_queue)

    def _on_close(self):
        sched = self.scheduler

        def _shutdown_and_stop():
            if sched:
                try:
                    sched.shutdown(wait=False)
                except Exception:
                    pass
            self.loop.stop()
        self.loop.call_soon_threadsafe(_shutdown_and_stop)
        self.root.destroy()

    # ---------- 界面构建 ----------

    def _build_ui(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)

        # ===== 标签页1：控制台 =====
        tab_console = ttk.Frame(notebook)
        notebook.add(tab_console, text=" 控制台 ")

        btn_frame = ttk.Frame(tab_console)
        btn_frame.pack(fill="x", pady=(0, 6))
        buttons = [
            ("发一条动态", self.on_post_dynamic),
            ("检查评论", self.on_check_comments),
            ("检查私信", self.on_check_private),
            ("今日统计", self.on_show_stats),
            ("私信总览", self.on_show_overview),
        ]
        for text, cmd in buttons:
            ttk.Button(btn_frame, text=text, command=cmd).pack(side="left", padx=3)

        self.btn_sched_start = ttk.Button(btn_frame, text="启动后台定时", command=self.on_start_scheduler)
        self.btn_sched_start.pack(side="left", padx=(20, 3))
        self.btn_sched_stop = ttk.Button(btn_frame, text="停止后台", command=self.on_stop_scheduler, state="disabled")
        self.btn_sched_stop.pack(side="left", padx=3)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(tab_console, textvariable=self.status_var, foreground="#666").pack(anchor="w")

        self.log_text = scrolledtext.ScrolledText(tab_console, state="disabled", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)

        # ===== 标签页2：私信聊天 =====
        tab_chat = ttk.Frame(notebook)
        notebook.add(tab_chat, text=" 私信聊天 ")

        paned = ttk.PanedWindow(tab_chat, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：会话列表
        left = ttk.Frame(paned, width=220)
        paned.add(left, weight=0)
        ttk.Button(left, text="刷新会话列表", command=self.on_refresh_sessions).pack(fill="x", pady=3)
        self.session_list = tk.Listbox(left, exportselection=False)
        self.session_list.pack(fill="both", expand=True)
        self.session_list.bind("<<ListboxSelect>>", self.on_select_session)

        # 右：聊天区
        right = ttk.Frame(paned)
        paned.add(right, weight=1)

        top_bar = ttk.Frame(right)
        top_bar.pack(fill="x")
        self.chat_title_var = tk.StringVar(value="未选择会话")
        ttk.Label(top_bar, textvariable=self.chat_title_var, font=("", 10, "bold")).pack(side="left")
        self.auto_reply_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top_bar, text="自动回复新消息", variable=self.auto_reply_var).pack(side="right")

        self.chat_text = scrolledtext.ScrolledText(right, state="disabled", font=("微软雅黑", 10), wrap="word")
        self.chat_text.pack(fill="both", expand=True, pady=4)
        self.chat_text.tag_config("self", foreground="#0066cc")
        self.chat_text.tag_config("other", foreground="#333333")
        self.chat_text.tag_config("time", foreground="#999999", font=("", 8))

        bottom = ttk.Frame(right)
        bottom.pack(fill="x")
        self.input_var = tk.StringVar()
        entry = ttk.Entry(bottom, textvariable=self.input_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda e: self.on_send_manual())
        ttk.Button(bottom, text="发送（以本人身份）", command=self.on_send_manual).pack(side="right")

    def _init_data(self):
        """启动后：迁移旧数据 + 加载会话列表"""
        print("=" * 50)
        print(f"  B站 AI 智能体 GUI 已启动  ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
        print("=" * 50)
        memory = load_memory()
        if migrate_memory_if_needed(memory):
            print("[迁移] 已将 memory.json 中的私信记录导入数据库")
        self.on_refresh_sessions()
        self._schedule_chat_poll()

    # ---------- 控制台动作 ----------

    def on_post_dynamic(self):
        self.status_var.set("正在生成并发布动态...")
        self.run_async(job_post_dynamic(), lambda _: self.status_var.set("动态发布完成"))

    def on_check_comments(self):
        self.status_var.set("正在检查评论...")
        self.run_async(job_check_and_interact(), lambda _: self.status_var.set("评论检查完成"))

    def on_check_private(self):
        self.status_var.set("正在检查私信...")
        self.run_async(job_check_private_messages(), lambda _: self.status_var.set("私信检查完成"))

    def on_show_stats(self):
        print_stats()

    def on_show_overview(self):
        print_private_overview()

    def on_start_scheduler(self):
        if self.scheduler:
            return

        async def _start():
            # AsyncIOScheduler.start() 必须在 asyncio 事件循环线程内调用
            self.scheduler = create_scheduler()
            self.scheduler.start()

        def _after(result):
            if isinstance(result, Exception):
                self.scheduler = None
                return
            self.btn_sched_start.configure(state="disabled")
            self.btn_sched_stop.configure(state="normal")
            self.status_var.set("后台定时已启动")
            print("[后台] 定时任务已启动（发动态 / 检查评论 / 检查私信）")
        self.run_async(_start(), _after)

    def on_stop_scheduler(self):
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
        self.btn_sched_start.configure(state="normal")
        self.btn_sched_stop.configure(state="disabled")
        self.status_var.set("后台定时已停止")
        print("[后台] 定时任务已停止")

    # ---------- 私信聊天面板 ----------

    def on_refresh_sessions(self):
        """从 B站 API 同步会话列表并刷新左侧列表"""
        async def _load():
            sessions = await get_private_sessions()
            for s in sessions:
                # 会话入库存档（已存在的只更新用户名）
                sync_messages(str(s["talker_id"]), s.get("uname", ""), [], self.my_uid)
            return get_overview()
        self.run_async(_load(), self._fill_session_list)

    def _fill_session_list(self, overview):
        if isinstance(overview, Exception):
            return
        self._chat_sessions = [(s["talker_id"], s["username"] or s["talker_id"], s["pending"]) for s in overview]
        self.session_list.delete(0, "end")
        for _, uname, pending in self._chat_sessions:
            label = f"{uname}" + (f" ({pending}条待回)" if pending else "")
            self.session_list.insert("end", label)

    def on_select_session(self, _event):
        sel = self.session_list.curselection()
        if not sel:
            return
        talker_id, uname, _ = self._chat_sessions[sel[0]]
        self.current_talker = talker_id
        self.chat_title_var.set(f"正在与 {uname} 聊天")
        self._render_chat()

    def _render_chat(self):
        """从数据库渲染当前会话的聊天记录"""
        if not self.current_talker:
            return
        messages = get_chat_display(self.current_talker, limit=100)
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        for m in messages:
            ts = m["timestamp"] or 0
            tstr = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else m["created_at"][5:16]
            self.chat_text.insert("end", f"[{tstr}] ", "time")
            if m["is_self"]:
                body = m["content"] + (f"（图）" if m["image_url"] else "")
                self.chat_text.insert("end", f"{self.persona_name}: {body}\n", "self")
            else:
                body = m["content"]
                if m["image_url"]:
                    body = f"[图片] {m['image_url']}"
                self.chat_text.insert("end", f"对方: {body}\n", "other")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def _schedule_chat_poll(self):
        """每 CHAT_POLL_SECONDS 秒轮询当前会话的新消息（持续对话的核心）"""
        self._poll_current_chat()
        self.root.after(CHAT_POLL_SECONDS * 1000, self._schedule_chat_poll)

    def _poll_current_chat(self):
        if not self.current_talker:
            return
        talker_id = self.current_talker
        auto_reply = self.auto_reply_var.get()

        async def _poll():
            existing = get_session(talker_id)
            sessions = await get_private_sessions()
            target = next((s for s in sessions if str(s["talker_id"]) == talker_id), None)
            if not target:
                return 0
            uname = target.get("uname", "") or talker_id
            max_seqno = int(target.get("max_seqno", 0) or 0)
            last_seqno = existing["last_seqno"] if existing else 0
            if max_seqno <= last_seqno:
                return 0
            messages = await get_chat_history(int(talker_id), last_seqno)
            if messages:
                # 已跟踪的会话：新消息入库待回复；未跟踪的：视为历史不回复
                sync_messages(talker_id, uname, messages, self.my_uid, historical=(existing is None))
            if auto_reply and existing is not None:
                limit = int(self.config["schedule"].get("private_msg_context_limit", 50))
                return await reply_pending_private_messages(talker_id, uname, self.my_uid, limit)
            return 0

        def _after(result):
            if not isinstance(result, Exception) and result:
                self._render_chat()
                self.on_refresh_sessions()
        self.run_async(_poll(), _after)

    def on_send_manual(self):
        """手动发送一条私信（以当前账号身份），支持持续多轮对话"""
        text = self.input_var.get().strip()
        if not text or not self.current_talker:
            return
        talker_id = self.current_talker
        self.input_var.set("")

        async def _send():
            result = await send_private_msg(int(talker_id), text)
            if isinstance(result, dict) and result.get("code", 0) != 0:
                raise Exception(f"接口返回错误代码：{result.get('code')}, 信息：{result.get('message')}")
            add_local_reply(talker_id, self.my_uid, text)

        def _after(result):
            if isinstance(result, Exception):
                messagebox.showerror("发送失败", str(result))
            else:
                print(f"[手动发送] 已发送给 {self.chat_title_var.get()}: {text}")
                self._render_chat()
        self.run_async(_send(), _after)


def main():
    app = BotGUI()
    app.root.mainloop()


if __name__ == "__main__":
    main()
