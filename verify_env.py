"""Environment and data-source checks for Codex Monitor."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from monitor import LOGS_DB, WAL_FILE, take_sample
from usage import AUTH_JSON, STATE_DB, get_local_usage


def check_module(name: str) -> bool:
    try:
        module = importlib.import_module(name)
        print(f"[OK] {name} {getattr(module, '__version__', '')}".rstrip())
        return True
    except ImportError:
        print(f"[缺少] {name}")
        return False


def main() -> int:
    print(f"Python {sys.version.split()[0]}")
    modules_ok = all(check_module(name) for name in ("requests", "PySide6"))
    for label, path in (
        ("日志数据库", LOGS_DB),
        ("WAL 文件", WAL_FILE),
        ("任务数据库", STATE_DB),
        ("登录状态", AUTH_JSON),
    ):
        status = "OK" if Path(path).exists() else "未找到"
        print(f"[{status}] {label}: {path}")

    sample = take_sample()
    print(
        f"监控: codex={sample.codex_running} db={sample.database_available} "
        f"trigger={sample.trigger_active} rows={sample.rows:,} wal={sample.wal_bytes:,}B"
    )
    if sample.error:
        print(f"[警告] {sample.error}")
    local = get_local_usage()
    print(f"本地用量: {local['total']:,} tokens / {local['thread_count']} 个任务")
    return 0 if modules_ok and sample.database_available else 1


if __name__ == "__main__":
    raise SystemExit(main())
