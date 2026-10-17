# Codex Quota Widget — 设计 Spec

日期：2026-10-17
状态：已与用户确认（方案 A + 双口径预估 + 美观优先）

## 1. 目标

一个 Windows 桌面极简悬浮条，常驻屏幕角落：

- 实时显示 Codex（ChatGPT 订阅）周限额剩余百分比
- 悬停展开，给出两种使用节奏下的预估分析（均按当前速率、锚定下次重置 `resets_at`）：
  - **工作日口径**：只在周一至周五使用 → 到重置时结余/超支多少；还能撑几个工作日
  - **7 天口径**：每天（含周末）都使用 → 到重置时结余/超支多少；还能撑几天
- 折叠态即全部占用空间（一条小字条），展开不占额外固定空间

非目标：不做 SSD 日志保护（参考仓库的主体功能）；不做多账号；不做 macOS。

## 2. 数据源

复用 `codex-monitor-widget` 的取数思路：

- 读取 `~/.codex/auth.json` → `tokens.access_token`、`tokens.account_id`
- `GET https://chatgpt.com/backend-api/wham/usage`
  - Headers: `Authorization: Bearer <token>`、`Accept: application/json`、可选 `ChatGPT-Account-ID`
- 解析 `rate_limit`/`rate_limits`：
  - `primary_window|primary`（5h 窗口，Pro 若无则不显示）
  - `secondary_window|secondary`（周窗口，主口径）
  - 每窗口取 `used_percent`、`window_minutes`（兼容 `windowDurationMins`/`limit_window_seconds`）、`resets_at`（兼容 `resetsAt`，Unix 秒）
- 轮询间隔默认 5 分钟（`config.py` 可调）

## 3. 项目结构

```
D:\Code\private\codex-quota-widget\
├── main.py            入口：装 QT 事件循环、启动刷新线程
├── config.py          常量：轮询间隔、回退权重、UI 尺寸/配色
├── usage.py           额度获取与解析（纯函数解析 + IO 分离）
├── forecast.py        预估算法（纯函数，无 IO）
├── history.py         本地快照存档 ~/.codex-quota-widget/history.jsonl
├── widget.py          PySide6 悬浮条 UI
├── requirements.txt   PySide6, requests
├── start.cmd          双击启动（pythonw 静默）
├── install-startup.cmd / uninstall-startup.cmd
├── tests/             forecast / usage 解析单测
└── docs/              本 spec
```

## 4. 预估算法（forecast.py，纯函数）

记号：`used`（%）、`resets_at`、`win_min`；`window_start = resets_at - win_min*60`。

- `elapsed_all_h` = now−window_start（小时）；`rate_all = used / elapsed_all_h`（%/h）
- `elapsed_wd_h` = [window_start, now] 内的工作日小时数（本地时区，周一至周五全天）
- **工作日速率**：
  - 本地历史覆盖 ≥ 3 天：按增量发生时段归属到工作日/周末，`rate_wd = Σ工作日消耗% / elapsed_wd_h`
  - 历史不足（回退）：假设消耗全部发生在工作日 → `rate_wd = used / elapsed_wd_h`（保守上界）
- `remaining_all_h` = resets_at−now；`remaining_wd_h` = [now, resets_at] 内工作日小时数
- **工作日口径**：到期结余 = `(100−used) − rate_wd × remaining_wd_h`
- **7 天口径**：到期结余 = `(100−used) − rate_all × remaining_all_h`
- 结余 < 0 显示为「超 x%」并换算「预计提前 H 小时用完」
- 还能撑：`N个工作日 = (100−used)/rate_wd/24`；`N天 = (100−used)/rate_all/24`

已知简化（记录在案）：Codex 周窗口为滚动窗口，本模型视 `resets_at` 为硬重置点、不计算旧消耗过期归还，因此预估偏保守，适合回答「够不够」。

## 5. UI（widget.py，美观优先）

折叠态（默认）：

- 无边框、置顶、跳过任务栏、深色半透明圆角条（约 26px 高、宽度自适应）
- 内容：状态点 + `62%` + 到重置 `4.1d`；配色随余额：>40% 绿 / 15–40% 琥珀 / <15% 红
- `FramelessWindowHint + WA_TranslucentBackground`，退出时位置写入 `~/.codex-quota-widget/settings.json`

悬停展开（150ms 尺寸动画，移开自动收回）：

- 约 280×180 深色玻璃质感面板（QSS 圆角、细边框、分隔线）
- 顶部：周剩余大字 + 细进度条；重置倒计时（`2天14时`）
- 中部两块预估卡并排：
  - 「工作日节奏」：到期结余/超支 x% · 还能撑 N 个工作日
  - 「每天都在用」：到期结余/超支 y% · 还能撑 N 天
- 5h 窗口行（有数据才显示）；底部小字：最后更新时间 / 更新失败标记

交互：悬停展开；左键拖动；右键菜单（立即刷新 / 打开用量页 / 退出）；点击面板打开 `https://chatgpt.com/codex/settings/usage`。

## 6. 错误处理

- API 失败：保留上次数据，折叠态变灰，面板标「更新失败 · HH:MM」
- `auth.json` 缺失/无效：折叠态显示「未登录」
- 快照解析失败：同 API 失败处理；异常不崩 UI（刷新线程兜底 try/except）

## 7. 测试

- `forecast`：合成窗口序列（跨周末边界、历史充足/不足两分支）验证 rate、结余、还能撑
- `usage` 解析：mock 上游 JSON 的多种键名变体（snake/camel）
- 运行方式：`python -m unittest discover -s tests -v`

## 8. 安装与启动

- `pip install -r requirements.txt`（或双击 `install.cmd` 一键：建 venv→装依赖→注册自启）
- 日常双击 `start.cmd`（pythonw 静默启动）
