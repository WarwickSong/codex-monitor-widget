"""Codex 额度获取：auth 读取、API 调用、快照解析（解析为纯函数）。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import requests

from config import AUTH_JSON, USAGE_API

UA = "codex-quota-widget/1.0"


class CodexAuthError(RuntimeError):
    """Codex 未登录或 auth.json 无效。"""


@dataclass(frozen=True)
class RateWindow:
    used_pct: float
    window_minutes: int
    resets_at: Optional[int]  # Unix 秒


@dataclass(frozen=True)
class UsageSnapshot:
    fetched_at: float
    weekly: Optional[RateWindow] = None
    five_hour: Optional[RateWindow] = None
    error: Optional[str] = None


def _window_minutes(payload: dict) -> Optional[int]:
    for key in ("window_minutes", "windowDurationMins"):
        if payload.get(key) is not None:
            return int(payload[key])
    secs = payload.get("limit_window_seconds")
    return int(secs) // 60 if secs else None


def _resets_at(payload: dict) -> Optional[int]:
    """兼容 reset_at / resets_at / resetsAt / reset_after_seconds。"""
    for key in ("reset_at", "resets_at", "resetsAt"):
        v = payload.get(key)
        if v is not None:
            return int(v)
    after = payload.get("reset_after_seconds") or payload.get("resetAfterSeconds")
    if after:
        return int(time.time() + after)
    return None


def parse_window(payload) -> Optional[RateWindow]:
    if not isinstance(payload, dict):
        return None
    minutes = _window_minutes(payload)
    used = payload.get("used_percent", payload.get("usedPercent"))
    if minutes is None or used is None:
        return None
    return RateWindow(float(used), minutes, _resets_at(payload))


def _classify(minutes: int) -> Optional[str]:
    if 1440 * 5 <= minutes <= 1440 * 8:
        return "weekly"
    if 270 <= minutes <= 330:
        return "five_hour"
    return None


def parse_usage(payload: dict) -> UsageSnapshot:
    """从上游 JSON 提取周窗口与 5h 窗口（键名兼容 snake/camel）。"""
    weekly = five = None
    rate_limit = payload.get("rate_limit") or payload.get("rate_limits") or payload.get("rateLimits") or {}
    candidates = (
        rate_limit.get("primary_window") or rate_limit.get("primary"),
        rate_limit.get("secondary_window") or rate_limit.get("secondary"),
    )
    for cand in candidates:
        win = parse_window(cand)
        if not win:
            continue
        kind = _classify(win.window_minutes)
        if kind == "weekly" and weekly is None:
            weekly = win
        elif kind == "five_hour" and five is None:
            five = win
    return UsageSnapshot(fetched_at=time.time(), weekly=weekly, five_hour=five)


def read_auth() -> Tuple[str, Optional[str]]:
    if not AUTH_JSON.exists():
        raise CodexAuthError(f"未找到 {AUTH_JSON}，请先用 Codex CLI 登录")
    auth = json.loads(AUTH_JSON.read_text(encoding="utf-8"))
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else auth
    token = tokens.get("access_token")
    if not isinstance(token, str) or len(token) < 20:
        raise CodexAuthError("Codex 登录令牌无效")
    return token, tokens.get("account_id")


def fetch_usage(timeout: float = 8.0) -> UsageSnapshot:
    """拉取额度快照；除登录问题外，异常一律转 error 快照返回。"""
    token, account_id = read_auth()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": UA,
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    try:
        resp = requests.get(USAGE_API, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return parse_usage(resp.json())
    except Exception as exc:
        return UsageSnapshot(fetched_at=time.time(), error=str(exc))
