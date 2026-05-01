# 代码审查修复记录 — 2026-05-01

全面代码审查发现 33 个问题（2 CRITICAL / 5 HIGH / 16 MEDIUM / 10 LOW），本次修复 8 个高优先级缺陷。

---

## CRITICAL

### 1. FD double-close bug（5 处）

**文件**：`compute.py`（4 处）、`core/market.py`（1 处）

**问题**：stdout 抑制代码中 `except` 块和 `finally` 块都执行 `os.dup2(old_stdout_fd, 1)` + `os.close(old_stdout_fd)`，导致文件描述符被关闭两次。异常时 except 块先关闭 fd，finally 块再关闭已关闭的 fd，可能影响后续打开的文件。

**修复**：移除 except 块中的 fd 恢复代码，仅保留 finally 块统一处理。

---

## HIGH

### 2. _quote_count 竞态条件

**文件**：`collect_ws.py`

**问题**：`_quote_count` 在 WebSocket 回调线程中自增，在主线程中读取，无锁保护。

**修复**：将 `_quote_count += 1` 移入 `_cache_lock` 保护范围，主线程读取时也加锁。

### 3. _active_tickers 内存泄漏

**文件**：`collect_ws.py`

**问题**：`_active_tickers` 集合只增不清，长期运行后无限增长。

**修复**：在每次 prev_close 缓存清理周期后调用 `_active_tickers.clear()`。

### 4. PID 文件竞态条件

**文件**：`start_all.py`

**问题**：检查 PID 文件存在 → 检查进程存活 → 写入新 PID 之间存在 TOCTOU 竞态，并发启动可能导致两个实例同时运行。

**修复**：使用 `fcntl.flock` 文件锁保护 PID 检查和写入的原子性。

### 5. 重复的交易日逻辑

**文件**：`compute.py`

**问题**：`compute.py` 自己实现了 `_check_trading_days()` 和 `is_trading_day()`，与 `core/market.py` 中的 `_is_trading_day()` 功能重复，且有两套独立缓存。

**修复**：删除 `compute.py` 中的重复实现，改用 `from core.market import _is_trading_day as is_trading_day`。

### 6. 静默进程死亡无日志

**文件**：`collect_ws.py`

**问题**：多个 `except` 块只打印简短错误信息，不记录堆栈，进程异常退出时无法排查原因。

**修复**：添加 `import traceback`，在关键 except 块中调用 `traceback.print_exc()`。

---

## MEDIUM

### 7. cleanup.py 收盘判断逻辑错误

**文件**：`cleanup.py`

**问题**：`now.hour >= 16 and now.minute >= 30` 在 17:00 时返回 False（minute < 30），导致 A 股 17:00-17:29 之间不触发清理。

**修复**：改为 `now.hour > 16 or (now.hour == 16 and now.minute >= 30)`。

### 8. mute 过期未检查

**文件**：`alert.py`

**问题**：`/mute CLF.US 2h` 设置的静默有到期时间，但 `detect_signals()` 只检查 `ticker in mute_list`，从不检查是否过期，导致静默永久生效。

**修复**：添加 `datetime.fromisoformat()` 过期检查，过期条目自动从 mute_list 删除并回写 config.yaml。

---

## 涉及文件清单

| 文件 | 改动 |
|------|------|
| `scripts/compute.py` | 删除重复交易日逻辑（-80 行），移除 4 处 FD double-close |
| `scripts/core/market.py` | 移除 1 处 FD double-close |
| `scripts/collect_ws.py` | 修复竞态、内存泄漏、添加 traceback |
| `scripts/start_all.py` | 添加 fcntl 文件锁 |
| `scripts/alert.py` | 添加 mute 过期检查 |
| `scripts/cleanup.py` | 修复 CN 收盘判断 |

---

## 验证

```bash
# 语法检查
python3 -m py_compile scripts/compute.py scripts/collect_ws.py scripts/alert.py scripts/cleanup.py scripts/start_all.py scripts/core/market.py

# 导入检查
python3 -c "import scripts.compute; import scripts.collect_ws; import scripts.alert; import scripts.cleanup"

# 功能测试
python3 -c "from scripts.compute import get_signal; print(get_signal(2.5))"
python3 -c "from scripts.cleanup import is_market_closed; print(is_market_closed('CN'))"
```
