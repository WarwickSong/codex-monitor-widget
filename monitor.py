"""Read-only sampling for Codex process and SQLite write activity."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


CODEX_DIR = Path.home() / ".codex"
LOGS_DB = CODEX_DIR / "logs_2.sqlite"
WAL_FILE = CODEX_DIR / "logs_2.sqlite-wal"
TRIGGER_NAME = "block_log_inserts"

PROTECTION_TRIGGER_SQL = (
    "CREATE TRIGGER block_log_inserts\n"
    "BEFORE INSERT ON logs\n"
    "BEGIN\n"
    "  SELECT RAISE(IGNORE);\n"
    "END"
)


@dataclass(slots=True)
class Sample:
    ts: float
    rows: int = 0
    max_id: int = 0
    wal_bytes: int = 0
    codex_running: bool = False
    trigger_active: bool = False
    trigger_intact: bool = False
    trigger_sql: str = ""
    trigger_hash: str = ""
    trigger_issue: str = ""
    database_available: bool = False
    error: str | None = None


@dataclass(slots=True)
class Rate:
    row_rate: float = 0.0
    wal_rate: float = 0.0


@dataclass(slots=True)
class TriggerResult:
    success: bool = False
    message: str = ""
    error: str | None = None


def is_codex_running() -> bool:
    """Return whether a Codex desktop/CLI process is visible on Windows."""
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq codex.exe", "/NH", "/FO", "CSV"],
            capture_output=True,
            timeout=2,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = result.stdout.decode("gbk", errors="ignore") if result.stdout else ""
        return "codex.exe" in output.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def _readonly_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def _evaluate_trigger_sql(sql: str) -> tuple[bool, str]:
    """严格校验触发器 SQL 是否能无条件拦住 logs 表的 INSERT。"""
    if not sql:
        return False, "触发器 SQL 为空"
    upper = sql.upper()
    if not re.search(r"\bBEFORE\s+INSERT\b", upper, re.IGNORECASE):
        return False, "触发器不是 BEFORE INSERT，可能拦不住写入"
    if not re.search(r"\bON\s+logs\b", upper, re.IGNORECASE):
        return False, "触发器不作用于 logs 表"
    if re.search(r"\bWHEN\b", upper, re.IGNORECASE):
        return False, "触发器含 WHEN 条件，存在绕过路径"
    if "RAISE(IGNORE)" not in upper:
        return False, "触发器不含 RAISE(IGNORE)"
    if re.search(r"\b(UPDATE|DELETE|REPLACE)\b", upper):
        return False, "触发器包含额外写操作"
    return True, ""


def _sql_hash(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]


def take_sample() -> Sample:
    """Capture one read-only snapshot without creating SQLite side files."""
    sample = Sample(ts=time.time(), codex_running=is_codex_running())
    try:
        sample.wal_bytes = WAL_FILE.stat().st_size
    except OSError:
        sample.wal_bytes = 0

    if not LOGS_DB.exists():
        sample.error = "未找到 logs_2.sqlite"
        return sample

    try:
        with sqlite3.connect(_readonly_uri(LOGS_DB), uri=True, timeout=1) as connection:
            sample.rows, sample.max_id = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM logs"
            ).fetchone()
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (TRIGGER_NAME,),
            ).fetchone()
            raw_sql = trigger[0] if trigger and trigger[0] else ""
            sample.trigger_sql = raw_sql
            sample.trigger_hash = _sql_hash(raw_sql) if raw_sql else ""
            sample.trigger_active = bool(raw_sql and "RAISE(IGNORE)" in raw_sql.upper())
            sample.trigger_intact, sample.trigger_issue = _evaluate_trigger_sql(raw_sql)
            sample.database_available = True
    except (sqlite3.Error, OSError) as exc:
        sample.error = f"数据库读取失败：{exc}"
    return sample


def calc_rate(previous: Sample, current: Sample) -> Rate:
    """Calculate non-negative growth rates between two valid samples."""
    if not previous.database_available or not current.database_available:
        return Rate()
    elapsed = current.ts - previous.ts
    if elapsed <= 0:
        return Rate()
    return Rate(
        row_rate=max(0.0, (current.max_id - previous.max_id) / elapsed),
        wal_rate=max(0.0, (current.wal_bytes - previous.wal_bytes) / elapsed),
    )


def install_protection_trigger() -> TriggerResult:
    """安装无持久写副作用的纯拦截触发器。"""
    if not LOGS_DB.exists():
        return TriggerResult(error="数据库不存在")
    try:
        with sqlite3.connect(str(LOGS_DB), timeout=5) as connection:
            connection.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
            connection.execute(PROTECTION_TRIGGER_SQL)
            connection.commit()
        return TriggerResult(success=True, message="保护触发器已安装")
    except (sqlite3.Error, OSError) as exc:
        return TriggerResult(error=f"安装失败: {exc}")


def ensure_protection_trigger(sample: Sample) -> TriggerResult:
    """触发器缺失或定义异常时恢复纯拦截触发器。"""
    if not sample.database_available:
        return TriggerResult(error="数据库不可用")
    if sample.trigger_intact:
        return TriggerResult(success=True, message="保护触发器在位")
    return install_protection_trigger()


if __name__ == "__main__":
    sample = take_sample()
    if sample.database_available and not sample.trigger_intact:
        print("安装保护触发器...", install_protection_trigger())
    first = take_sample()
    time.sleep(1)
    second = take_sample()
    rate = calc_rate(first, second)
    print(second)
    print(f"rows/s={rate.row_rate:.2f} wal_B/s={rate.wal_rate:.0f}")
    print(f"trigger_intact={second.trigger_intact} issue={second.trigger_issue!r}")
    print(f"trigger_hash={second.trigger_hash}")
