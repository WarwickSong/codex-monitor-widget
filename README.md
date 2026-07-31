# Codex Monitor Widget

> 你的 SSD，你自己守护。

Codex 每小时向 `logs_2.sqlite` 写入数千条调试日志。你的 NVMe 被消耗的不是 TBW，是**信任**。

**双击安装。3 秒见效。**

```text
触发器完整 · 定义 ✓ · 哈希 467857e8
日志增长 0 · WAL 安静 · 已守护
额度剩余 78% · 8 天后重置
```

---

## 它做了什么

| 效果 | 说明 |
|------|------|
| **阻止日志落盘** | 在 `logs_2.sqlite` 安装 `RAISE(IGNORE)` 触发器，丢弃所有日志写入，不含持久计数副作用 |
| **防篡改检测** | 每秒校验触发器 SQL 结构 + SHA-256；被覆盖/改错/加条件 → 自动恢复 + 红色告警 |
| **写入泄漏发现** | `logs` 行数一旦增长，立即判定"写入泄漏" |
| **额度早知道** | 从你的 Codex 登录态拉取剩余额度和重置时间；拉不到就显示本机累计 |
| **纯只读采样** | 监控不写数据库（除非触发器被篡改才写入一次修复 DDL） |

---

## 安装

前提：Codex 已安装且至少启动过一次。

```
双击 install.cmd
```

自动：找 Python → 装 PySide6 → 可选开机自启 → 启动悬浮窗。

---

## 日常

| 操作 | 文件 |
|------|------|
| 启动 | 双击 `start-monitor.cmd` |
| 开机自启 | 双击 `install-startup.cmd` |
| 移除自启 | 双击 `uninstall-startup.cmd` |

---

## 工作方式

```text
               每秒（只读）
start  ──────────────────→ 读取触发器 SQL
                                   │
              ┌────────────────────┤
              ▼                    ▼
        定义 ✓？             哈希变了？
              │                    │
        ┌─非─┤              ┌─是─→ 红色告警
        ▼    ▼              │
   DROP+CREATE           ┌──┘
   修复触发器             │
        │                 ▼
        ▼            哈希一致
    重采样 → 正常监控循环

同时：
  每 5 分钟 → 拉取远端额度
  退出时    → 记住窗口位置
```

---

## 校验

| 校验层 | 说明 | 频率 |
|--------|------|------|
| 定义校验 | 正则匹配 `BEFORE INSERT ON logs` + 无 `WHEN` + 含 `RAISE(IGNORE)` + 无额外写语句 | 每秒 |
| 哈希校验 | SHA-256 前 8 位，字符级变化立即报警 | 每秒 |
| 日志泄漏 | 监控 `MAX(id)`，增长即判定未拦截 | 每秒 |

---

## 隐私

- 监控连接以 `mode=ro` 只读打开，不修改数据库
- 仅在触发器被破坏后执行一次修复 DDL
- 额度请求使用你已登录的 Codex 令牌，令牌不写日志或界面
- "本机累计"来自本地 `state_5.sqlite`，非远端订阅额度

---

## 文件

```
main.py                入口，崩溃日志 → crash.log
widget.py              PySide6 悬浮窗
monitor.py             采样、触发器校验、自愈
usage.py               额度获取
verify_env.py          环境自检
requirements.txt       PySide6 + requests
install.cmd            一键安装
start-monitor.cmd      日常启动（pythonw.exe）
install-startup.cmd    开机自启
uninstall-startup.cmd  移除自启
tests/                 单元测试
```

不要提交 `__pycache__/`、`.venv/`、`*.log`、`crash.log`。

---

## 迁移

复制整个文件夹到目标机器 → 确保 Codex 已装并启动过一次 → 双击 `install.cmd`。

仅支持 Windows。macOS 用户见 [Handoff-macOS-Codex-SSD-Fix.md](Handoff-macOS-Codex-SSD-Fix.md)。

---

## 验证

```powershell
python verify_env.py
python -m unittest discover -s tests -v
```
