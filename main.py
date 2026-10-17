"""入口：启动悬浮窗与后台轮询线程。"""
from __future__ import annotations

import os
import sys
import threading
import traceback

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from config import POLL_INTERVAL_SEC
from usage import CodexAuthError, UsageSnapshot, fetch_usage
from widget import QuotaWidget


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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Codex Quota Widget")
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI",
                      "Microsoft YaHei UI", "Microsoft YaHei"])
    font.setPointSize(9)
    app.setFont(font)
    widget = QuotaWidget()

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
