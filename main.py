"""入口 —— 启动 Codex 监控悬浮窗。

用法: python main.py
"""
import os
import sys
import traceback
from pathlib import Path

# 确保脚本所在目录在 sys.path 中（pythonw.exe 启动时可能缺少）
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

# 崩溃日志 —— pythonw.exe 没有控制台，出错时静默退出，必须写文件才能排查
_CRASH_LOG = _DIR / "crash.log"


def _write_crash_log(exc_type, exc_value, exc_tb):
    try:
        lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        with open(_CRASH_LOG, "w", encoding="utf-8") as f:
            f.write("".join(lines))
    except Exception:
        pass


def main():
    from widget import main as widget_main
    widget_main()


if __name__ == "__main__":
    sys.excepthook = lambda t, v, tb: (
        _write_crash_log(t, v, tb),
        sys.exit(1),
    )
    try:
        main()
    except Exception:
        traceback.print_exc()
        _write_crash_log(*sys.exc_info())
        sys.exit(1)
