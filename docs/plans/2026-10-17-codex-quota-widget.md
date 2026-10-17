# Codex Quota Widget 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个 Windows 极简置顶悬浮条，显示 Codex 周限额剩余，悬停展开「工作日节奏 / 每天都用」双口径预估（按当前速率、锚定下次重置）。

**Architecture:** 纯函数预估层（forecast）+ IO 层（usage 拉 API、history 存本地快照）+ PySide6 悬浮窗（widget/main），后台线程轮询、Qt 信号跨线程投递。折叠态一条小字，悬停 QVariantAnimation 原地展开。

**Tech Stack:** Python 3.10+、PySide6 ≥6.6、requests、unittest。

**Spec:** `docs/2026-10-17-codex-quota-widget-design.md`

## Global Constraints

- 仅 Windows；依赖仅 `PySide6>=6.6`、`requests>=2.31`
- Python ≥3.10；所有模块首行 `from __future__ import annotations`
- UI 文案为中文；轮询间隔 300 秒；历史不足 3 天时工作日速率走保守回退
- 数据目录 `~/.codex-quota-widget/`（history.jsonl + settings.json）
- 测试命令统一：`.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 每个任务结束必须 git commit

---

### Task 1: 项目骨架 + forecast 预估纯函数（TDD）

**Files:**
- Create: `.gitignore`、`requirements.txt`、`config.py`、`forecast.py`、`tests/__init__.py`、`tests/test_forecast.py`

**Interfaces:**
- Produces:
  - `forecast.working_hours_between(start: datetime, end: datetime) -> float`
  - `forecast.weekday_rate_from_history(samples: list[tuple[datetime, float]], now: datetime | None = None, min_days: float = 3.0) -> float | None`
  - `forecast.forecast(used_pct: float, resets_at: datetime, window_minutes: int, now: datetime | None = None, history_samples: list | None = None) -> Forecast`
  - `Forecast(rate_all, rate_wd, remaining_all_h, remaining_wd_h, working_day: Scenario, every_day: Scenario, fallback_wd: bool)`；`Scenario(rate_per_hour, end_remaining_pct, hours_left, days_left)`
  - `config` 常量：`DATA_DIR/HISTORY_FILE/SETTINGS_FILE/AUTH_JSON/USAGE_API/USAGE_PAGE/POLL_INTERVAL_SEC/COLLAPSED_H/PANEL_W/PANEL_H/EXPAND_MS/COLOR_GREEN/COLOR_AMBER/COLOR_RED`

- [ ] **Step 1: 环境与骨架文件**

检查 Python（`python --version`，需 ≥3.10；若无则试 `py -3`）。写入：

`.gitignore`:
```
__pycache__/
.venv/
*.log
crash.log
```

`requirements.txt`:
```
PySide6>=6.6
requests>=2.31
```

建虚拟环境并装依赖：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

- [ ] **Step 2: 写失败测试 `tests/test_forecast.py`**

```python
import unittest
from datetime import datetime

from forecast import forecast, weekday_rate_from_history, working_hours_between


class TestWorkingHours(unittest.TestCase):
    def test_full_week(self):
        # 2026-10-12 是周一，完整一周工作日 = 5*24
        self.assertAlmostEqual(
            working_hours_between(datetime(2026, 10, 12), datetime(2026, 10, 19)), 120.0)

    def test_across_weekend(self):
        # 周五 23:00 → 周一 01:00：周五 1h + 周一 1h，周末不计
        self.assertAlmostEqual(
            working_hours_between(datetime(2026, 10, 16, 23), datetime(2026, 10, 19, 1)), 2.0)

    def test_empty_range(self):
        t = datetime(2026, 10, 15, 12)
        self.assertEqual(working_hours_between(t, t), 0.0)


class TestForecast(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 12, 0)      # 周三
    RESETS = datetime(2026, 10, 16, 12, 0)   # 周五，48h 后

    def test_every_day_and_fallback(self):
        f = forecast(50.0, self.RESETS, 10080, now=self.NOW)
        # 窗口起点 = 10-16 12:00 − 7天 = 10-09 12:00（周五）→ 全程 120h
        self.assertAlmostEqual(f.rate_all, 50.0 / 120.0)
        self.assertAlmostEqual(f.remaining_all_h, 48.0)
        self.assertAlmostEqual(f.every_day.end_remaining_pct, 50.0 - (50.0 / 120.0) * 48.0)
        self.assertAlmostEqual(f.every_day.days_left, 120.0 / 24.0)  # 5.0 天
        # 无历史 → 保守回退：全部消耗视为工作日发生
        self.assertTrue(f.fallback_wd)
        # 窗口内工作日小时：周五12 + 周一24 + 周二24 + 周三12 = 72
        self.assertAlmostEqual(f.rate_wd, 50.0 / 72.0)
        # 距重置工作日小时：周三12 + 周四24 + 周五12 = 48（无周末）
        self.assertAlmostEqual(f.remaining_wd_h, 48.0)
        self.assertAlmostEqual(f.working_day.end_remaining_pct, 50.0 - (50.0 / 72.0) * 48.0)

    def test_working_day_hours_across_weekend(self):
        f = forecast(50.0, datetime(2026, 10, 19, 12, 0), 10080, now=self.NOW)
        # 周三12→周一12：周三12 + 周四24 + 周五24 + 周一12 = 72，周末不计
        self.assertAlmostEqual(f.remaining_wd_h, 72.0)

    def test_history_overrides_fallback(self):
        samples = [
            (datetime(2026, 10, 12, 12), 10.0),
            (datetime(2026, 10, 13, 12), 30.0),  # 周二 +20
            (datetime(2026, 10, 14, 12), 30.0),
            (datetime(2026, 10, 15, 12), 30.0),
            (datetime(2026, 10, 16, 12), 30.0),
        ]
        f = forecast(50.0, self.RESETS, 10080, now=datetime(2026, 10, 16, 12), history_samples=samples)
        self.assertFalse(f.fallback_wd)
        self.assertAlmostEqual(f.rate_wd, 20.0 / 96.0)  # 观测期工作日 96h


class TestHistoryRate(unittest.TestCase):
    def test_insufficient_span_returns_none(self):
        samples = [(datetime(2026, 10, 15, 12), 10.0), (datetime(2026, 10, 16, 12), 30.0)]
        self.assertIsNone(weekday_rate_from_history(samples, now=datetime(2026, 10, 16, 12)))

    def test_nonpositive_usage_returns_none(self):
        samples = [(datetime(2026, 10, 12, 12), 30.0), (datetime(2026, 10, 16, 12), 30.0)]
        self.assertIsNone(weekday_rate_from_history(samples, now=datetime(2026, 10, 16, 12)))
```

- [ ] **Step 3: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: FAIL/ERROR（`ModuleNotFoundError: No module named 'forecast'`）

- [ ] **Step 4: 实现 `config.py` 与 `forecast.py`**

`config.py`:
```python
"""集中配置：路径、轮询、UI 常量。"""
from __future__ import annotations

from pathlib import Path

# —— 数据与 Codex ——
DATA_DIR = Path.home() / ".codex-quota-widget"
HISTORY_FILE = DATA_DIR / "history.jsonl"
SETTINGS_FILE = DATA_DIR / "settings.json"

CODEX_DIR = Path.home() / ".codex"
AUTH_JSON = CODEX_DIR / "auth.json"
USAGE_API = "https://chatgpt.com/backend-api/wham/usage"
USAGE_PAGE = "https://chatgpt.com/codex/settings/usage"

POLL_INTERVAL_SEC = 5 * 60  # 额度轮询间隔

# —— UI ——
COLLAPSED_H = 28              # 折叠条高度 px
PANEL_W, PANEL_H = 300, 204   # 展开面板尺寸 px
EXPAND_MS = 150               # 展开/收起动画时长

COLOR_GREEN = "#34d399"
COLOR_AMBER = "#fbbf24"
COLOR_RED = "#f87171"
```

`forecast.py`:
```python
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```powershell
git add .gitignore requirements.txt config.py forecast.py tests/
git commit -m "feat: forecast dual-scenario quota projection with tests"
```

---

### Task 2: usage 额度获取与解析（TDD）

**Files:**
- Create: `usage.py`、`tests/test_usage_parse.py`

**Interfaces:**
- Consumes: `config.AUTH_JSON/USAGE_API`
- Produces:
  - `usage.CodexAuthError(RuntimeError)`
  - `usage.RateWindow(used_pct: float, window_minutes: int, resets_at: int | None)`
  - `usage.UsageSnapshot(fetched_at: float, weekly: RateWindow | None, five_hour: RateWindow | None, error: str | None)`
  - `usage.parse_usage(payload: dict) -> UsageSnapshot`（纯函数）
  - `usage.read_auth() -> tuple[str, str | None]`（token, account_id；失败抛 CodexAuthError）
  - `usage.fetch_usage(timeout: float = 8.0) -> UsageSnapshot`（网络异常转为 error 快照，不抛）

- [ ] **Step 1: 写失败测试 `tests/test_usage_parse.py`**

```python
import unittest

from usage import parse_usage

SNAKE = {
    "rate_limit": {
        "primary_window": {"used_percent": 30.0, "window_minutes": 300, "resets_at": 1790000000},
        "secondary_window": {"used_percent": 62.0, "window_minutes": 10080, "resets_at": 1790000000},
    }
}
CAMEL = {
    "rate_limits": {
        "primary": {"usedPercent": 10.0, "windowDurationMins": 300, "resetsAt": 1790000000},
        "secondary": {"usedPercent": 55.0, "limit_window_seconds": 604800, "resetsAt": 1790000000},
    }
}


class TestParseUsage(unittest.TestCase):
    def test_snake_case(self):
        snap = parse_usage(SNAKE)
        self.assertEqual(snap.weekly.used_pct, 62.0)
        self.assertEqual(snap.weekly.window_minutes, 10080)
        self.assertEqual(snap.weekly.resets_at, 1790000000)
        self.assertEqual(snap.five_hour.used_pct, 30.0)
        self.assertIsNone(snap.error)

    def test_camel_case_and_seconds(self):
        snap = parse_usage(CAMEL)
        self.assertEqual(snap.weekly.window_minutes, 10080)
        self.assertEqual(snap.weekly.used_pct, 55.0)
        self.assertEqual(snap.five_hour.used_pct, 10.0)

    def test_empty_rate_limit(self):
        snap = parse_usage({"rate_limit": {}})
        self.assertIsNone(snap.weekly)
        self.assertIsNone(snap.five_hour)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: ERROR（`ModuleNotFoundError: No module named 'usage'`）

- [ ] **Step 3: 实现 `usage.py`**

```python
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


def parse_window(payload) -> Optional[RateWindow]:
    if not isinstance(payload, dict):
        return None
    minutes = _window_minutes(payload)
    used = payload.get("used_percent", payload.get("usedPercent"))
    if minutes is None or used is None:
        return None
    resets = payload.get("resets_at", payload.get("resetsAt"))
    return RateWindow(float(used), minutes, int(resets) if resets is not None else None)


def _classify(minutes: int) -> Optional[str]:
    if 1440 * 5 <= minutes <= 1440 * 8:
        return "weekly"
    if 270 <= minutes <= 330:
        return "five_hour"
    return None


def parse_usage(payload: dict) -> UsageSnapshot:
    """从上游 JSON 提取周窗口与 5h 窗口（键名兼容 snake/camel）。"""
    weekly = five = None
    rate_limit = payload.get("rate_limit") or payload.get("rateLimits") or {}
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: 全部 PASS

- [ ] **Step 5: 真机拉取一次验证（可选但推荐）**

Run: `.venv\Scripts\python.exe -c "from usage import fetch_usage; s=fetch_usage(); print(s)"`
Expected: 打印含 `weekly=RateWindow(used_pct=…, window_minutes=10080, resets_at=…)`；若账号无 5h 窗口则 `five_hour=None`

- [ ] **Step 6: Commit**

```powershell
git add usage.py tests/test_usage_parse.py
git commit -m "feat: codex usage fetch and parse with tests"
```

---

### Task 3: history 本地快照与设置存取（TDD）

**Files:**
- Create: `history.py`、`tests/test_history.py`

**Interfaces:**
- Consumes: `config.DATA_DIR/HISTORY_FILE/SETTINGS_FILE`、`usage.UsageSnapshot`
- Produces:
  - `history.append_sample(snapshot: UsageSnapshot) -> None`（仅 weekly 有效时写一行）
  - `history.load_samples(days: float = 14.0) -> list[tuple[datetime, float]]`（升序，坏行跳过）
  - `history.save_settings(data: dict) -> None` / `history.load_settings() -> dict`

- [ ] **Step 1: 写失败测试 `tests/test_history.py`**

```python
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import history
from usage import RateWindow, UsageSnapshot


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._old = (history.DATA_DIR, history.HISTORY_FILE, history.SETTINGS_FILE)
        history.DATA_DIR = Path(self.tmp.name)
        history.HISTORY_FILE = history.DATA_DIR / "history.jsonl"
        history.SETTINGS_FILE = history.DATA_DIR / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()
        history.DATA_DIR, history.HISTORY_FILE, history.SETTINGS_FILE = self._old

    def test_append_and_load_roundtrip(self):
        snap = UsageSnapshot(
            fetched_at=datetime.now().timestamp(),
            weekly=RateWindow(62.0, 10080, 1790000000),
        )
        history.append_sample(snap)
        samples = history.load_samples()
        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0][1], 62.0)

    def test_append_without_weekly_is_noop(self):
        history.append_sample(UsageSnapshot(fetched_at=0.0, error="x"))
        self.assertEqual(history.load_samples(), [])

    def test_corrupt_lines_skipped(self):
        history.DATA_DIR.mkdir(parents=True, exist_ok=True)
        history.HISTORY_FILE.write_text("not json\n{\"ts\": 1, \"used\": \"x\"}\n", encoding="utf-8")
        self.assertEqual(history.load_samples(), [])

    def test_settings_roundtrip(self):
        history.save_settings({"pos": [100, 200]})
        self.assertEqual(history.load_settings(), {"pos": [100, 200]})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: ERROR（`ModuleNotFoundError: No module named 'history'`）

- [ ] **Step 3: 实现 `history.py`**

```python
"""本地快照与设置存取：~/.codex-quota-widget/ 下 jsonl + json。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List, Tuple

from config import DATA_DIR, HISTORY_FILE, SETTINGS_FILE
from usage import UsageSnapshot


def append_sample(snapshot: UsageSnapshot) -> None:
    """把一次成功拉取的周窗口快照写进 history.jsonl。"""
    if snapshot.weekly is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": snapshot.fetched_at,
        "used": snapshot.weekly.used_pct,
        "resets_at": snapshot.weekly.resets_at,
        "win_min": snapshot.weekly.window_minutes,
    }
    with HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_samples(days: float = 14.0) -> List[Tuple[datetime, float]]:
    """读最近 N 天 (本地时间, used%) 样本，升序；损坏行跳过。"""
    if not HISTORY_FILE.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    out: List[Tuple[datetime, float]] = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            ts = datetime.fromtimestamp(row["ts"])
            if ts >= cutoff:
                out.append((ts, float(row["used"])))
        except (ValueError, KeyError, TypeError, OSError):
            continue
    out.sort()
    return out


def save_settings(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```powershell
git add history.py tests/test_history.py
git commit -m "feat: local snapshot history and settings storage"
```

---

### Task 4: PySide6 悬浮窗 + main 入口

**Files:**
- Create: `widget.py`、`main.py`、`start.cmd`

**Interfaces:**
- Consumes: Task 1–3 全部接口；`config` 的 UI 常量与颜色
- Produces:
  - `widget.QuotaWidget`：`submit(snapshot)`（线程安全入口）、`refresh_callback` 属性（main 注入立即刷新函数）
  - `main.main() -> int`；`main.py` 直接运行即启动悬浮窗 + 5 分钟轮询

- [ ] **Step 1: 实现 `widget.py`**

```python
"""PySide6 悬浮条：折叠为一条小字，悬停展开双口径预估面板。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QDesktopServices, QUrl
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMenu, QProgressBar, QVBoxLayout, QWidget,
)

from config import (
    COLOR_AMBER, COLOR_GREEN, COLOR_RED, COLLAPSED_H, EXPAND_MS,
    PANEL_H, PANEL_W, USAGE_PAGE,
)
from forecast import Forecast, forecast
from history import append_sample, load_samples, load_settings, save_settings
from usage import UsageSnapshot

SHELL_QSS = """
#shell { background: rgba(20,20,26,0.86); border-radius: 10px;
         border: 1px solid rgba(255,255,255,0.10); }
#bar { color: #e8e8ec; font-size: 12px; }
#pct { font-size: 24px; font-weight: 700; }
#countdown { color: #9a9aa4; font-size: 11px; }
#muted { color: #9a9aa4; font-size: 11px; }
#card { background: rgba(255,255,255,0.07); border-radius: 8px; }
#card_title { color: #8f8f9a; font-size: 10px; }
#card_main { color: #e8e8ec; font-size: 13px; font-weight: 600; }
#card_sub { color: #9a9aa4; font-size: 11px; }
#bar_progress { background: rgba(255,255,255,0.12); border: none;
                border-radius: 2px; max-height: 4px; min-height: 4px; }
QMenu { background: #1c1c22; color: #e8e8ec; border: 1px solid #33333c;
        border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 6px; }
QMenu::item:selected { background: #2e2e38; }
"""


def _status_color(remain_pct: float) -> str:
    if remain_pct > 40:
        return COLOR_GREEN
    if remain_pct > 15:
        return COLOR_AMBER
    return COLOR_RED


def _fmt_countdown(resets_ts) -> str:
    if not resets_ts:
        return "重置时间未知"
    secs = max(0, int((datetime.fromtimestamp(resets_ts) - datetime.now()).total_seconds()))
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    return f"距重置 {d}天{h}时" if d else f"距重置 {h}时{r // 60}分"


class QuotaWidget(QWidget):
    updated = Signal(object)  # submit → emit → 队列投递到 UI 线程

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._snapshot: Optional[UsageSnapshot] = None
        self._forecast: Optional[Forecast] = None
        self._expanded = False
        self._press_pos = None
        self._dragging = False
        self.refresh_callback = None  # main 注入：立即刷新
        self._build_ui()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(EXPAND_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_size_anim)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(250)
        self._collapse_timer.timeout.connect(self._try_collapse)
        self.updated.connect(self._on_snapshot)
        self._restore_pos()
        self._apply_state()

    # ---- UI 构建 ----
    def _build_ui(self) -> None:
        shell = QFrame(self, objectName="shell")
        shell.setStyleSheet(SHELL_QSS)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(shell)
        lay = QVBoxLayout(shell)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(6)

        self.bar = QLabel("加载中…", objectName="bar")
        lay.addWidget(self.bar)

        self.panel = QFrame(objectName="panel")
        lay.addWidget(self.panel)
        pl = QVBoxLayout(self.panel)
        pl.setContentsMargins(2, 2, 2, 2)
        pl.setSpacing(6)

        head = QHBoxLayout()
        self.pct = QLabel("--", objectName="pct")
        self.countdown = QLabel("", objectName="countdown")
        head.addWidget(self.pct)
        head.addStretch(1)
        head.addWidget(self.countdown, 0, Qt.AlignTop)
        pl.addLayout(head)

        self.progress = QProgressBar(objectName="bar_progress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        pl.addWidget(self.progress)

        cards = QHBoxLayout()
        cards.setSpacing(6)
        self.wd_card = self._make_card("工作日节奏 · 周一~五")
        self.ed_card = self._make_card("每天都在用 · 含周末")
        cards.addWidget(self.wd_card)
        cards.addWidget(self.ed_card)
        pl.addLayout(cards)

        self.extra = QLabel("", objectName="muted")
        self.footer = QLabel("", objectName="muted")
        pl.addWidget(self.extra)
        pl.addWidget(self.footer)

        self.panel.hide()
        self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _make_card(self, title: str) -> QFrame:
        card = QFrame(objectName="card")
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(QLabel(title, objectName="card_title"))
        card.main_label = QLabel("--", objectName="card_main")
        card.sub_label = QLabel("", objectName="card_sub")
        v.addWidget(card.main_label)
        v.addWidget(card.sub_label)
        return card

    def _bar_width(self) -> int:
        return max(96, self.bar.sizeHint().width() + 28)

    # ---- 位置与生命周期 ----
    def _restore_pos(self) -> None:
        pos = load_settings().get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(int(pos[0]), int(pos[1]))
        else:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.right() - 150, g.bottom() - 80)

    def closeEvent(self, event) -> None:
        save_settings({"pos": [self.x(), self.y()]})
        super().closeEvent(event)

    # ---- 展开 / 收起 ----
    def enterEvent(self, event) -> None:
        self._expand()

    def leaveEvent(self, event) -> None:
        self._collapse_timer.start()

    def _expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self._collapse_timer.stop()
        self.panel.show()
        self._start_anim(1.0)
        self.raise_()

    def _try_collapse(self) -> None:
        if self.underMouse() or not self._expanded:
            return
        self._expanded = False
        self.panel.hide()
        self._start_anim(0.0)

    def _start_anim(self, end: float) -> None:
        geo = self.geometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = max(screen.left(), min(geo.x(), screen.right() - PANEL_W + 1))
        self._anchor = (x, geo.y())
        self._anim.stop()
        self._anim.setStartValue(1.0 - end)
        self._anim.setEndValue(end)
        self._anim.start()

    def _on_size_anim(self, v) -> None:
        p = float(v)
        wc = self._bar_width()
        w = int(wc + (PANEL_W - wc) * p)
        h = int(COLLAPSED_H + (PANEL_H - COLLAPSED_H) * p)
        self.setGeometry(self._anchor[0], self._anchor[1], w, h)

    # ---- 拖动 / 点击 / 菜单 ----
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press_pos = (e.globalPosition().toPoint(), self.pos())
            self._dragging = False

    def mouseMoveEvent(self, e) -> None:
        if self._press_pos is None:
            return
        gpos, origin = self._press_pos
        delta = e.globalPosition().toPoint() - gpos
        if self._dragging or delta.manhattanLength() > 4:
            self._dragging = True
            self._anim.stop()
            self.move(origin + delta)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and not self._dragging:
            QDesktopServices.openUrl(QUrl(USAGE_PAGE))
        self._press_pos = None
        self._dragging = False

    def contextMenuEvent(self, e) -> None:
        m = QMenu(self)
        m.setStyleSheet(SHELL_QSS)
        if self.refresh_callback:
            m.addAction("立即刷新", self.refresh_callback)
        m.addAction("打开用量页", lambda: QDesktopServices.openUrl(QUrl(USAGE_PAGE)))
        m.addSeparator()
        m.addAction("退出", QApplication.quit)
        m.exec(e.globalPos())

    # ---- 数据 ----
    def submit(self, snapshot: UsageSnapshot) -> None:
        self.updated.emit(snapshot)  # 任意线程可调用

    def _on_snapshot(self, snap: UsageSnapshot) -> None:
        if snap.weekly is not None:
            append_sample(snap)
            self._snapshot = snap
        elif self._snapshot is None:
            self._snapshot = snap
        else:  # 拉取失败：保留上次数据，带上错误标记
            self._snapshot = UsageSnapshot(
                fetched_at=self._snapshot.fetched_at,
                weekly=self._snapshot.weekly,
                five_hour=self._snapshot.five_hour,
                error=snap.error,
            )
        self._apply_state()

    def _set_bar(self, text: str, color: str) -> None:
        self.bar.setText(text)
        self.bar.setStyleSheet(f"color: {color};")

    def _apply_state(self) -> None:
        snap = self._snapshot
        if snap is None:
            self._set_bar("加载中…", "#8a8a94")
            return
        if snap.weekly is None:
            self._set_bar("● 未登录", COLOR_RED)
            self.footer.setText("未登录：请先用 Codex CLI 登录")
            return

        w = snap.weekly
        remain = 100.0 - w.used_pct
        color = "#8a8a94" if snap.error else _status_color(remain)
        err = " ·未更新" if snap.error else ""
        if w.resets_at:
            days = max((datetime.fromtimestamp(w.resets_at) - datetime.now()).total_seconds() / 86400, 0.0)
            self._set_bar(f"● {remain:.0f}%  {days:.1f}d{err}", color)
        else:
            self._set_bar(f"● {remain:.0f}%{err}", color)

        self.pct.setText(f"{remain:.0f}%")
        self.pct.setStyleSheet(f"color: {color};")
        self.progress.setValue(int(remain))
        self.progress.setStyleSheet(
            f"#bar_progress::chunk {{ background: {color}; border-radius: 2px; }}")
        self.countdown.setText(_fmt_countdown(w.resets_at))

        self._forecast = None
        if w.resets_at:
            self._forecast = forecast(
                w.used_pct,
                datetime.fromtimestamp(w.resets_at),
                w.window_minutes,
                history_samples=load_samples(),
            )
        if self._forecast:
            f = self._forecast
            self._fill_card(self.wd_card, f.working_day, f.remaining_wd_h, "个工作日",
                            "" if f.fallback_wd else "按历史速率")
            self._fill_card(self.ed_card, f.every_day, f.remaining_all_h, "天", "")
        extra = (f"5h 窗口剩余 {100 - snap.five_hour.used_pct:.0f}%"
                 if snap.five_hour else "")
        self.extra.setText(extra)
        self.extra.setVisible(bool(extra))
        ts = datetime.fromtimestamp(snap.fetched_at).strftime("%H:%M")
        self.footer.setText(f"更新 {ts}" + (f" · {snap.error}" if snap.error else ""))
        if not self._expanded:
            self.setGeometry(self.x(), self.y(), self._bar_width(), COLLAPSED_H)

    def _fill_card(self, card, sc, remaining_h: float, unit: str, tag: str) -> None:
        if sc.end_remaining_pct >= 0:
            card.main_label.setText(f"结余 {sc.end_remaining_pct:.0f}%")
            card.main_label.setStyleSheet(f"color: {COLOR_GREEN};")
        else:
            ahead = max(0.0, remaining_h - sc.hours_left)
            text = f"超 {-sc.end_remaining_pct:.0f}%"
            if ahead > 0:
                text += f" ·提前{ahead:.0f}h用完"
            card.main_label.setText(text)
            card.main_label.setStyleSheet(f"color: {COLOR_RED};")
        sub = f"还能撑 {sc.days_left:.1f} {unit}"
        if tag:
            sub += f" · {tag}"
        card.sub_label.setText(sub)
```

- [ ] **Step 2: 实现 `main.py`**

```python
"""入口：启动悬浮窗与后台轮询线程。"""
from __future__ import annotations

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
    widget.submit(snap)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Codex Quota Widget")
    app.setFont(QFont("Microsoft YaHei UI", 9))
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
```

- [ ] **Step 3: `start.cmd`**

```bat
@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "main.py"
) else (
  start "" pythonw "main.py"
)
```

- [ ] **Step 4: 冒烟验证**

```powershell
.venv\Scripts\python.exe -c "import widget, main; print('import ok')"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
Expected: `import ok`；全部测试 PASS。

再启动真实窗口（非阻塞运行 5 秒后停止，确认无 crash.log、无异常输出）：

```powershell
.venv\Scripts\python.exe main.py
```
Expected: 屏幕右下角出现小条并显示真实额度；无 stderr 输出、无 crash.log 生成。若面板文字被裁剪，微调 `config.PANEL_W/PANEL_H` 或 `widget.py` 中的 margins/字号后重跑本步。

- [ ] **Step 5: Commit**

```powershell
git add widget.py main.py start.cmd
git commit -m "feat: hover-expand quota widget with dual-scenario panel"
```

---

### Task 5: 安装/自启脚本 + 全量验证

**Files:**
- Create: `install.cmd`、`install-startup.cmd`、`uninstall-startup.cmd`

**Interfaces:**
- Consumes: Task 4 的 `.venv` 与 `start.cmd`
- Produces: 双击 `install.cmd` 完成建 venv→装依赖→注册自启；`uninstall-startup.cmd` 移除自启

- [ ] **Step 1: 写三个脚本**

`install.cmd`:
```bat
@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 未找到 Python，请先安装 Python 3.10+
  pause & exit /b 1
)
if not exist ".venv" python -m venv .venv
call ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo [OK] 依赖安装完成
call install-startup.cmd
```

`install-startup.cmd`:
```bat
@echo off
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\CodexQuotaWidget.lnk'); $lnk.TargetPath = '%~dp0start.cmd'; $lnk.WorkingDirectory = '%~dp0'; $lnk.Save()"
echo [OK] 已注册开机自启
pause
```

`uninstall-startup.cmd`:
```bat
@echo off
powershell -NoProfile -Command "Remove-Item ([Environment]::GetFolderPath('Startup') + '\CodexQuotaWidget.lnk') -ErrorAction SilentlyContinue"
echo [OK] 已移除开机自启
pause
```

- [ ] **Step 2: 全量回归**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```
Expected: 全部 PASS

- [ ] **Step 3: Commit**

```powershell
git add install.cmd install-startup.cmd uninstall-startup.cmd
git commit -m "chore: install and startup scripts"
```
