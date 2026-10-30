"""Codex 会话日志 token 统计：解析 ~/.codex/sessions，实测 1% 额度 ≈ 多少 token。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import CODEX_DIR

SESSIONS_DIR = CODEX_DIR / "sessions"

# rollout-<ts>-<会话id>[_<续聊id>].jsonl：续聊文件的累计值包含前史，按首个 uuid 去重
_CONV_RE = re.compile(r"rollout-[\dT:-]+-([0-9a-f-]{36})(?:_[0-9a-f-]{36})?\.jsonl$")

# {path: (mtime_ns, size, SessionTokens)} 进程内缓存，跨轮询只增量读变动文件
_CACHE: dict[str, tuple[int, int, Optional["SessionTokens"]]] = {}


@dataclass
class SessionTokens:
    end_ts: float                 # 会话内最后一个 token_count 的时间（epoch 秒）
    resets_at: Optional[int]      # 该时刻所属的额度窗口
    pct_end: Optional[float]      # 会话结束时的 used_percent
    input_t: int = 0              # 非缓存输入（含 cache_write）
    cached_t: int = 0             # 缓存命中输入
    output_t: int = 0             # 输出（含 reasoning）

    @property
    def total(self) -> int:
        return self.input_t + self.cached_t + self.output_t


def _parse_count_line(line: str):
    """单行 → (ts, total_token_usage, used_percent, resets_at)，非 token_count 行返回 None。"""
    if '"token_count"' not in line or '"total_token_usage"' not in line:
        return None
    try:
        evt = json.loads(line)
        payload = evt.get("payload", {})
        if payload.get("type") != "token_count":
            return None
        info = payload.get("info", {})
        total = info.get("total_token_usage") or {}
        rate = payload.get("rate_limits") or {}
        primary = rate.get("primary") or {}
        ts = datetime.fromisoformat(evt["timestamp"].replace("Z", "+00:00")).timestamp()
        return (
            ts,
            total,
            primary.get("used_percent"),
            primary.get("resets_at"),
        )
    except (ValueError, KeyError, TypeError):
        return None


def session_totals(path: Path) -> Optional[SessionTokens]:
    """一个 rollout 文件 → 会话 token 汇总（取首末 token_count 事件）。"""
    first = last = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parsed = _parse_count_line(line)
                if parsed is None:
                    continue
                if first is None:
                    first = parsed
                last = parsed
    except OSError:
        return None
    if last is None:
        return None
    _, total, pct_end, resets_at = last
    inp = total.get("input_tokens", 0) + total.get("cache_write_input_tokens", 0)
    return SessionTokens(
        end_ts=last[0],
        resets_at=resets_at,
        pct_end=pct_end,
        input_t=int(inp),
        cached_t=int(total.get("cached_input_tokens", 0)),
        output_t=int(total.get("output_tokens", 0)),
    )


def refresh(sessions_dir: Path = SESSIONS_DIR) -> list[SessionTokens]:
    """增量扫描目录，返回会话汇总（mtime 缓存 + 续聊链去重，取链上最新文件）。"""
    if not sessions_dir.exists():
        return []
    per_conv: dict[str, SessionTokens] = {}
    live_paths = set()
    for path in sessions_dir.rglob("rollout-*.jsonl"):
        key = str(path)
        live_paths.add(key)
        try:
            st = path.stat()
        except OSError:
            continue
        sig = (st.st_mtime_ns, st.st_size)
        cached = _CACHE.get(key)
        if cached and (cached[0], cached[1]) == sig:
            sess = cached[2]
        else:
            sess = session_totals(path)
            _CACHE[key] = (*sig, sess)
        if sess is None:
            continue
        m = _CONV_RE.search(path.name)
        conv_id = m.group(1) if m else key
        # 同一会话链：只保留结束最晚的文件（其累计值已包含前史）
        if conv_id not in per_conv or sess.end_ts > per_conv[conv_id].end_ts:
            per_conv[conv_id] = sess
    for stale in set(_CACHE) - live_paths:
        del _CACHE[stale]
    return sorted(per_conv.values(), key=lambda s: s.end_ts)


def window_usage(sessions: list[SessionTokens], window_start_ts: float):
    """当前窗口内的 token 合计 → (输入, 缓存命中, 输出)。"""
    in_ = cached = out = 0
    for s in sessions:
        if s.end_ts >= window_start_ts:
            in_ += s.input_t
            cached += s.cached_t
            out += s.output_t
    return in_, cached, out


def cache_hit_rate(in_: int, cached: int) -> Optional[float]:
    denom = in_ + cached
    return cached / denom if denom else None


def fit_tokens_per_pct(sessions: list[SessionTokens]) -> Optional[float]:
    """实测拟合：1% 额度 ≈ 多少 token（过原点最小二乘，按窗口分组累积）。

    原理：同一额度窗口内，会话按时间排序、token 累积，与各时点 used_percent
    形成过原点直线，斜率倒数即 token/%。样本不足或百分比几乎未动时返回 None。
    """
    by_window: dict[int, list[SessionTokens]] = {}
    for s in sessions:
        if s.resets_at is None or s.pct_end is None:
            continue
        by_window.setdefault(s.resets_at, []).append(s)

    pairs: list[tuple[float, float]] = []  # (累计token, pct)
    for group in by_window.values():
        group.sort(key=lambda s: s.end_ts)
        cum = 0.0
        prev_pct: Optional[float] = None
        for s in group:
            if prev_pct is not None and s.pct_end < prev_pct - 0.05:
                cum = 0.0  # 同窗口内百分比回落（异常/统计回退），重新起算
            cum += s.total
            pairs.append((cum, s.pct_end))
            prev_pct = s.pct_end

    if len(pairs) < 3 or max(p for _, p in pairs) < 2.0:
        return None
    num = sum(c * p for c, p in pairs)
    den = sum(c * c for c, _ in pairs)
    slope_pct_per_tok = num / den if den else 0.0
    if slope_pct_per_tok <= 0:
        return None
    return 1.0 / slope_pct_per_tok


def fmt_tok(n: float) -> str:
    if n >= 1e8:
        return f"{n / 1e8:.2f}亿"
    if n >= 1e4:
        return f"{n / 1e4:.0f}万"
    return f"{int(n)}"
