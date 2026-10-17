"""双口径额度预估：纯函数，无 IO、无 Qt 依赖。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MIN_ELAPSED_H = 0.5  # 防除零下限


@dataclass(frozen=True)
class Scenario:
    rate_per_hour: float      # 该口径消耗速率 %/h
    end_remaining_pct: float  # 到重置时预计剩余%（负数 = 超支）
    hours_left: float         # 剩余额度按此速率还能用多少小时
    days_left: float          # hours_left / 24


@dataclass(frozen=True)
class Forecast:
    rate_all: float           # 全时段平均速率 %/h
    rate_wd: float            # 工作日小时速率 %/h
    remaining_all_h: float    # 距重置全部小时
    remaining_wd_h: float     # 距重置工作日小时
    working_day: Scenario     # 工作日节奏（只在周一~五用）
    every_day: Scenario       # 每天都用（含周末）
    fallback_wd: bool         # 工作日速率是否来自保守回退


def working_hours_between(start: datetime, end: datetime) -> float:
    """[start, end) 区间内周一~周五的小时数（本地时间，按整天 24h 计）。"""
    if end <= start:
        return 0.0
    total = 0.0
    day = start.date()
    last = (end - timedelta(seconds=1)).date()
    while day <= last:
        if day.weekday() < 5:
            day_start = datetime.combine(day, datetime.min.time())
            lo = max(start, day_start)
            hi = min(end, day_start + timedelta(days=1))
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def weekday_rate_from_history(samples, now=None, min_days=3.0):
    """由本地历史快照估计工作日小时速率。

    samples: [(datetime, used_pct)] 升序；相邻样本净增量按区间中点
    归属工作日/周末。覆盖时长不足 min_days 天、或工作日增量为非正
    时返回 None（调用方走保守回退）。
    """
    now = now or datetime.now()
    pts = [(t, u) for t, u in samples if t <= now]
    if len(pts) < 2:
        return None
    span_h = (pts[-1][0] - pts[0][0]).total_seconds() / 3600
    if span_h < min_days * 24:
        return None
    wd_used = 0.0
    for (t1, u1), (t2, u2) in zip(pts, pts[1:]):
        mid = t1 + (t2 - t1) / 2
        if mid.weekday() < 5:
            wd_used += u2 - u1
    wd_hours = working_hours_between(pts[0][0], pts[-1][0])
    if wd_hours <= 0 or wd_used <= 0:
        return None
    return wd_used / wd_hours


def forecast(used_pct, resets_at, window_minutes, now=None, history_samples=None):
    """计算双口径预估。

    used_pct: 窗口已用%；resets_at: 重置时刻；window_minutes: 窗口长度；
    history_samples: [(datetime, used_pct)]，用于把速率拆到工作日/周末。
    """
    now = now or datetime.now()
    used = max(0.0, min(100.0, float(used_pct)))
    window_start = resets_at - timedelta(minutes=window_minutes)

    elapsed_all_h = max((now - window_start).total_seconds() / 3600, MIN_ELAPSED_H)
    rate_all = used / elapsed_all_h

    elapsed_wd_h = max(working_hours_between(window_start, now), MIN_ELAPSED_H)
    fallback_wd = True
    rate_wd = used / elapsed_wd_h  # 保守回退：全部消耗视为工作日发生
    if history_samples:
        est = weekday_rate_from_history(history_samples, now=now)
        if est is not None:
            rate_wd = est
            fallback_wd = False

    remaining_all_h = max((resets_at - now).total_seconds() / 3600, 0.0)
    remaining_wd_h = working_hours_between(now, resets_at)
    remain_quota = 100.0 - used

    def scenario(rate, remaining_h):
        end_rem = remain_quota - rate * remaining_h
        hours_left = remain_quota / rate if rate > 0 else float("inf")
        return Scenario(rate, end_rem, hours_left, hours_left / 24)

    return Forecast(
        rate_all=rate_all,
        rate_wd=rate_wd,
        remaining_all_h=remaining_all_h,
        remaining_wd_h=remaining_wd_h,
        working_day=scenario(rate_wd, remaining_wd_h),
        every_day=scenario(rate_all, remaining_all_h),
        fallback_wd=fallback_wd,
    )
