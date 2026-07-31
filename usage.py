"""Codex account quota and local token activity readers."""

from __future__ import annotations

import json
import sqlite3
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


CODEX_DIR = Path.home() / ".codex"
STATE_DB = CODEX_DIR / "state_5.sqlite"
AUTH_JSON = CODEX_DIR / "auth.json"
USAGE_API = "https://chatgpt.com/backend-api/wham/usage"
USAGE_PAGE = "https://chatgpt.com/codex/settings/usage"


@dataclass(slots=True)
class RateWindow:
    label: str
    used_percent: float
    remaining_percent: float
    window_minutes: int
    resets_at: int | None = None


@dataclass(slots=True)
class UsageInfo:
    source: str = "failed"
    plan_type: str | None = None
    windows: list[RateWindow] = field(default_factory=list)
    allowed: bool = True
    limit_reached: bool = False
    credits_balance: str | None = None
    local_tokens_total: int = 0
    local_thread_count: int = 0
    fetched_at: float = 0.0
    error: str | None = None


def _readonly_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def get_local_usage() -> dict[str, int]:
    """Read the durable local totals maintained by Codex."""
    result = {"total": 0, "thread_count": 0}
    if not STATE_DB.exists():
        return result
    try:
        with sqlite3.connect(_readonly_uri(STATE_DB), uri=True, timeout=2) as connection:
            total, count = connection.execute(
                "SELECT COALESCE(SUM(tokens_used), 0), COUNT(*) FROM threads"
            ).fetchone()
            result.update(total=int(total or 0), thread_count=int(count or 0))
    except sqlite3.Error:
        pass
    return result


def _window_label(minutes: int) -> str:
    if 270 <= minutes <= 330:
        return "5 小时"
    if 9_500 <= minutes <= 10_500:
        return "每周"
    if minutes and minutes % 1440 == 0:
        return f"{minutes // 1440} 天"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60} 小时"
    return "额度窗口"


def _parse_window(payload: dict[str, Any] | None) -> RateWindow | None:
    if not payload:
        return None
    minutes = int(
        payload.get("window_minutes")
        or payload.get("windowDurationMins")
        or int(payload.get("limit_window_seconds") or 0) // 60
    )
    used = float(payload.get("used_percent", payload.get("usedPercent", 0)) or 0)
    resets_at = payload.get("reset_at", payload.get("resetsAt"))
    return RateWindow(
        label=_window_label(minutes),
        used_percent=max(0.0, min(100.0, used)),
        remaining_percent=max(0.0, min(100.0, 100.0 - used)),
        window_minutes=minutes,
        resets_at=int(resets_at) if resets_at else None,
    )


def _read_auth() -> tuple[str, str | None]:
    auth = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else auth
    token = tokens.get("access_token")
    if not isinstance(token, str) or len(token) < 20:
        raise ValueError("Codex 登录令牌不可用")
    account_id = tokens.get("account_id")
    return token, account_id if isinstance(account_id, str) else None


def fetch_remote_usage(timeout: float = 8.0) -> UsageInfo:
    """Fetch the same ChatGPT quota snapshot used by Codex clients."""
    info = UsageInfo()
    try:
        token, account_id = _read_auth()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "codex-monitor-widget/1.0",
        }
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        response = requests.get(USAGE_API, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        rate_limit = payload.get("rate_limit") or payload.get("rateLimits") or {}
        candidates = (
            rate_limit.get("primary_window") or rate_limit.get("primary"),
            rate_limit.get("secondary_window") or rate_limit.get("secondary"),
        )
        info.windows = [window for item in candidates if (window := _parse_window(item))]
        info.plan_type = payload.get("plan_type") or rate_limit.get("planType")
        info.allowed = bool(rate_limit.get("allowed", True))
        info.limit_reached = bool(
            rate_limit.get("limit_reached")
            or rate_limit.get("rateLimitReachedType")
            or payload.get("rate_limit_reached_type")
        )
        credits = payload.get("credits") or rate_limit.get("credits") or {}
        if credits.get("has_credits") or credits.get("hasCredits"):
            info.credits_balance = str(credits.get("balance", "0"))
        info.source = "remote"
        info.fetched_at = time.time()
    except (OSError, ValueError, json.JSONDecodeError, requests.RequestException) as exc:
        info.error = str(exc)
    return info


def get_usage() -> UsageInfo:
    info = fetch_remote_usage()
    local = get_local_usage()
    info.local_tokens_total = local["total"]
    info.local_thread_count = local["thread_count"]
    if info.source != "remote":
        info.source = "local"
        info.fetched_at = time.time()
    return info


def format_reset_time(timestamp: int | None) -> str:
    if not timestamp:
        return "重置时间未知"
    target = datetime.fromtimestamp(timestamp)
    delta = target - datetime.now()
    seconds = max(0, int(delta.total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        relative = f"{days} 天 {hours} 小时后"
    elif hours:
        relative = f"{hours} 小时 {minutes} 分后"
    else:
        relative = f"{minutes} 分后"
    return f"{target:%m/%d %H:%M} · {relative}"


def open_usage_page() -> None:
    webbrowser.open(USAGE_PAGE)


if __name__ == "__main__":
    print(get_usage())
