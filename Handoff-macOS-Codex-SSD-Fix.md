# Handoff: Codex 高频写盘问题 — macOS 修复与监控指南

> 本文档由 Windows 机器上的工作整理，交接给 macOS 机器上的 Codex 执行。
> 问题已在 Windows 端确认、复现、修复并验证。macOS 端需做同样的检测与修复。

---

## 一、问题背景

### 是什么问题

OpenAI Codex（CLI / 桌面端）存在一个**已被 OpenAI 认领的 bug**（GitHub Issue #17320 / #28224 / #30322 等）：

Codex 默认把 **TRACE 级网络日志**（HTTP/2 帧、SSE 流式响应、token delta、工具调用等）持续写入本地 SQLite 数据库：

```
~/.codex/logs_2.sqlite
~/.codex/logs_2.sqlite-wal
~/.codex/logs_2.sqlite-shm
```

### 为什么严重

- **写入量惊人**：社区实测 21 天可产生约 **37TB** 写入量，年化约 **640TB**——超过主流 1TB 消费级 SSD 的标称全生命周期写入寿命（约 600TBW）
- **绕过用户配置**：即使用户设了 `RUST_LOG=warn`，SQLite 日志层有自己独立的过滤链，默认 `Level::TRACE`，照写不误。`RUST_LOG` 只影响终端输出
- **文件大小是假象**：Codex 定期删旧行（insert-prune 循环），文件看着只有几百 MB，但 SSD 背后已经吞掉了巨量写入
- **写放大**：SQLite WAL、checkpoint、索引更新、文件系统元数据都会放大真实写入量

### Windows 端已确认的事实（macOS 大概率相同）

- `logs` 表中 **TRACE 占 80%**（7091/8908 行）
- 写入量最大的来源全是 HTTP/2 传输层：`codex_api::sse::responses`、`h2::proto::streams::recv`、`h2::codec::framed_read` 等
- 表内行数 `COUNT(*)` = 8,908，但 `MAX(id)` = 1,328,321——差 130 多万，证明累计已写入约 130 万行又删掉
- Codex 干活时实测写入速率 **289 行/秒**
- 单条日志最大可达 **~50KB**（`estimated_bytes` 字段）

---

## 二、macOS 检测步骤

### 2.1 检查文件是否存在

```bash
ls -lh ~/.codex/logs_2.sqlite*
```

如果看到 `logs_2.sqlite` + `-wal` + `-shm` 三个文件，说明存在同样的问题。

### 2.2 检查日志级别分布

```bash
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT level, COUNT(*) AS n FROM logs GROUP BY level ORDER BY n DESC;"
```

如果 TRACE 行数占绝大多数，确认中招。

### 2.3 检查写入来源

```bash
sqlite3 ~/.codex/logs_2.sqlite <<'SQL'
SELECT target, level, COUNT(*) AS n
FROM logs
GROUP BY target, level
ORDER BY n DESC
LIMIT 20;
SQL
```

如果顶部全是 `codex_api::sse::responses`、`h2::proto::*`、`codex_http_client::*` 等 TRACE 日志，确认是同一个 bug。

### 2.4 检查写放大（关键证据）

```bash
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT COUNT(*) AS current_rows, COALESCE(MAX(id),0) AS max_id FROM logs;"
```

如果 `max_id` 远大于 `current_rows`（比如差几十万），说明 Codex 累计写入了大量日志又删掉，SSD 承受了巨量写入。

### 2.5 实时采样（15 秒）

**在 Codex 正在干活时运行**（空闲时测不出问题）：

```bash
# 记录初始值
T0_ROWS=$(sqlite3 ~/.codex/logs_2.sqlite "SELECT COUNT(*) FROM logs;")
T0_MAXID=$(sqlite3 ~/.codex/logs_2.sqlite "SELECT COALESCE(MAX(id),0) FROM logs;")
T0_WAL=$(stat -f%z ~/.codex/logs_2.sqlite-wal 2>/dev/null || echo 0)
sleep 15
T1_ROWS=$(sqlite3 ~/.codex/logs_2.sqlite "SELECT COUNT(*) FROM logs;")
T1_MAXID=$(sqlite3 ~/.codex/logs_2.sqlite "SELECT COALESCE(MAX(id),0) FROM logs;")
T1_WAL=$(stat -f%z ~/.codex/logs_2.sqlite-wal 2>/dev/null || echo 0)

echo "15秒内新增行数: $((T1_MAXID - T0_MAXID))"
echo "WAL 增量: $((T1_WAL - T0_WAL)) 字节"
echo "平均速率: $(echo "scale=1; ($T1_MAXID - $T0_MAXID) / 15" | bc) 行/秒"
```

### 2.6 检查 SSD 已损耗写入量

```bash
# 安装 smartmontools（如果没有）
brew install smartmontools

# 查看 SSD 总写入量
smartctl -a /dev/disk0 | grep -i "total bytes written"
# 或
smartctl -a /dev/disk0 | grep -i "data units written"
```

对比 SSD 标称 TBW，可估算剩余寿命百分比。

---

## 三、修复方案

### 方案优先级（macOS 推荐）

| 优先级 | 方案 | 说明 |
|--------|------|------|
| ⭐ 推荐 | 方案 C：sqlite_home 迁移到 RAM disk | macOS 原生支持 RAM disk（hdiutil），最彻底 |
| 临时 | 方案 A：SQLite Trigger 拦截 | 立即止血，但升级可能失效 |
| 不推荐 | 方案 B：关 analytics | 几乎无效 |

### 方案 A：SQLite Trigger 立即止血（先做这个）

**先完全退出 Codex**，然后：

```bash
# 1. 确认 Codex 已退出
pgrep -fl 'codex|Codex|app-server'
lsof ~/.codex/logs_2.sqlite 2>/dev/null

# 2. 备份
BACKUP=~/.codex/logs-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
cp ~/.codex/logs_2.sqlite* "$BACKUP/"

# 3. checkpoint WAL
sqlite3 ~/.codex/logs_2.sqlite "PRAGMA wal_checkpoint(TRUNCATE);"

# 4. 创建触发器（macOS 的 sqlite3 支持 heredoc，不会像 PowerShell 那样吞换行）
sqlite3 ~/.codex/logs_2.sqlite <<'SQL'
CREATE TRIGGER IF NOT EXISTS block_log_inserts
BEFORE INSERT ON logs
BEGIN
  SELECT RAISE(IGNORE);
END;
SQL

# 5. 验证触发器存在
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT name FROM sqlite_master WHERE type='trigger' AND name='block_log_inserts';"

# 6. 测试拦截（插入应该被拦住，行数不变）
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT COUNT(*) AS before_count FROM logs;"
sqlite3 ~/.codex/logs_2.sqlite \
  "INSERT INTO logs(ts, ts_nanos, level, target) VALUES(2000000000, 0, 'TRACE', 'trigger_test');"
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT COUNT(*) AS after_count FROM logs;"
# 如果 before_count == after_count，说明拦截成功

# 7. VACUUM 压缩历史残留
sqlite3 ~/.codex/logs_2.sqlite "VACUUM;"

# 8. 确认触发器在 VACUUM 后还在
sqlite3 ~/.codex/logs_2.sqlite \
  "SELECT name FROM sqlite_master WHERE type='trigger' AND name='block_log_inserts';"
```

**方案 A 的效果与代价**：
- ✅ SSD 写入归零（触发器拦住所有 INSERT）
- ✅ CPU/句柄开销可忽略，不伤硬件（只有 SSD 有写入寿命问题）
- ⚠️ Codex 升级后触发器可能被重建的数据库冲掉，需重加
- ⚠️ 日志表不再有新数据，无法用 sqlite3 查历史日志排查问题（会话记录不受影响，在 `sessions/` 目录）
- ⚠️ 非官方做法，万一 Codex 逻辑依赖插入成功可能有未知行为

**重建触发器命令**（升级后重加用）：
```bash
sqlite3 ~/.codex/logs_2.sqlite <<'SQL'
DROP TRIGGER IF EXISTS block_log_inserts;
CREATE TRIGGER block_log_inserts
BEFORE INSERT ON logs
BEGIN
  SELECT RAISE(IGNORE);
END;
SQL
```

### 方案 C：迁移到 RAM disk（macOS 推荐，最彻底）

macOS 原生支持 RAM disk（用 `hdiutil`），比 Windows 方便得多。这是**最推荐的长期方案**——写入全在内存，SSD 完全不受影响。

```bash
# 1. 创建约 3 GiB RAM Disk
DISK=$(hdiutil attach -nomount ram://6291456 | awk 'NR==1 {print $1}')
diskutil erasevolume HFS+ CodexRAM "$DISK"
mkdir -p /Volumes/CodexRAM/codex-sqlite

# 2. 如果已有 logs_2.sqlite，先 checkpoint 并复制过去
sqlite3 ~/.codex/logs_2.sqlite "PRAGMA wal_checkpoint(TRUNCATE);"
cp ~/.codex/logs_2.sqlite* /Volumes/CodexRAM/codex-sqlite/

# 3. 在 ~/.codex/config.toml 中配置 sqlite_home
cat >> ~/.codex/config.toml <<'TOML'
sqlite_home = "/Volumes/CodexRAM/codex-sqlite"
TOML

# 4. 启动 Codex，它会读写 RAM disk 而非 SSD
codex

# 5. 用完后卸载 RAM disk（重启也会自动消失）
hdiutil detach /Volumes/CodexRAM
```

**方案 C 的效果与代价**：
- ✅ 写入完全不碰 SSD，最彻底
- ✅ 官方支持的配置项（`sqlite_home`）
- ⚠️ RAM disk 重启后清空（本地诊断日志丢失，会话记录不在 SQLite 里不受影响）
- ⚠️ `sqlite_home` 会迁移**所有** SQLite 状态（state、logs、goals、memories），不只是日志
- ⚠️ RAM disk 容量要给够（建议 3GB+）

**更完善的方案**：社区有人写了 RAM disk 往返脚本——运行时日志写 RAM disk，退出 Codex 后 checkpoint + 裁剪 + VACUUM 压缩 + 回写到 SSD。参考：https://www.cnblogs.com/notlate-cn/p/21439874

### 无效方案（别浪费时间）

| 方案 | 为什么无效 |
|------|-----------|
| `RUST_LOG=warn` | 只影响终端输出，SQLite 日志层独立过滤，TRACE 照写 |
| `[feedback] enabled = false` | 只禁反馈上传，不挡本地写入 |
| `[analytics] enabled = false` | 只禁分析数据上传，不挡本地写入 |
| 定期删旧行 + VACUUM | 能回收文件空间，但不阻止新写入，SSD 写放大依然存在 |
| 软链到 `/tmp` | macOS 的 `/tmp` 不是 tmpfs，在 SSD 上，无意义 |

### 安全清理已有日志

```bash
# ⚠️ 不要直接删 ~/.codex 整个目录——里面有登录凭据和配置
# 只删日志文件（先确认 Codex 已退出）

# 1. 确认 Codex 已退出
pgrep -fl 'codex|Codex|app-server'
lsof ~/.codex/logs_2.sqlite 2>/dev/null

# 2. 备份后移除
BACKUP=~/.codex/logs-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP"
mv ~/.codex/logs_2.sqlite* "$BACKUP/"
echo "已备份到 $BACKUP"

# 3. Codex 下次启动会自动重建空的 logs_2.sqlite
# 4. 重建后再加触发器（见方案 A）
```

如果 Time Machine 在备份 `~/.codex`，记得排除 WAL 文件：
```bash
# 在 Time Machine 排除项中加入
~/.codex/logs_2.sqlite-wal
~/.codex/logs_2.sqlite-shm
```

---

## 四、监控方案

### 4.1 简单检测脚本（bash 版）

```bash
#!/bin/bash
# codex_write_check.sh — 15秒采样 Codex 写入速率
# 用法：在 Codex 干活时运行

DB=~/.codex/logs_2.sqlite
WAL=~/.codex/logs_2.sqlite-wal

echo "===== 15秒写入采样 ====="
T0_MAXID=$(sqlite3 "$DB" "SELECT COALESCE(MAX(id),0) FROM logs;" 2>/dev/null)
T0_WAL=$(stat -f%z "$WAL" 2>/dev/null || echo 0)
T0_ROWS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM logs;" 2>/dev/null)
TRIGGER=$(sqlite3 "$DB" "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name='block_log_inserts';" 2>/dev/null)

echo "T0: rows=$T0_ROWS maxId=$T0_MAXID wal=${T0_WAL}B trigger=$([ "$TRIGGER" -gt 0 ] && echo '已生效' || echo '未生效')"

sleep 15

T1_MAXID=$(sqlite3 "$DB" "SELECT COALESCE(MAX(id),0) FROM logs;" 2>/dev/null)
T1_WAL=$(stat -f%z "$WAL" 2>/dev/null || echo 0)
T1_ROWS=$(sqlite3 "$DB" "SELECT COUNT(*) FROM logs;" 2>/dev/null)

echo "T1: rows=$T1_ROWS maxId=$T1_MAXID wal=${T1_WAL}B"
echo ""
echo "15秒新增行数: $((T1_MAXID - T0_MAXID))"
echo "WAL 增量: $((T1_WAL - T0_WAL)) 字节"
echo "平均速率: $(echo "scale=1; ($T1_MAXID - $T0_MAXID) / 15" | bc) 行/秒"
```

### 4.2 桌面悬浮窗（Python + PySide6）

Windows 端已写好骨架代码，在 `codex-monitor-widget/` 文件夹内。macOS 需要做的适配：

1. **进程检测**：`pgrep -fl codex` 替代 `tasklist`
2. **WAL 文件大小**：`os.path.getsize()` 跨平台通用，无需改
3. **SQLite 路径**：`~/.codex/logs_2.sqlite` 跨平台通用
4. **PySide6 安装**：`pip install PySide6`（macOS 原生支持）
5. **窗口置顶**：`Qt.WindowStaysOnTopHint` 跨平台通用
6. **auth.json**：同样在 `~/.codex/auth.json`，有 `tokens` 字段

骨架代码文件：
- `README.md` — 完整任务说明 + 验收标准
- `monitor.py` — 磁盘监控采样（`is_codex_running()` 需改为 pgrep）
- `usage.py` — 用量获取（方案2 auth.json token + 降级本地估算）
- `widget.py` — PySide6 悬浮窗 UI
- `main.py` — 入口
- `verify_env.py` — 环境自检

**macOS 需改动的代码**（`monitor.py` 的 `is_codex_running()`）：
```python
def is_codex_running() -> bool:
    """macOS: 用 pgrep 检测 codex 进程"""
    try:
        subprocess.check_output(["pgrep", "-fl", "codex"],
                                stderr=subprocess.DEVNULL, timeout=3)
        return True
    except subprocess.CalledProcessError:
        return False  # pgrep 没匹配到返回非 0
    except Exception:
        return False
```

### 4.3 用量显示

ChatGPT Plus / Codex 的剩余额度**没有公开 API**。调研结论：

- API key 余额 ≠ Plus 订阅用量，两者完全独立
- `backend-api` 非官方、被 Cloudflare 保护、无成熟开源项目
- Codex 专属 usage 页面：`https://chatgpt.com/codex/settings/usage`（显示每 5h + 每周额度）
- `~/.codex/auth.json` 有 `tokens` 字段（keys: `auth_mode`, `OPENAI_API_KEY`, `tokens`, `last_refresh`），可能含 access_token，可尝试请求 usage 页

**降级方案**（必做）：
- 从 `~/.codex/state_5.sqlite` 的 `threads` 表汇总 `tokens_used`（今日/本周/累计）
- 明确标注"本地估算，非官方剩余"
- 一键跳转按钮打开 `https://chatgpt.com/codex/settings/usage`

---

## 五、Windows 端已完成的操作记录

供参考，macOS 端执行时可对照：

| 步骤 | Windows 状态 | macOS 对应 |
|------|-------------|-----------|
| 检测确认中招 | ✅ TRACE 80%，maxId 差 130 万 | 需执行第二节检测 |
| 备份日志文件 | ✅ `logs-backup-20260721-230313` | `cp ~/.codex/logs_2.sqlite* <backup>/` |
| 创建触发器 | ✅ 已生效 | `sqlite3 <<'SQL'` heredoc（macOS 不会吞换行） |
| 验证拦截 | ✅ maxId 不变，测试行 0 行 | 同上 |
| VACUUM 压缩 | ✅ 13.48MB → 8.41MB | 同上 |
| 网页监控仪表板 | ✅ PowerShell HttpListener 版 | 用 Python 版悬浮窗替代 |
| 桌面悬浮窗 | 📦 骨架代码已交付 | 适配 macOS 后实现 |

### Windows 端踩过的坑（macOS 可避免）

1. **PowerShell 多行 SQL 管道传参换行被吞** → macOS 的 bash heredoc 不会
2. **PowerShell 5.1 按 GBK 读 UTF-8 文件中文乱码** → macOS 默认 UTF-8
3. **Windows 无 tmpfs，RAM disk 要装 ImDisk** → macOS 原生 `hdiutil` 支持

---

## 六、建议执行顺序

1. **先跑检测**（第二节）——确认 macOS 上是否存在同样问题
2. **方案 A 止血**（先加触发器）——立即停止 SSD 写入
3. **方案 C 彻底**（迁移到 RAM disk）——macOS 原生支持，一劳永逸
4. **装监控**（悬浮窗）——实时监控写入情况 + 触发器是否失效
5. **验证**——启动 Codex 跑任务，确认写入归零

---

## 七、参考链接

- GitHub Issue #17320: https://github.com/openai/codex/issues/17320
- GitHub Issue #28224: https://github.com/openai/codex/issues/28224
- GitHub Issue #30322: https://github.com/openai/codex/issues/30322
- 完整排查与修复指南: https://blog.csdn.net/aidoudoulong/article/details/162445238
- macOS RAM disk 往返方案: https://www.cnblogs.com/notlate-cn/p/21439874
- Codex 官方配置文档: https://developers.openai.com/codex/config-reference
- Codex usage 页面: https://chatgpt.com/codex/settings/usage

---

## 八、logs 表 schema（已确认）

```sql
CREATE TABLE logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    ts_nanos INTEGER NOT NULL,
    level TEXT NOT NULL,
    target TEXT NOT NULL,
    feedback_log_body TEXT,
    module_path TEXT,
    file TEXT,
    line INTEGER,
    thread_id TEXT,
    process_uuid TEXT,
    estimated_bytes INTEGER NOT NULL DEFAULT 0
);
```

state_5.sqlite 的 threads 表（用于用量估算）：
```sql
CREATE TABLE threads (
    id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,    -- unix 时间戳
    updated_at INTEGER NOT NULL,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    -- ... 其他字段
);
```
