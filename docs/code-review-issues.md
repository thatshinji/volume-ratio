# 代码审查问题记录

审查日期：2026-05-07

本文档记录代码审查中发现但决定不修复的问题，附理由供后续参考。

---

## 已修复

以下问题在本次审查中已修复：

| 问题 | 修复方式 |
|:--|:--|
| `json` 未导入 (llm.py) | 添加 `import json` |
| `_resolve_session_time` 嵌套过深 | 提取 `_fallback_to_latest`，扁平化逻辑 |
| `init_db` 过长 (~165行) | 拆分为 `_create_tables` + `_migrate_schema` |
| `RATIO_WRITE_INTERVAL` 无注释 | 添加注释 |
| `_trading_days_cache` 无大小限制 | 添加 `_TRADING_DAYS_CACHE_MAX` |
| mute 配置直接修改字典 | 使用 `dict()` 副本 |
| 命名不一致 (start_all.py) | 统一为 `venv_python` / `system_python` |
| monkey-patch SDK 无容错 | 加 try/except + 版本注释 |
| `build_sync_card` 阻塞消息循环 | 改为后台线程执行 |
| node_modules 残留 | 删除 + .gitignore |
| collect.py 废弃代码 | 删除 + cli.py 移除引用 |
| locked_pid 重复定义 | 提取到 core/utils.py |
| BaseException 过捕获 | 改为 Exception，删除 dead code |
| DB 连接风格不一致 | `_sqlite3` → `sqlite3` |
| 缓存无 TTL | range/lookup 缓存加 4 小时过期 |
| 依赖未锁定 | pyproject.toml 加上限版本约束 |
| 市场时间硬编码 | 提取到 `MARKET_SESSIONS` / `MARKET_END_OF_DAY` |

---

## 审查通过（不修复）

### 守护进程宽泛异常捕获

**位置：** feishu_bot.py (10处)、collect_ws.py (3处)

**现状：** 多处 `except Exception as e:` 捕获所有异常后记录日志并继续运行。

**理由：** 这两个文件都是长驻守护进程。宽泛异常捕获是防止单条消息/单次行情处理失败导致整个进程崩溃的标准做法。异常已被记录到日志，不影响问题排查。收窄异常类型反而可能遗漏未预见的 SDK 异常。

---

### should_push 降级也推送

**位置：** alert.py:461-486

**现状：** 信号状态变化（包括降级）都会触发推送，只有状态不变时静默。

**理由：** 业务正确。降级信号（如从"放量突破"变为"放量下跌"）包含重要信息，用户需要知道方向变化。静默降级会导致用户错过风险信号。

---

### _row_to_record 静默返回 None

**位置：** compute.py:204-224

**现状：** 解析失败时返回 None，不记录日志。

**理由：** 该函数在量比计算的热路径上被高频调用。添加日志会在数据异常时产生大量输出，反而淹没真正的问题。数据库中缺失字段的记录本身就是可预期的边界情况。

---

### JSONL 每条行情 flush

**位置：** collect_ws.py:138-141

**现状：** 每条行情打开文件、写入、flush、关闭。

**理由：** 这是 crash-safety 优先的设计取舍。WebSocket 进程可能随时被 kill（SIGTERM/SIGKILL），如果使用缓冲写入，最后几百条行情可能丢失。当前实现保证每条行情落盘，性能代价可接受（append-only 写入在 SSD 上很快）。

---

### API Key 空值仅打日志

**位置：** llm.py:113-117

**现状：** API Key 为空时打印日志并返回 None。

**理由：** 调用方（alert.py、feishu_bot.py）已处理 None 返回值（跳过 LLM 分析）。抛异常会中断信号推送流程，而 LLM 分析是锦上添花功能，不应阻塞核心告警。

---

### collect_ws 导入风格不一致

**位置：** collect_ws.py:143 (`from compute import` vs `from core.config import`)

**现状：** `compute.py` 在 scripts/ 级别，使用裸导入；core/ 子包使用 `core.xxx` 前缀。

**理由：** 这是项目全局约定。`compute.py` 是核心计算引擎，与 `core/` 目录下的工具模块职责不同，保持在 scripts/ 级别是合理的。所有文件都通过 `sys.path.insert` 确保导入可用。将 compute.py 移入 core/ 是更大的重构，收益不高。

---

### #N 标签注释

**位置：** alert.py、feishu_bot.py、core/display.py

**现状：** `#1`、`#4`、`#10` 等标签散布在代码中。

**理由：** 这些是需求追溯标记，将代码实现与产品需求（飞书消息优化的 10 项需求）关联。在需求稳定前保留有助于理解设计意图。后续需求冻结后可清理。

---

### compute.py 职责过大 (1181行)

**现状：** 数据库、缓存、计算、信号生成全在一个文件。

**理由：** 当前函数按职责分组，内部依赖紧密（共享缓存、共享 DB 连接、共享 SnapshotRecord 类型）。拆分需要引入大量跨模块传参或依赖注入，对单人维护的小项目来说收益不高。如果后续团队扩大或需要独立测试计算逻辑，再考虑拆分。

---

## 暂缓（后续评估）

### feishu_bot.py monkey-patch SDK

**位置：** feishu_bot.py:1163-1218

**现状：** 替换 `WsClient._handle_data_frame` 以支持 CARD 消息类型。原版 SDK 静默丢弃 CARD 帧。

**已做：** 加了 try/except 容错和版本注释。

**待定：** 如果 lark-oapi 后续版本原生支持 CARD 消息，应移除 patch 并使用官方 API。升级 SDK 时需要验证。
