# Codex 高频写盘修复指南 — Windows

> 适用于 Windows 10/11 上的 OpenAI Codex CLI / 桌面端。
> 本文档交接给 Codex 用户或 AI Agent，用于检测、修复、验证该已知问题。

---

## 一、问题背景

OpenAI Codex 存在一个已被认领的 bug（GitHub Issue [#17320](https://github.com/openai/codex/issues/17320) / [#28224](https://github.com/openai/codex/issues/28224) / [#30322](https://github.com/openai/codex/issues/30322)）：

Codex 默认把 **TRACE 级网络日志**（HTTP/2 帧、SSE 流、token delta、工具调用等）持续写入本地 SQLite：

```
%USERPROFILE%\.codex\logs_2.sqlite
%USERPROFILE%\.codex\logs_2.sqlite-wal
%USERPROFILE%\.codex\logs_2.sqlite-shm
```

### 严重性

- **写入量惊人**：实测 21 天约 **37TB** 写入，年化 ~640TB，远超 1TB 消费级 SSD 的全生命周期写入寿命（~600TBW）
- **绕过用户配置**：`RUST_LOG=warn` 只影响终端输出，SQLite 日志层有独立过滤链，TRACE 照写不误
- **文件大小是假象**：Codex 定期删旧行（insert-prune 循环），文件看着只有几百 MB，SSD 背后已吞掉巨量写入

---

## 二、检测步骤

### 2.1 检查文件是否存在

```powershell
dir "$env:USERPROFILE\.codex\logs_2.sqlite*"
```

### 2.2 检查日志级别分布

```powershell
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "SELECT level, COUNT(*) AS n FROM logs GROUP BY level ORDER BY n DESC;"
```

如果 TRACE 占绝大多数，确认中招。

### 2.3 检查写放大（关键证据）

```powershell
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "SELECT COUNT(*) AS current_rows, COALESCE(MAX(id),0) AS max_id FROM logs;"
```

如果 `max_id` 远大于 `current_rows`（比如差几十万），说明 Codex 累计写入大量日志又删掉，SSD 承受了巨量写入。

### 2.4 实时采样 15 秒

**Codex 正在工作时运行**：

```powershell
# 需要 sqlite3.exe 在 PATH 中，推荐从 https://www.sqlite.org/download.html 下载
$db = "$env:USERPROFILE\.codex\logs_2.sqlite"
$wal = "$env:USERPROFILE\.codex\logs_2.sqlite-wal"
$t0 = @{
    Rows = & sqlite3 $db "SELECT COUNT(*) FROM logs;"
    MaxId = & sqlite3 $db "SELECT COALESCE(MAX(id),0) FROM logs;"
    Wal = (Get-Item $wal -ErrorAction SilentlyContinue).Length
}
Start-Sleep 15
$t1 = @{
    Rows = & sqlite3 $db "SELECT COUNT(*) FROM logs;"
    MaxId = & sqlite3 $db "SELECT COALESCE(MAX(id),0) FROM logs;"
    Wal = (Get-Item $wal -ErrorAction SilentlyContinue).Length
}
Write-Host "15 秒内新增行数: $($t1.MaxId - $t0.MaxId)"
Write-Host "WAL 增量: $($t1.Wal - $t0.Wal) 字节"
Write-Host "平均速率: $(($t1.MaxId - $t0.MaxId) / 15) 行/秒"
```

### 2.5 检查 SSD 写入寿命

```powershell
# PowerShell 查看 SSD 健康状态
Get-PhysicalDisk | Select-Object FriendlyName, MediaType, HealthStatus
Get-Disk | Select-Object Number, FriendlyName, Size | Format-Table
```

使用 CrystalDiskInfo 查看更详细的 SMART 数据——关注 "Total Host Writes"。

---

## 三、修复方案

### 推荐：SQLite Trigger 拦截（立即止血）

**先完全退出 Codex**，然后：

```powershell
# 确认 Codex 已退出
Get-Process -Name "codex" -ErrorAction SilentlyContinue

# 备份
$backup = "$env:USERPROFILE\.codex\logs-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory $backup -Force
Copy-Item "$env:USERPROFILE\.codex\logs_2.sqlite*" $backup\

# 创建触发器（注意：sqlite3.exe 必须可用）
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "CREATE TRIGGER IF NOT EXISTS block_log_inserts BEFORE INSERT ON logs BEGIN SELECT RAISE(IGNORE); END;"

# 验证
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "SELECT name FROM sqlite_master WHERE type='trigger' AND name='block_log_inserts';"

# 测试拦截
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "SELECT COUNT(*) AS before_count FROM logs;"
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "INSERT INTO logs(ts, ts_nanos, level, target) VALUES(2000000000, 0, 'TRACE', 'test');"
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "SELECT COUNT(*) AS after_count FROM logs;"
# before == after 说明拦截成功

# VACUUM 压缩
sqlite3 "$env:USERPROFILE\.codex\logs_2.sqlite" "VACUUM;"
```

**效果与代价**：
- SSD 写入归零
- Codex 升级后触发器可能被重建覆盖，需重新添加
- 日志表不再有新数据（会话记录不受影响，在 `sessions/` 目录）

### 备选：RAM Disk 迁移（最彻底）

```powershell
# 需要安装 ImDisk Toolkit: https://sourceforge.net/projects/imdisk-toolkit/
# 创建 RAM disk 后，在 ~\.codex\config.toml 中添加：
# sqlite_home = "R:\codex-sqlite"
```

---

## 四、持续监控

本项目提供的 `codex-monitor-widget` 桌面悬浮窗会：

- 每秒检查数据库状态、触发器完整性
- 三重校验（SQL 结构 + 哈希 + 行为实测）
- 自动安装/恢复计数版触发器
- 显示实时写入速率、WAL 增长、累计拦截数
- 显示 ChatGPT/Codex 账户剩余额度

详见 [README.md](README.md)。

---

## 五、无效方案

| 方案 | 为什么无效 |
|------|-----------|
| `RUST_LOG=warn` | 只影响终端，SQLite 日志层独立，TRACE 照写 |
| `[feedback] enabled = false` | 只禁反馈上传，不挡本地写入 |
| `[analytics] enabled = false` | 只禁分析上传，不挡本地写入 |
| 定期删旧行 + VACUUM | 回收文件空间，但不阻止新写入 |

---

## 六、安全清理已有日志

```powershell
# 确认 Codex 已退出
Get-Process -Name "codex" -ErrorAction SilentlyContinue

# 备份后移除
$backup = "$env:USERPROFILE\.codex\logs-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory $backup -Force
Move-Item "$env:USERPROFILE\.codex\logs_2.sqlite*" $backup\
# Codex 下次启动会自动重建空数据库
```

---

## 参考

- [GitHub Issue #17320](https://github.com/openai/codex/issues/17320)
- [GitHub Issue #28224](https://github.com/openai/codex/issues/28224)
- [完整排查与修复指南](https://blog.csdn.net/aidoudoulong/article/details/162445238)
- [Codex 官方配置文档](https://developers.openai.com/codex/config-reference)
- [Codex Usage 页面](https://chatgpt.com/codex/settings/usage)
