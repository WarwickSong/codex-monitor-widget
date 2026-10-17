"""本地快照与设置存取：~/.codex-quota-widget/ 下 jsonl + json。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import List, Tuple

from config import DATA_DIR, HISTORY_FILE, SETTINGS_FILE
from usage import UsageSnapshot


def append_sample(snapshot: UsageSnapshot) -> None:
    """把一次成功拉取的周窗口快照写进 history.jsonl（失败安全，不抛）。"""
    if snapshot.weekly is None:
        return
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": snapshot.fetched_at,
            "used": snapshot.weekly.used_pct,
            "resets_at": snapshot.weekly.resets_at,
            "win_min": snapshot.weekly.window_minutes,
        }
        with HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass  # 写入失败只降级预估精度，不影响 UI


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
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 记不住位置就用默认位置


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
