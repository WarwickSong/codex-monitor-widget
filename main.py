"""入口：单实例守卫、运行日志、悬浮窗与后台轮询线程。"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import traceback
from datetime import datetime

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from config import POLL_INTERVAL_SEC, RUN_LOG
from tokens import cache_hit_rate, fit_tokens_per_pct, refresh as refresh_tokens, window_usage
from usage import CodexAuthError, UsageSnapshot, fetch_usage
from widget import QuotaWidget

_MUTEX = None  # 持有引用，进程存活期间互斥体不释放


def _acquire_single_instance() -> bool:
    """同名互斥体已存在说明已有实例，直接退出（静默）。"""
    global _MUTEX
    _MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\CodexQuotaWidget")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _log_run(msg: str) -> None:
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%m-%d %H:%M:%S} pid={os.getpid()} {msg}\n")
    except OSError:
        pass


def _fetch_once(widget: QuotaWidget) -> None:
    try:
        snap = fetch_usage()
    except CodexAuthError as exc:
        snap = UsageSnapshot(error=str(exc))
    except Exception as exc:  # 线程兜底，绝不向上抛
        snap = UsageSnapshot(error=str(exc))
    if os.environ.get("CQ_DEBUG"):
        print(f"[CQ] {snap}", flush=True)
    widget.submit(snap)
    _collect_tokens(widget, snap)


def _collect_tokens(widget: QuotaWidget, snap: UsageSnapshot) -> None:
    """扫会话日志统计 token（增量缓存），投递给面板。永不向上抛。"""
    try:
        sessions = refresh_tokens()
        weekly = snap.weekly
        if weekly and weekly.resets_at:
            window_start = weekly.resets_at - weekly.window_minutes * 60
        else:
            window_start = time.time() - 7 * 86400
        in_t, cached, out = window_usage(sessions, window_start)
        widget.submit_tokens({
            "in_t": in_t,
            "cached": cached,
            "out": out,
            "hit_rate": cache_hit_rate(in_t, cached),
            "per_pct": fit_tokens_per_pct(sessions),
        })
    except Exception as exc:
        if os.environ.get("CQ_DEBUG"):
            print(f"[CQ] tokens error: {exc}", flush=True)


def main() -> int:
    if not _acquire_single_instance():
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName("Codex Quota Widget")
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI",
                      "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSize(9)
    app.setFont(font)
    widget = QuotaWidget()
    # 运行轨迹：start/quit 各记一行；若消失时无 quit 行，即为外部强杀
    app.aboutToQuit.connect(lambda: _log_run("quit"))
    _log_run("start")

    def refresh_now() -> None:
        threading.Thread(target=_fetch_once, args=(widget,), daemon=True).start()

    widget.refresh_callback = refresh_now
    widget.show()
    refresh_now()

    stop = threading.Event()

    def poll_loop() -> None:
        while not stop.wait(POLL_INTERVAL_SEC):
            _fetch_once(widget)

    threading.Thread(target=poll_loop, daemon=True).start()
    rc = app.exec()
    stop.set()
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        with open("crash.log", "a", encoding="utf-8") as fh:
            fh.write(traceback.format_exc() + "\n")
        raise
