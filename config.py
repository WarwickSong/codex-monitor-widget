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
COLLAPSED_H = 32              # 折叠胶囊高度 px
PANEL_W, PANEL_H = 312, 224   # 展开面板尺寸 px
EXPAND_MS = 150               # 展开/收起动画时长

COLOR_GREEN = "#34d399"
COLOR_AMBER = "#fbbf24"
COLOR_RED = "#f87171"
