# -*- coding: utf-8 -*-
"""
NVIDIA 着色器缓存清理工具
一键强制清理 %LOCALAPPDATA%\\NVIDIA 下的全部缓存，完成后自动重启系统。

作者: linexx
"""

from __future__ import annotations

import ctypes
import os
import queue
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox

APP_NAME = "NVIDIA 着色器缓存清理工具"
AUTHOR = "linexx"
VERSION = "1.0.0"

CREATE_NO_WINDOW = 0x08000000
MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004
RESTART_DELAY = 15

# 占用缓存的常见 NVIDIA 进程
PROCESS_NAMES = (
    "nvcontainer.exe",
    "nvsphelper64.exe",
    "NVIDIA Share.exe",
    "NVIDIA Overlay.exe",
)

COLORS = {
    "bg": "#17171b",
    "card": "#212127",
    "card_alt": "#2a2a32",
    "border": "#33333d",
    "fg": "#eceef2",
    "muted": "#8f8f9c",
    "green": "#76b900",
    "green_hover": "#8ad600",
    "green_dim": "#5c8f00",
    "red": "#e5484d",
    "amber": "#f5a524",
}

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_BIG = ("Microsoft YaHei UI", 13, "bold")
FONT_MONO = ("Consolas", 9)


def _target_dir() -> str:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(local, "NVIDIA")


TARGET_DIR = _target_dir()


# --------------------------------------------------------------------------- #
# Win32 辅助
# --------------------------------------------------------------------------- #
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
_kernel32.MoveFileExW.restype = wintypes.BOOL
_kernel32.SetFileAttributesW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
_kernel32.SetFileAttributesW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUIDAndAttributes(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", wintypes.DWORD),
        ("Privileges", _LUIDAndAttributes * 1),
    ]


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重启自身，成功返回 True。"""
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            args = subprocess.list2cmdline(sys.argv[1:])
        else:
            exe = sys.executable
            args = subprocess.list2cmdline(sys.argv)
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe, args, os.path.dirname(os.path.abspath(exe)), 1
        )
        return int(rc) > 32
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _enable_privilege(name: str) -> bool:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.LookupPrivilegeValueW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(_LUID),
    )
    advapi32.AdjustTokenPrivileges.argtypes = (
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(_TokenPrivileges),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    token = wintypes.HANDLE()
    TOKEN_ADJUST_PRIVILEGES = 0x0020
    TOKEN_QUERY = 0x0008
    SE_PRIVILEGE_ENABLED = 0x00000002
    try:
        if not advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(token),
        ):
            return False
        luid = _LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        privs = _TokenPrivileges(
            1, (_LUIDAndAttributes * 1)(_LUIDAndAttributes(luid, SE_PRIVILEGE_ENABLED))
        )
        ok = advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privs), 0, None, None
        )
        return bool(ok)
    except Exception:
        return False
    finally:
        try:
            _kernel32.CloseHandle(token)
        except Exception:
            pass


def enable_all_privileges() -> None:
    for name in (
        "SeTakeOwnershipPrivilege",
        "SeRestorePrivilege",
        "SeBackupPrivilege",
        "SeDebugPrivilege",
    ):
        _enable_privilege(name)


# --------------------------------------------------------------------------- #
# 文件操作
# --------------------------------------------------------------------------- #
def is_safe_target(path: str) -> bool:
    """只允许操作 ...\\AppData\\Local\\NVIDIA，防止误删。"""
    if not path:
        return False
    norm = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    expected = os.path.normcase(
        os.path.normpath(os.path.join("appdata", "local", "nvidia"))
    )
    return norm.endswith(expected)


def fmt_size(num: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if num < 1024 or unit == units[-1]:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TB"


def scan_usage(root: str) -> tuple[int, int]:
    count = 0
    size = 0
    for dirpath, _dirnames, filenames in os.walk(root, onerror=lambda e: None):
        for name in filenames:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size
                count += 1
            except OSError:
                continue
    return count, size


def _gather(root: str) -> tuple[list[str], list[str]]:
    """自底向上收集文件与目录。"""
    files: list[str] = []
    dirs: list[str] = []
    for dirpath, dirnames, filenames in os.walk(
        root, topdown=False, onerror=lambda e: None
    ):
        for name in filenames:
            files.append(os.path.join(dirpath, name))
        for name in dirnames:
            dirs.append(os.path.join(dirpath, name))
    return files, dirs


def _clear_readonly(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    try:
        _kernel32.SetFileAttributesW(path, 0x80)  # FILE_ATTRIBUTE_NORMAL
    except Exception:
        pass


def _delete_one(path: str) -> str:
    """返回 ok / pending（重启后删除） / fail。"""
    is_dir = os.path.isdir(path)
    _clear_readonly(path)
    try:
        if is_dir:
            os.rmdir(path)
        else:
            os.remove(path)
        return "ok"
    except OSError:
        pass
    try:
        if _kernel32.MoveFileExW(
            os.path.abspath(path), None, MOVEFILE_DELAY_UNTIL_REBOOT
        ):
            return "pending"
    except Exception:
        pass
    return "fail"


def _repair_acl(target: str) -> None:
    """接管所有权并重置权限，仅在首次删除失败时调用一次。"""
    cmds = [
        ["takeown", "/f", target, "/r", "/d", "y"],
        ["icacls", target, "/reset", "/t", "/c", "/q"],
        ["icacls", target, "/grant", "*S-1-5-32-544:F", "/t", "/c", "/q"],
    ]
    for cmd in cmds:
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=120,
            )
        except Exception:
            continue


def _kill_processes(log) -> None:
    for name in PROCESS_NAMES:
        try:
            result = subprocess.run(
                ["taskkill", "/f", "/im", name],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
            if result.returncode == 0:
                log(f"已结束占用进程：{name}", "ok")
        except Exception:
            continue


def clean_nvidia_cache(target: str, kill_procs: bool, log, done) -> None:
    """核心清理流程。log(msg, tag) / done(stats, reason)。"""
    enable_all_privileges()
    stats = {"ok": 0, "pending": 0, "fail": 0, "left": 0, "left_size": 0}

    log(f"目标目录：{target}", "title")
    if not is_safe_target(target):
        log("目标路径校验未通过，为安全起见已中止操作。", "err")
        done(stats, "unsafe")
        return
    if not os.path.isdir(target):
        log("缓存目录不存在，无需清理。", "warn")
        done(stats, "missing")
        return

    if kill_procs:
        log("正在结束可能的占用进程…", "info")
        _kill_processes(log)

    log("正在扫描缓存文件…", "info")
    files, dirs = _gather(target)
    log(f"发现 {len(files):,} 个文件、{len(dirs):,} 个目录，开始删除。", "info")

    failed: list[str] = []
    for idx, path in enumerate(files, 1):
        state = _delete_one(path)
        if state == "ok":
            stats["ok"] += 1
        elif state == "pending":
            stats["pending"] += 1
        else:
            failed.append(path)
        if idx % 1000 == 0:
            log(f"处理中… {idx:,} / {len(files):,}", "info")

    if failed:
        log(f"{len(failed):,} 个文件被占用或权限不足，尝试接管所有权后重试…", "warn")
        _repair_acl(target)
        still: list[str] = []
        for path in failed:
            if not os.path.exists(path):
                stats["ok"] += 1
                continue
            state = _delete_one(path)
            if state == "ok":
                stats["ok"] += 1
            elif state == "pending":
                stats["pending"] += 1
            else:
                still.append(path)
        failed = still

    for path in dirs:
        if os.path.isdir(path) and _delete_one(path) == "fail":
            stats["fail"] += 1

    stats["fail"] += len(failed)
    stats["left"], stats["left_size"] = scan_usage(target)

    log(f"直接删除 {stats['ok']:,} 项", "ok")
    if stats["pending"]:
        log(f"{stats['pending']:,} 项已标记为重启时删除", "warn")
    if stats["fail"]:
        log(f"{stats['fail']:,} 项无法删除（可能被内核或驱动锁定）", "warn")
    if stats["left"]:
        log(f"目录仍有 {stats['left']:,} 个文件 / {fmt_size(stats['left_size'])}", "warn")
    else:
        log("缓存目录已清空。", "ok")
    done(stats, "done")


# --------------------------------------------------------------------------- #
# 界面
# --------------------------------------------------------------------------- #
class CleanerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.cleaning = False
        self.restart_win: tk.Toplevel | None = None
        self._cd_job = None
        self._cd_left = RESTART_DELAY

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._poll)
        self.start_scan()

    # ------------------------------ 构建界面 ------------------------------ #
    def _build_ui(self) -> None:
        root = self.root
        root.title(f"{APP_NAME} v{VERSION}")
        root.configure(bg=COLORS["bg"])
        root.geometry("800x680")
        root.minsize(720, 600)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        # 顶部标题
        header = tk.Frame(root, bg=COLORS["bg"])
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))

        tk.Label(
            header,
            text="NV",
            bg=COLORS["green"],
            fg="#101014",
            font=("Segoe UI", 14, "bold"),
            padx=12,
            pady=6,
        ).pack(side="left")

        title_box = tk.Frame(header, bg=COLORS["bg"])
        title_box.pack(side="left", padx=14)
        tk.Label(
            title_box,
            text=APP_NAME,
            bg=COLORS["bg"],
            fg=COLORS["fg"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="强制清理显卡着色器缓存 · 完成自动重启",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        ).pack(anchor="w")

        tk.Label(
            header,
            text=f"v{VERSION}  ·  by {AUTHOR}",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        ).pack(side="right", pady=(6, 0))

        # 主体
        body = tk.Frame(root, bg=COLORS["bg"])
        body.grid(row=1, column=0, sticky="nsew", padx=24)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # 路径卡片
        card = tk.Frame(
            body,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        card.grid(row=0, column=0, sticky="ew")
        inner = tk.Frame(card, bg=COLORS["card"])
        inner.pack(fill="x", padx=18, pady=14)

        tk.Label(
            inner,
            text="缓存目录（自动识别当前用户）",
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        ).pack(anchor="w")

        path_row = tk.Frame(inner, bg=COLORS["card"])
        path_row.pack(fill="x", pady=(5, 0))
        tk.Label(
            path_row,
            text=TARGET_DIR,
            bg=COLORS["card"],
            fg=COLORS["fg"],
            font=FONT_MONO,
            anchor="w",
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        self.open_btn = tk.Button(
            path_row,
            text="打开",
            command=self.open_folder,
            bg=COLORS["card_alt"],
            fg=COLORS["fg"],
            activebackground=COLORS["border"],
            activeforeground=COLORS["fg"],
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            padx=12,
            pady=2,
            cursor="hand2",
        )
        self.open_btn.pack(side="right")

        self.stats_label = tk.Label(
            inner,
            text="正在统计缓存占用…",
            bg=COLORS["card"],
            fg=COLORS["green"],
            font=FONT_UI,
            anchor="w",
        )
        self.stats_label.pack(anchor="w", pady=(10, 0))

        # 操作区
        action = tk.Frame(body, bg=COLORS["bg"])
        action.grid(row=1, column=0, sticky="ew", pady=14)

        self.btn = tk.Button(
            action,
            text="一键清理缓存",
            command=self.on_clean,
            bg=COLORS["green"],
            fg="#0d0d10",
            activebackground=COLORS["green_hover"],
            activeforeground="#0d0d10",
            font=FONT_BIG,
            relief="flat",
            bd=0,
            cursor="hand2",
            pady=12,
        )
        self.btn.pack(fill="x")
        self.btn.bind("<Enter>", self._btn_enter)
        self.btn.bind("<Leave>", self._btn_leave)

        opts = tk.Frame(action, bg=COLORS["bg"])
        opts.pack(fill="x", pady=(10, 0))

        self.kill_var = tk.BooleanVar(value=True)
        self.restart_var = tk.BooleanVar(value=True)

        for var, text in (
            (self.kill_var, "清理前结束 NVIDIA 占用进程"),
            (self.restart_var, f"清理完成后自动重启电脑（{RESTART_DELAY}s 倒计时可取消）"),
        ):
            tk.Checkbutton(
                opts,
                text=text,
                variable=var,
                bg=COLORS["bg"],
                fg=COLORS["muted"],
                activebackground=COLORS["bg"],
                activeforeground=COLORS["fg"],
                selectcolor=COLORS["card_alt"],
                font=FONT_SMALL,
                bd=0,
                highlightthickness=0,
                cursor="hand2",
            ).pack(side="left", padx=(0, 18))

        # 日志卡片
        log_card = tk.Frame(
            body,
            bg=COLORS["card"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
        )
        log_card.grid(row=2, column=0, sticky="nsew", pady=(0, 16))

        log_head = tk.Frame(log_card, bg=COLORS["card"])
        log_head.pack(fill="x", padx=16, pady=(10, 0))
        tk.Label(
            log_head,
            text="运行日志",
            bg=COLORS["card"],
            fg=COLORS["fg"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side="left")
        tk.Button(
            log_head,
            text="清空",
            command=self.clear_log,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            activebackground=COLORS["card"],
            activeforeground=COLORS["fg"],
            relief="flat",
            bd=0,
            font=FONT_SMALL,
            cursor="hand2",
        ).pack(side="right")

        text_row = tk.Frame(log_card, bg=COLORS["card"])
        text_row.pack(fill="both", expand=True, padx=8, pady=6)

        scrollbar = tk.Scrollbar(
            text_row,
            bg=COLORS["card_alt"],
            troughcolor=COLORS["card"],
            activebackground=COLORS["border"],
            bd=0,
            relief="flat",
            width=10,
        )
        scrollbar.pack(side="right", fill="y")

        self.log_text = tk.Text(
            text_row,
            bg=COLORS["card"],
            fg=COLORS["fg"],
            insertbackground=COLORS["fg"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=FONT_MONO,
            wrap="word",
            padx=12,
            pady=8,
            height=10,
            state="disabled",
            yscrollcommand=scrollbar.set,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.log_text.yview)

        self.log_text.tag_configure("info", foreground=COLORS["fg"])
        self.log_text.tag_configure("ok", foreground=COLORS["green"])
        self.log_text.tag_configure("warn", foreground=COLORS["amber"])
        self.log_text.tag_configure("err", foreground=COLORS["red"])
        self.log_text.tag_configure(
            "title",
            foreground=COLORS["fg"],
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.log_text.tag_configure("time", foreground=COLORS["muted"])

        # 底部状态栏
        footer = tk.Frame(root, bg=COLORS["bg"])
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.status_label = tk.Label(
            footer,
            text="就绪",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        )
        self.status_label.pack(side="left")
        tk.Label(
            footer,
            text=f"作者 {AUTHOR}",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        ).pack(side="right")

        if not is_safe_target(TARGET_DIR):
            self.btn.configure(
                state="disabled", text="目标路径异常，已禁用", bg=COLORS["card_alt"]
            )
            self.log("目标路径校验失败，无法执行清理。", "err")
        else:
            self.log("准备就绪，正在统计缓存占用…", "info")

    # ------------------------------ 通用 UI ------------------------------ #
    def _btn_enter(self, _event=None) -> None:
        if str(self.btn["state"]) == "normal":
            self.btn.configure(bg=COLORS["green_hover"])

    def _btn_leave(self, _event=None) -> None:
        if str(self.btn["state"]) == "normal":
            self.btn.configure(bg=COLORS["green"])

    def log(self, msg: str, tag: str = "info") -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] ", ("time",))
        self.log_text.insert("end", f"{msg}\n", (tag,))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def open_folder(self) -> None:
        try:
            if os.path.isdir(TARGET_DIR):
                os.startfile(TARGET_DIR)
            else:
                messagebox.showinfo(APP_NAME, "缓存目录当前不存在。")
        except Exception as exc:
            messagebox.showwarning(APP_NAME, f"无法打开目录：{exc}")

    # ------------------------------ 统计 ------------------------------ #
    def start_scan(self) -> None:
        def work() -> None:
            count, size = scan_usage(TARGET_DIR)
            self.q.put(("scan", count, size))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------ 清理 ------------------------------ #
    def on_clean(self) -> None:
        if self.cleaning:
            return
        if not is_safe_target(TARGET_DIR):
            messagebox.showerror(APP_NAME, "目标路径校验未通过，已停止操作。")
            return

        self.cleaning = True
        self.btn.configure(
            state="disabled",
            text="正在清理，请稍候…",
            bg=COLORS["green_dim"],
            cursor="watch",
        )
        self.set_status("正在清理缓存…")
        self.log("", "info")
        self.log("开始清理 NVIDIA 着色器缓存", "title")

        kill_procs = bool(self.kill_var.get())
        restart = bool(self.restart_var.get())
        threading.Thread(
            target=self._clean_worker, args=(kill_procs, restart), daemon=True
        ).start()

    def _clean_worker(self, kill_procs: bool, restart: bool) -> None:
        def log(msg: str, tag: str = "info") -> None:
            self.q.put(("log", msg, tag))

        def done(stats: dict, reason: str) -> None:
            self.q.put(("done", (stats, reason, restart)))

        try:
            clean_nvidia_cache(TARGET_DIR, kill_procs, log, done)
        except Exception as exc:  # 兜底，避免线程静默崩溃
            log(f"清理过程出现异常：{exc}", "err")
            done({"ok": 0, "pending": 0, "fail": 0, "left": 1, "left_size": 0}, "error")

    def _on_done(self, stats: dict, reason: str, restart: bool) -> None:
        self.cleaning = False
        self.btn.configure(
            state="normal",
            text="一键清理缓存",
            bg=COLORS["green"],
            cursor="hand2",
        )
        self.start_scan()

        if reason == "done":
            total = stats["ok"] + stats["pending"]
            self.set_status(f"清理完成，已处理 {total:,} 项")
            if restart:
                self.begin_restart_countdown()
        elif reason == "unsafe":
            self.set_status("目标路径异常，已停止")
        elif reason == "missing":
            self.set_status("缓存目录不存在")
        else:
            self.set_status("清理过程出现异常")

    # ------------------------------ 重启倒计时 ------------------------------ #
    def begin_restart_countdown(self) -> None:
        if self.restart_win is not None and self.restart_win.winfo_exists():
            return

        win = tk.Toplevel(self.root)
        self.restart_win = win
        win.title("清理完成")
        win.configure(bg=COLORS["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", lambda: self._cancel_restart(win))

        width, height = 460, 250
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + max(
            (self.root.winfo_width() - width) // 2, 0
        )
        y = self.root.winfo_rooty() + max(
            (self.root.winfo_height() - height) // 2, 0
        )
        win.geometry(f"{width}x{height}+{x}+{y}")

        box = tk.Frame(win, bg=COLORS["bg"])
        box.pack(fill="both", expand=True, padx=26, pady=22)

        tk.Label(
            box,
            text="缓存已清理完成",
            bg=COLORS["bg"],
            fg=COLORS["green"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor="w")

        self._cd_label = tk.Label(
            box,
            text="",
            bg=COLORS["bg"],
            fg=COLORS["fg"],
            font=("Microsoft YaHei UI", 11),
            justify="left",
            anchor="w",
        )
        self._cd_label.pack(anchor="w", pady=(10, 0))

        tk.Label(
            box,
            text="重启会在开机时清除所有被占用的缓存文件。",
            bg=COLORS["bg"],
            fg=COLORS["muted"],
            font=FONT_SMALL,
        ).pack(anchor="w", pady=(4, 0))

        row = tk.Frame(box, bg=COLORS["bg"])
        row.pack(side="bottom", fill="x", pady=(14, 0))

        tk.Button(
            row,
            text="取消重启",
            command=lambda: self._cancel_restart(win),
            bg=COLORS["card_alt"],
            fg=COLORS["fg"],
            activebackground=COLORS["border"],
            activeforeground=COLORS["fg"],
            relief="flat",
            bd=0,
            font=FONT_UI,
            pady=7,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        tk.Button(
            row,
            text="立即重启",
            command=lambda: self._restart_now(win),
            bg=COLORS["green"],
            fg="#0d0d10",
            activebackground=COLORS["green_hover"],
            activeforeground="#0d0d10",
            relief="flat",
            bd=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            pady=7,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True)

        win.grab_set()
        self._cd_left = RESTART_DELAY
        self._tick_countdown(win)

    def _tick_countdown(self, win: tk.Toplevel) -> None:
        if not win.winfo_exists():
            return
        if self._cd_left <= 0:
            self._restart_now(win)
            return
        self._cd_label.configure(
            text=f"系统将在 {self._cd_left} 秒后自动重启…"
        )
        self._cd_left -= 1
        self._cd_job = self.root.after(1000, lambda: self._tick_countdown(win))

    def _cancel_countdown(self) -> None:
        if self._cd_job is not None:
            try:
                self.root.after_cancel(self._cd_job)
            except Exception:
                pass
            self._cd_job = None

    def _cancel_restart(self, win: tk.Toplevel) -> None:
        self._cancel_countdown()
        try:
            subprocess.run(
                ["shutdown", "/a"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=10,
            )
        except Exception:
            pass
        if win.winfo_exists():
            win.destroy()
        self.restart_win = None
        self.log("已取消自动重启。被占用的缓存项将在下次开机时删除。", "warn")
        self.set_status("已取消重启")

    def _restart_now(self, win: tk.Toplevel) -> None:
        self._cancel_countdown()
        if win.winfo_exists():
            win.destroy()
        self.restart_win = None
        self.perform_restart()

    def perform_restart(self) -> None:
        self.log("正在重启系统…", "warn")
        self.set_status("系统即将重启…")
        try:
            result = subprocess.run(
                [
                    "shutdown",
                    "/r",
                    "/t",
                    "0",
                    "/c",
                    "NVIDIA 着色器缓存清理完成，系统即将重启",
                ],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
            if result.returncode != 0:
                self.log("自动重启失败，请手动重启电脑。", "err")
                messagebox.showwarning(
                    APP_NAME, "自动重启失败，请手动重启电脑以完成缓存清理。"
                )
        except Exception as exc:
            self.log(f"自动重启失败：{exc}", "err")

    # ------------------------------ 事件循环 ------------------------------ #
    def _poll(self) -> None:
        while True:
            try:
                item = self.q.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "log":
                self.log(item[1], item[2])
            elif kind == "scan":
                count, size = item[1], item[2]
                self.stats_label.configure(
                    text=f"{count:,} 个文件  ·  {fmt_size(size)}"
                )
            elif kind == "done":
                self._on_done(*item[1])
        self.root.after(80, self._poll)

    def on_close(self) -> None:
        if self.cleaning:
            if not messagebox.askyesno(APP_NAME, "正在清理中，确定要退出吗？"):
                return
        if self.restart_win is not None and self.restart_win.winfo_exists():
            self._cancel_countdown()
            try:
                subprocess.run(
                    ["shutdown", "/a"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=10,
                )
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    enable_dpi_awareness()

    if not is_admin():
        if relaunch_as_admin():
            return
        ctypes.windll.user32.MessageBoxW(
            None,
            "未能获取管理员权限，部分被占用的缓存可能无法删除。\n"
            "建议右键以管理员身份运行本程序。",
            APP_NAME,
            0x30,
        )

    root = tk.Tk()
    CleanerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
