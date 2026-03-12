"""
Claude Code Monitor
- 监控 ~/.claude/projects/ 下 jsonl 文件大小变化判断状态
- 进程检测确定实例存在/消失
"""

import tkinter as tk
import threading
import subprocess
import json
import time
import os
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude" / "projects"
POLL_INTERVAL = 0.5  # jsonl 文件检查间隔（秒）
IDLE_THRESHOLD = 3.0  # 文件停止增长多久后检查是否完成
MIN_FILE_SIZE = 1024  # 小于此大小的 jsonl 忽略（未真正使用的会话）
GONE_THRESHOLD = 30   # 已完成回复且超过此秒数没更新，认为会话已结束

STATES = {
    "working": {"text": "工作中", "color": "#4CAF50", "icon": "⚙️"},
    "waiting": {"text": "等待交互", "color": "#FF9800", "icon": "⏳"},
    "idle": {"text": "空闲", "color": "#808080", "icon": "💤"},
}


def get_claude_processes():
    """获取所有 Claude Code node 进程"""
    processes = {}
    ps_file = Path(__file__).parent / "_get_procs.ps1"
    ps_file.write_text(
        'Get-CimInstance Win32_Process -Filter "Name=\'node.exe\'" |\n'
        '    Where-Object { $_.CommandLine -match "claude-code" } |\n'
        '    Select-Object ProcessId, CommandLine |\n'
        '    ConvertTo-Json\n',
        encoding='utf-8'
    )
    try:
        result = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', str(ps_file)],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        procs = json.loads(result.stdout) if result.stdout.strip() else []
        if not isinstance(procs, list):
            procs = [procs] if procs else []
        for p in procs:
            pid = p.get('ProcessId')
            if pid:
                processes[str(pid)] = {"pid": pid, "cmd": p.get('CommandLine', '')}
    except:
        pass
    return processes


def get_active_jsonl_files():
    """获取活跃的 jsonl 文件（排除子代理和过小的文件）"""
    active = {}
    if not CLAUDE_DIR.exists():
        return active
    now = time.time()
    for jsonl in CLAUDE_DIR.rglob("*.jsonl"):
        try:
            # 跳过 subagents 目录
            if "subagents" in str(jsonl):
                continue
            st = jsonl.stat()
            # 跳过太小的文件（未真正使用的会话）
            if st.st_size < MIN_FILE_SIZE:
                continue
            mtime = st.st_mtime
            age = now - mtime
            # 超过 10 分钟没更新的直接跳过
            if age > 600:
                continue
            project = jsonl.parent.name
            # 从 jsonl 第一条记录中读取 cwd
            cwd = _read_cwd(jsonl)
            active[str(jsonl)] = {"project": project, "size": st.st_size, "mtime": mtime, "path": jsonl, "cwd": cwd}
        except:
            pass
    return active


def _read_cwd(filepath):
    """从 jsonl 文件开头读取 cwd 字段"""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f):
                if i >= 20:
                    break
                try:
                    entry = json.loads(line.strip())
                    if 'cwd' in entry:
                        return entry['cwd']
                except:
                    continue
    except:
        pass
    return None


def is_finished_responding(filepath):
    """判断会话是否已完成回复（等待用户输入）
    跳过 progress/system/file-history-snapshot，找最后一条 assistant 或 user 记录
    返回: True=等待交互, False=仍在工作, None=无法判断
    """
    try:
        with open(filepath, 'rb') as f:
            f.seek(0, 2)
            fsize = f.tell()
            read_size = min(fsize, 32768)
            f.seek(fsize - read_size)
            lines = f.read().decode('utf-8', errors='ignore').strip().split('\n')

        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except:
                continue

            etype = entry.get("type", "")

            # 跳过非消息类型
            if etype in ("progress", "system", "file-history-snapshot"):
                continue

            if etype == "assistant":
                content = entry.get("message", {}).get("content", [])
                content_types = {c.get("type") for c in content if isinstance(c, dict)}
                if "tool_use" in content_types:
                    return False  # 还在调用工具
                return True  # 纯 text，已完成回复

            if etype == "user":
                content = entry.get("message", {}).get("content", [])
                content_types = {c.get("type") for c in content if isinstance(c, dict)}
                if "tool_result" in content_types:
                    return False  # 工具返回结果，Claude 还要继续
                if not content_types or content_types == {"text"}:
                    return True  # 空消息或纯文本，等待 Claude 回复或用户输入
                return False

            # 其他未知类型，跳过继续找
            continue
    except:
        pass
    return None


class ClaudeMonitor:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claude Code Monitor")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.width = 300
        screen_w = self.root.winfo_screenwidth()
        self.root.geometry(f"{self.width}x55+{screen_w - self.width - 20}+20")

        # session_key -> {project, state, last_size, last_change_time}
        self.sessions = {}
        self.widgets = {}
        self.running = True

        self.setup_ui()
        self.setup_drag()

        # jsonl 监控线程
        threading.Thread(target=self.monitor_loop, daemon=True).start()
        # 进程扫描线程（清理已关闭的实例）
        threading.Thread(target=self.process_scan_loop, daemon=True).start()

        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def setup_ui(self):
        self.main = tk.Frame(self.root, bg="#2d2d2d", relief="raised", bd=2)
        self.main.pack(fill="both", expand=True)

        title_bar = tk.Frame(self.main, bg="#3d3d3d")
        title_bar.pack(fill="x")

        tk.Label(title_bar, text="📊 Claude Code Monitor", font=("Microsoft YaHei UI", 10, "bold"),
                 bg="#3d3d3d", fg="white").pack(side="left", padx=8, pady=4)

        close_btn = tk.Label(title_bar, text="×", font=("Arial", 12, "bold"),
                            bg="#3d3d3d", fg="#666666", cursor="hand2")
        close_btn.pack(side="right", padx=5)
        close_btn.bind("<Button-1>", lambda e: self.close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ff5555"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#666666"))

        refresh_btn = tk.Label(title_bar, text="↻", font=("Arial", 12, "bold"),
                              bg="#3d3d3d", fg="#666666", cursor="hand2")
        refresh_btn.pack(side="right", padx=2)
        refresh_btn.bind("<Button-1>", lambda e: self.force_refresh())
        refresh_btn.bind("<Enter>", lambda e: refresh_btn.config(fg="#4CAF50"))
        refresh_btn.bind("<Leave>", lambda e: refresh_btn.config(fg="#666666"))

        self.summary = tk.Label(self.main, text="Scanning...", font=("Microsoft YaHei UI", 9),
                               bg="#2d2d2d", fg="#aaaaaa")
        self.summary.pack(fill="x", padx=8, pady=5)

        self.list_frame = tk.Frame(self.main, bg="#2d2d2d")
        self.list_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

    def setup_drag(self):
        def start(e):
            self.drag_x, self.drag_y = e.x, e.y
        def drag(e):
            x = self.root.winfo_x() + e.x - self.drag_x
            y = self.root.winfo_y() + e.y - self.drag_y
            self.root.geometry(f"+{x}+{y}")
        self.root.bind("<Button-1>", start)
        self.root.bind("<B1-Motion>", drag)

    def monitor_loop(self):
        """主监控循环：检查 jsonl 文件大小变化"""
        while self.running:
            try:
                self.check_jsonl_files()
            except:
                pass
            time.sleep(POLL_INTERVAL)

    def check_jsonl_files(self):
        """检查所有活跃的 jsonl 文件"""
        active = get_active_jsonl_files()
        now = time.time()

        current_keys = set()
        for key, info in active.items():
            current_keys.add(key)
            size = info["size"]

            if key not in self.sessions:
                # 新发现的会话，读最后一条判断状态
                finished = is_finished_responding(info["path"])
                state = "working" if finished is False else "waiting"
                self.sessions[key] = {
                    "project": info["project"],
                    "cwd": info.get("cwd"),
                    "state": state,
                    "last_size": size,
                    "last_change_time": info["mtime"],
                }
                self.root.after(0, lambda k=key: self.add_widget(k))
            else:
                session = self.sessions[key]
                if size != session["last_size"]:
                    # 文件在增长 → 正在工作
                    session["last_size"] = size
                    session["last_change_time"] = now
                    if session["state"] != "working":
                        session["state"] = "working"
                        self.root.after(0, lambda k=key: self.update_widget_state(k, "working"))
                else:
                    # 文件没变，检查最后一条记录判断是否已完成
                    elapsed = now - session["last_change_time"]
                    if elapsed > IDLE_THRESHOLD and session["state"] == "working":
                        finished = is_finished_responding(info["path"])
                        if finished:
                            session["state"] = "waiting"
                            self.root.after(0, lambda k=key: self.update_widget_state(k, "waiting"))

        # 移除不再活跃的会话
        stale = set(self.sessions.keys()) - current_keys
        for key in stale:
            del self.sessions[key]
            self.root.after(0, lambda k=key: self.remove_widget(k))

        self.root.after(0, self.update_summary)

    def process_scan_loop(self):
        """定期检查进程，清理已关闭的实例"""
        while self.running:
            try:
                procs = get_claude_processes()
                proc_count = len(procs)
                session_count = len(self.sessions)
                now = time.time()

                if proc_count == 0 and session_count > 0:
                    # 没有进程了，清理所有会话
                    for key in list(self.sessions.keys()):
                        del self.sessions[key]
                        self.root.after(0, lambda k=key: self.remove_widget(k))
                    self.root.after(0, self.update_summary)
                elif proc_count < session_count:
                    # 进程数 < 会话数，移除已完成且停止更新的会话
                    to_remove = []
                    for key, session in self.sessions.items():
                        elapsed = now - session["last_change_time"]
                        if elapsed > GONE_THRESHOLD and session["state"] == "waiting":
                            to_remove.append(key)
                    # 只移除多余的数量
                    excess = session_count - proc_count
                    for key in to_remove[:excess]:
                        del self.sessions[key]
                        self.root.after(0, lambda k=key: self.remove_widget(k))
                    if to_remove:
                        self.root.after(0, self.update_summary)
            except:
                pass
            time.sleep(5)

    def force_refresh(self):
        """强制刷新"""
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        procs = get_claude_processes()
        proc_count = len(procs)
        session_count = len(self.sessions)
        now = time.time()
        if proc_count == 0:
            for key in list(self.sessions.keys()):
                del self.sessions[key]
                self.root.after(0, lambda k=key: self.remove_widget(k))
        elif proc_count < session_count:
            to_remove = []
            for key, session in self.sessions.items():
                elapsed = now - session["last_change_time"]
                if elapsed > GONE_THRESHOLD and session["state"] == "waiting":
                    to_remove.append(key)
            excess = session_count - proc_count
            for key in to_remove[:excess]:
                del self.sessions[key]
                self.root.after(0, lambda k=key: self.remove_widget(k))
        self.check_jsonl_files()

    def add_widget(self, key):
        if key in self.widgets:
            return
        session = self.sessions.get(key)
        if not session:
            return

        state = session["state"]
        state_info = STATES.get(state, STATES["idle"])

        # 从 cwd 取最后一级目录名
        cwd = session.get("cwd")
        if cwd:
            short_name = Path(cwd).name[:25]
        else:
            # fallback: 从项目目录名取
            segments = session["project"].split("--")
            last = segments[-1].lstrip("-") if segments else session["project"]
            short_name = last[:25] or session["project"][:25]

        frame = tk.Frame(self.list_frame, bg="#3a3a3a")
        frame.pack(fill="x", pady=1, padx=2)

        icon_lbl = tk.Label(frame, text=state_info["icon"], font=("Segoe UI Emoji", 11), bg="#3a3a3a")
        icon_lbl.pack(side="left", padx=(5, 2))
        name_lbl = tk.Label(frame, text=short_name, font=("Microsoft YaHei UI", 9),
                bg="#3a3a3a", fg="white")
        name_lbl.pack(side="left", padx=(0, 5))
        status_lbl = tk.Label(frame, text=state_info["text"], font=("Microsoft YaHei UI", 8),
                             bg="#3a3a3a", fg=state_info["color"])
        status_lbl.pack(side="left")

        self.widgets[key] = {"frame": frame, "icon": icon_lbl, "status": status_lbl, "name": name_lbl}
        self.adjust_size()

    def remove_widget(self, key):
        if key in self.widgets:
            self.widgets[key]["frame"].destroy()
            del self.widgets[key]
            self.adjust_size()

    def update_widget_state(self, key, state):
        if key not in self.widgets:
            return
        state_info = STATES.get(state, STATES["idle"])
        self.widgets[key]["icon"].config(text=state_info["icon"])
        self.widgets[key]["status"].config(text=state_info["text"], fg=state_info["color"])
        # 闪烁
        frame = self.widgets[key]["frame"]
        frame.config(bg="#555555")
        for child in frame.winfo_children():
            child.config(bg="#555555")
        self.root.after(500, lambda: self._reset_bg(key))

    def _reset_bg(self, key):
        if key in self.widgets:
            frame = self.widgets[key]["frame"]
            frame.config(bg="#3a3a3a")
            for child in frame.winfo_children():
                child.config(bg="#3a3a3a")

    def adjust_size(self):
        count = len(self.widgets)
        h = 32 + 28 + count * 30 + 10
        if count == 0:
            h = 65
        self.root.geometry(f"{self.width}x{h}")

    def update_summary(self):
        count = len(self.sessions)
        working = sum(1 for s in self.sessions.values() if s["state"] == "working")
        waiting = sum(1 for s in self.sessions.values() if s["state"] == "waiting")

        if count == 0:
            self.summary.config(text="No Claude Code instances")
        else:
            parts = []
            if working > 0:
                parts.append(f"⚙️ {working}")
            if waiting > 0:
                parts.append(f"⏳ {waiting}")
            self.summary.config(text="  |  ".join(parts) if parts else f"● {count} running")

    def close(self):
        self.running = False
        self.root.quit()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ClaudeMonitor().run()
