# 跨市场量比监控系统 — 代码审查报告 (DeepSeek)

**审查日期**: 2026-04-29
**项目版本**: v2.0
**代码规模**: 1869 行 Python，10 个脚本
**审查范围**: 框架设计、编码安全、编码质量、数据流

---

## 一、框架设计问题

### P0 — 严重

#### 1.1 `scan_and_alert()` 函数重复定义 (BUG)

- **文件**: `scripts/alert.py:151` 和 `scripts/alert.py:257`
- **问题**: 同一函数在同文件定义两次。Python 使用后一个定义，第一个版本（第151-174行）永远不会被执行。第一个版本调用 `generate_minimax_prompt()`，第二个版本调用 `generate_llm_prompt()` — 两个 prompt 函数内容几乎相同但签名略有差异。
- **影响**: 第一个版本及其依赖的 `generate_minimax_prompt()` 成为死代码。开发者可能以为运行的是简单版本，实际运行的是会消耗 LLM API 额度的版本。
- **修复**: 删除第一个 `scan_and_alert()`（第151-174行）和 `generate_minimax_prompt()`（第177-192行）。

#### 1.2 A股简报筛选 BUG (`.CN` 后缀不存在)

- **文件**: `scripts/alert.py:332`
- **代码**: `cn_tickers = [r for r in sorted_results if r["ticker"].endswith(".CN")]`
- **问题**: 系统中 A 股 ticker 使用 `.SH`（上交所）或 `.SZ`（深交所）后缀，不存在 `.CN` 后缀。此筛选条件永远为空。
- **影响**: A 股标的永远不会出现在 30 分钟简报中。
- **修复**: 改为 `r["ticker"].endswith(".SH") or r["ticker"].endswith(".SZ")`

#### 1.3 `scan_market()` 市场过滤失效 (BUG)

- **文件**: `scripts/cli.py:130-142`
- **问题**: `market_key` 变量已计算（`market_map.get(market.upper(), "us")`）但从未用于过滤结果。当前 `scan_market()` 返回的是所有市场中量比超阈值的标的，而非指定市场。
- **影响**: `python3 cli.py --market US --min-ratio 2.0` 会返回所有市场的结果，而非仅美股。
- **修复**: 使用 `market_key` 过滤 ticker 后缀。

#### 1.4 API Key 明文存储

- **文件**: `config.yaml:68,81,87,96`
- **问题**: 3 组 API Key（MiniMax、Xiaomi）和 1 个飞书 Webhook URL 以明文存储。`.gitignore` 排除了 `config.yaml`，但文件仍存在于磁盘上，任何有文件系统访问权限的人都能读取。
- **影响**: 密钥泄露可导致 API 滥用、费用损失、飞书群被垃圾消息轰炸。
- **修复**: 迁移至环境变量或 macOS Keychain。

### P1 — 中等

#### 1.5 `load_config()` 在 6 个文件中重复定义

- **文件**: `collect.py:22-25`, `collect_ws.py:31-34`, `compute.py:22-25`, `alert.py:20-23`, `cli.py:19-22`, `llm.py:19-22`
- **问题**: 完全相同的 4 行函数复制粘贴 6 次，每次调用都重新读取和解析 YAML。`compute_all()` 流程中该函数被调用 1+N 次（N=标的数）。
- **修复**: 创建 `scripts/core/config.py`，使用 `functools.lru_cache` 缓存。

#### 1.6 `get_market()` 在 3 个文件中重复定义

- **文件**: `collect.py:28-36`, `collect_ws.py:37-44`, `compute.py:28-35`
- **问题**: 市场判断逻辑重复 3 次。
- **修复**: 提取为共享工具函数。

#### 1.7 快照文件爆炸，线性扫描性能退化

- **文件**: `collect_ws.py:151-163`, `compute.py:57-75`
- **问题**: WebSocket 每秒推送一次，每个 ticker 每次产生一个 JSON 文件。以 11 个标的、6 小时交易时段计算：11 × 3600 × 6 = 237,600 个文件/天。`list_snapshots()` 每次都遍历整个目录，用文件名前缀过滤，O(N) 复杂度且 N 持续增长。
- **影响**: 3 小时后已有 15,993 个文件，`calc_intraday_ratio()` 每次读取 100+ 个 JSON 文件，磁盘 IO 和解析开销极大。
- **修复**: 改用 SQLite 单表追加存储，或每标的每天一个 JSONL 文件。

#### 1.8 `cli.py` 绕过了 `llm.py` 抽象层

- **文件**: `scripts/cli.py:25-84`
- **问题**: `get_minimax_analysis()` 直接内联了 HTTP 调用逻辑，包括 headers、payload、endpoint 拼接。未使用 `llm.py` 的 `call_llm()` 统一接口。
- **影响**: 切换 LLM 模型时，`cli.py --analyze` 仍调用旧 API。修改 LLM 调用逻辑需要同时改两处。
- **修复**: 用 `llm.call_llm(prompt)` 替换内联代码。

#### 1.9 `sys.path.insert` hack 跨 2 个文件 8 处

- **文件**: `alert.py:153,197,259,311`, `cli.py:89,116,132,180`
- **问题**: 动态修改 `sys.path` 实现跨模块导入，而非使用 Python 标准包导入机制。
- **影响**: 脆弱、IDE 无法静态分析、可能导入错误版本的模块。
- **修复**: 配置 `pyproject.toml` 的 `[project.scripts]` 或使用相对导入。

#### 1.10 无结构化日志

- **文件**: 全部脚本
- **问题**: 所有日志通过 `print()` 输出，无时间戳格式、无日志级别、无轮转、无结构化字段。
- **影响**: `ws_collect.log` 已达 8.2MB，无法按级别过滤，调试困难。
- **修复**: 引入 `logging` 模块 + `RotatingFileHandler`。

### P2 — 低

#### 1.11 双层 fork 守护进程实现重复

- **文件**: `collect_ws.py:231-257`, `collect_ws_launcher.py:48-74`, `start_all.py:38-63`
- **问题**: 三处各自实现相同的 double-fork + setsid + IO 重定向模式，代码高度重复。
- **修复**: 使用 `supervisord` 或 `python-daemon` 库。

#### 1.12 `writer_thread()` 死代码

- **文件**: `collect_ws.py:138-148`
- **问题**: 函数已定义但从未被调用（`collect_ws.py` 中只出现一次，即定义处）。实际写出逻辑内联在 `run_websocket()` 的第 206-213 行。
- **修复**: 删除 `writer_thread()` 函数。

#### 1.13 `switch_profile()` 无意义别名

- **文件**: `llm.py:136-138`
- **问题**: 仅一行调用 `switch_llm(profile)`，不提供任何额外价值。
- **修复**: 删除，直接使用 `switch_llm()`。

---

## 二、编码安全问题

### P0 — 高风险

#### 2.1 `--test` 模式泄露完整 API 配置

- **文件**: `scripts/llm.py:161-162`
- **代码**: `print(f"当前配置: {get_llm_config()}")`
- **问题**: `get_llm_config()` 返回完整的配置字典，包含 `api_key` 明文。`--test` 输出会被写入 cron 日志。
- **修复**: 脱敏处理，API Key 仅显示前 6 位和后 4 位。

#### 2.2 API 错误响应可能泄露 API Key

- **文件**: `scripts/llm.py:129`, `scripts/cli.py:80`
- **代码**: `print(f"[llm] API 错误: {resp.status_code} {resp.text[:200]}")`
- **问题**: API 错误响应 body 可能回显请求参数（含 API Key）。截取前 200 字符不足以防止泄露。
- **修复**: 只记录状态码，不记录响应 body。

#### 2.3 `config.yaml` 中的真实密钥有意外提交风险

- **文件**: `config.yaml`
- **问题**: 虽然 `.gitignore` 包含 `config.yaml`，但一旦误操作（如 `git add -A` 后手动恢复其他文件时遗漏），密钥将进入 git 历史。
- **修复**: 使用环境变量 + `config.yaml.example` 模板模式，或使用 `git update-index --skip-worktree`。

### P1 — 中风险

#### 2.4 路径遍历风险

- **文件**: `collect.py:95-104`, `collect_ws.py:151-163`
- **问题**: `ticker` 值直接用于构建文件路径（`f"{ticker.replace('.', '_')}_{ts}.json"`）。如果 ticker 来自不可信来源且包含 `../`，可写入任意目录。
- **当前缓解**: ticker 来自 `config.yaml`，非外部输入。但若未来支持 CLI 输入或 API 传入，将成为直接漏洞。
- **修复**: 添加 `sanitize_ticker()` 过滤 `/`、`\`、`.html` 等路径字符。

#### 2.5 CLI ticker 参数无格式验证

- **文件**: `collect.py:131`, `compute.py:422`, `cli.py:170`
- **问题**: `--ticker` 参数直接传递给下游函数，无任何格式校验。
- **修复**: 添加正则验证 `^[A-Za-z0-9]+\\.(US|HK|SH|SZ)$`

#### 2.6 `float()` 转换无异常保护

- **文件**: `collect.py:74-77`
- **代码**: `last = float(quote.get("last", 0) or 0)`
- **问题**: 如果 `quote["last"]` 为非数值字符串（如 `"N/A"`），`float()` 抛出 `ValueError` 导致整个采集流程中断。
- **修复**: 包装为 `safe_float()` 函数，失败时返回 0.0 并记录警告。

#### 2.7 模块级全局变量无线程保护

- **文件**: `collect_ws.py:26-28`
- **代码**: `running = True`, `quote_queue = queue.Queue()`, `_prev_close_cache = {}`
- **问题**: `running` 在主线程和信号处理器之间共享，无同步原语；`_prev_close_cache` 在回调线程和主线程间共享，无锁保护。虽然 Python GIL 提供了基本原子性，但依赖 GIL 实现细节是不安全的实践。
- **修复**: `running` 使用 `threading.Event()`，`_prev_close_cache` 加 `threading.Lock()`。

#### 2.8 快照文件名秒级时间戳导致竞态覆盖

- **文件**: `collect_ws.py:156-158`
- **问题**: 文件名使用秒级时间戳（`%Y%m%d_%H%M%S`）。同一秒内同一 ticker 收到多条 WebSocket 推送时，后一条覆盖前一条，数据丢失。
- **修复**: 添加毫秒（`%f`）或 UUID 后缀。

#### 2.9 SQLite 并发写入无超时

- **文件**: `compute.py:354`
- **代码**: `conn = sqlite3.connect(get_db_path())`
- **问题**: 默认超时 5 秒。多个 cron 任务同时触发 `alert.py` 和 `alert.py --brief` 时，两个进程同时写 `ratios.db`，引发 `database is locked`。
- **修复**: `sqlite3.connect(get_db_path(), timeout=30)`

#### 2.10 过于宽泛的 `except Exception`

- **文件**: `alert.py:146`, `llm.py:131`, `collect_ws.py:62,134,147,213`, `cli.py:82`
- **问题**: 共 7 处 `except Exception`，会吞掉 `KeyboardInterrupt`、`SystemExit`、`MemoryError` 等不该捕获的异常。
- **修复**: 至少捕获 `except (requests.RequestException, ValueError, KeyError)` 等具体类型，并记录 traceback。

#### 2.11 WebSocket 断连无自动重连

- **文件**: `collect_ws.py:206-213`
- **问题**: WebSocket 断开后 `run_websocket()` 的主循环退出，进程终止。依赖外部 cron launcher 每分钟检测重启，最多有 60 秒真空期。
- **修复**: 在 `run_websocket()` 外层包装重连循环，使用指数退避（1s → 2s → 4s → 8s → max 60s）。

#### 2.12 `fetch_prev_close()` 无空目录处理

- **文件**: `collect_ws.py:52-53`
- **代码**: `cid = list(client_id.iterdir())[0].name`
- **问题**: 如果 token 目录为空，`list(...)` 返回空列表，`[0]` 引发 `IndexError`，被外层 `except Exception` 吞掉。
- **修复**: 检查目录是否为空，给出明确错误提示。

### P2 — 低风险

#### 2.13 日志文件权限 0o644 过宽

- **文件**: `collect_ws.py:245`, `collect_ws_launcher.py:65,69`
- **问题**: 日志可能包含交易数据、ticker 信息、LLM 分析结果，不应让系统所有用户可读。
- **修复**: 改为 `0o600` 或 `0o640`。

#### 2.14 `subprocess.run` 无超时设置

- **文件**: `start_all.py:16,22,86`, `stop_all.py:17,25,31,67`
- **问题**: `crontab` 命令可能因锁文件或权限问题永久挂起。
- **修复**: 添加 `timeout=30`。

#### 2.15 PID 文件 TOCTOU 竞态

- **文件**: `start_all.py:29-35`, `collect_ws_launcher.py:30-38`
- **问题**: 先检查 PID 文件（`is_running(pid)`），再 fork 写入新 PID。两步之间无原子性保证，两个 launcher 可能同时启动两个进程。
- **修复**: 使用 `fcntl.flock()` 或创建 PID 文件时使用 `os.open(path, os.O_CREAT | os.O_EXCL)`。

---

## 三、编码质量问题

### P0 — 严重

#### 3.1 `generate_minimax_prompt()` 与 `generate_llm_prompt()` 几乎重复

- **文件**: `scripts/alert.py:177-192` 和 `scripts/alert.py:214-229`
- **问题**: 两个函数生成完全相同的 prompt 文本，仅函数名不同。前者是死代码（仅被已删除的第一个 `scan_and_alert` 引用）。
- **修复**: 删除 `generate_minimax_prompt()`，保留 `generate_llm_prompt()`。

#### 3.2 `format_alert_message()` 与 `format_full_alert_message()` 高度重复

- **文件**: `scripts/alert.py:106-124` 和 `scripts/alert.py:232-254`
- **问题**: ~80% 代码重叠，后者仅多了 LLM 分析字段。
- **修复**: 合并为一个函数，LLM 分析作为可选参数 `analysis: Optional[str] = None`。

#### 3.3 LLM Prompt 模板重复 3 次

- **文件**: `alert.py:218-229`, `cli.py:38-48`, `alert.py:354-362`（简报 prompt）
- **问题**: 量比分析 prompt 的核心结构（你是量比分析专家...）在三个地方独立硬编码。
- **修复**: 提取为 `PROMPT_TEMPLATE_ANALYSIS` 常量或模板函数。

### P1 — 中等

#### 3.4 `calc_intraday_ratio()` 重复读取同一个快照文件

- **文件**: `compute.py:268-290`
- **问题**: 每个快照文件独立 `open()` + `json.load()`。对于 100+ 个快照，这是 100+ 次文件系统调用。
- **修复**: 批量读取并缓存解析结果。

#### 3.5 `load_config()` 无缓存导致重复 IO

- **文件**: 全部使用 `load_config()` 的文件
- **问题**: `compute_all()` → `compute_ticker()` → `calc_volume_ratio()` 流程中，`load_config()` 被调用 1 + N 次。YAML 解析是纯 CPU 操作，无缓存意味着每个 ticker 都重新解析。
- **修复**: `functools.lru_cache(maxsize=1)` 装饰。

#### 3.6 数据库连接每次调用都创建和销毁

- **文件**: `compute.py:350-368`
- **问题**: `save_ratio()` 每次调用执行 `init_db()` + `connect()` + `commit()` + `close()`。11 个标的 × 每分钟 = 每分钟 11 次连接创建。
- **修复**: 模块级持久连接或连接池。

#### 3.7 数据库 `daily_summary` 表完全空置

- **文件**: `compute.py:184-197`
- **问题**: `daily_summary` 表已创建但没有任何代码写入，表中始终为 0 条记录。这是未完成的功能。
- **修复**: 要么实现每日汇总写入逻辑，要么删除该表。

#### 3.8 数据库只写不读

- **文件**: `compute.py:165-199,350-368`
- **问题**: `volume_ratios` 表已写入大量记录，但项目中没有任何查询该表的代码。历史数据未被利用。
- **修复**: 添加历史量比查询接口或移除数据库。

#### 3.9 watchlist 遍历模式重复 5 次

- **文件**: `collect.py:115-116`, `collect_ws.py:170-173`, `compute.py:381-383`, `alert.py:330-332`, `cli.py:122-124`
- **代码**: 相同的三层嵌套循环 `for market in ["us", "hk", "cn"]: tickers.extend(watchlist.get(market, []))`
- **修复**: 提取为共享函数 `get_all_tickers()`。

### P2 — 低

#### 3.10 `import yaml` 位置不符合 PEP 8

- **文件**: 6 个包含 `load_config()` 的文件
- **问题**: `import yaml` 位于函数内部而非文件顶部。
- **修复**: 移至文件顶部。

#### 3.11 类型注解不完整

- **问题**: 大量函数返回值标注为 `dict`，未使用 `TypedDict` 定义具体结构。`Optional[dict]` 过于宽泛。
- **修复**: 定义 `Snapshot`, `QuoteResult`, `AlertSignal` 等 TypedDict。

#### 3.12 `start_all.py` 硬编码绝对路径

- **文件**: `start_all.py:72-74`
- **问题**: `/Users/shinji/project-x/volume-ratio/` 和 `/usr/bin/python3` 硬编码。无法在其他机器或用户下运行。
- **修复**: 使用 `ROOT` 和 `sys.executable` 动态构建路径。

#### 3.13 `collect.py` 中的空异常处理

- **文件**: `collect.py:60-62`
- **代码**: `except json.JSONDecodeError: pass`
- **问题**: JSON 解析失败时静默忽略，之后返回 `None`。丢失了错误上下文。
- **修复**: 至少记录警告日志。

#### 3.14 信号字符串中英文混杂

- **问题**: 函数名和变量名使用英文（`calc_volume_ratio`, `cond_stop`），信号输出值使用中文（`"放量止跌"`, `"缩量异常"`），注释中英文混合。
- **建议**: 信号值考虑使用英文枚举 + 中文显示名映射。

#### 3.15 `scripts/__init__.py` 仅一行注释

- **文件**: `scripts/__init__.py`
- **问题**: 仅含 `# Scripts module`，无 `__all__` 导出控制。
- **修复**: 添加 `__all__` 明确定义公共 API。

#### 3.16 `pyproject.toml` 依赖列表为空

- **文件**: `pyproject.toml`
- **代码**: `dependencies = []`
- **问题**: 未声明 `pyyaml`, `requests`, `longbridge` 等必需依赖。
- **修复**: 补全依赖列表，或添加 `requirements.txt`。

#### 3.17 `send_brief_report()` 中的内联 prompt 过长

- **文件**: `alert.py:354-362`
- **问题**: prompt 模板直接硬编码在函数体内，与其他 prompt 模板不统一。
- **修复**: 提取为独立常量。

---

## 四、数据流与架构问题

### 4.1 快照数据无清理策略

- **问题**: 快照文件无限积累，无过期删除机制。一周后可达百万级文件。
- **修复**: 添加定时清理任务，删除 7 天前的快照文件。

### 4.2 数据冗余：文件系统 + SQLite 双写

- **问题**: 同一数据（量比结果）同时存在于快照 JSON 文件和 `ratios.db` 中，两处无一致性保证。
- **修复**: 统一数据源，快照存 SQLite，或量比结果直接读快照文件。

### 4.3 `--brief` 简报不区分交易时间

- **文件**: `alert.py:306-371`
- **问题**: 简报每 30 分钟发送一次（通过 cron），但在非交易时间（如 A 股收盘后、美股开盘前）仍会发送空或过时数据。
- **修复**: 添加交易时间判断，非交易时段跳过。

### 4.4 无数据校验

- **问题**: 快照数据无完整性校验。volume 可能为负数（WebSocket 异常推送），price 可能为 0（停牌），这些情况均未处理。
- **修复**: 添加 `validate_snapshot()` 函数，标记异常数据。

---

## 五、测试覆盖

### 5.1 零测试代码

- **问题**: 项目完全没有测试文件（无 `test_*.py`，无 `tests/` 目录）。1869 行产品代码的测试覆盖率为 0%。
- **核心函数风险**:
  - `calc_volume_ratio()` — 量比计算核心，无测试
  - `calc_intraday_ratio()` — 日内滚动量比，逻辑复杂（窗口分割、三条件判断）
  - `detect_signals()` — 信号检测，多条件组合
  - `get_signal()` / `get_signal_detail()` — 信号分类
  - `extract_fields()` — 数据提取和类型转换
- **修复**: 至少为上述 5 个核心函数添加单元测试。

### 5.2 无 mock/fixture 基础设施

- **问题**: 所有函数直接依赖文件系统和外部服务（Longbridge、飞书、LLM API），无法独立测试。
- **修复**: 引入 `pytest` + `pytest-mock` + `tmp_path` fixture。

---

## 六、综合优先级矩阵

| 优先级 | 编号 | 问题 | 类别 | 严重度 |
|:--:|:--:|:--|:--:|:--:|
| **P0** | 1.1 | `scan_and_alert()` 重复定义 | 框架 | 🔴 |
| **P0** | 1.2 | A股简报 `.CN` BUG | 框架 | 🔴 |
| **P0** | 1.3 | `scan_market()` 市场过滤失效 | 框架 | 🔴 |
| **P0** | 1.4 | API Key 明文存储 | 安全 | 🔴 |
| **P0** | 2.1 | `--test` 模式泄露 API Key | 安全 | 🔴 |
| **P0** | 2.2 | API 错误响应可能泄露 Key | 安全 | 🔴 |
| **P0** | 3.1 | generate 函数重复 | 质量 | 🔴 |
| **P1** | 1.5 | `load_config()` 重复 6 次 | 框架 | 🟡 |
| **P1** | 1.6 | `get_market()` 重复 3 次 | 框架 | 🟡 |
| **P1** | 1.7 | 快照文件爆炸 | 框架 | 🟡 |
| **P1** | 1.8 | `cli.py` 绕过 `llm.py` | 框架 | 🟡 |
| **P1** | 2.4 | 路径遍历风险 | 安全 | 🟡 |
| **P1** | 2.5 | CLI 参数无验证 | 安全 | 🟡 |
| **P1** | 2.6 | `float()` 无异常保护 | 安全 | 🟡 |
| **P1** | 2.7 | 全局变量无线程保护 | 安全 | 🟡 |
| **P1** | 2.8 | 快照文件竞态覆盖 | 安全 | 🟡 |
| **P1** | 2.9 | SQLite 并发无超时 | 安全 | 🟡 |
| **P1** | 2.10 | 过于宽泛的异常捕获 | 安全 | 🟡 |
| **P1** | 2.11 | WebSocket 无自动重连 | 安全 | 🟡 |
| **P1** | 3.4 | 重复读取快照文件 | 质量 | 🟡 |
| **P1** | 3.5 | `load_config()` 无缓存 | 质量 | 🟡 |
| **P1** | 3.7 | 数据库 daily_summary 空置 | 质量 | 🟡 |
| **P1** | 3.8 | 数据库只写不读 | 数据流 | 🟡 |
| **P1** | 4.1 | 快照无清理策略 | 数据流 | 🟡 |
| **P1** | 4.3 | 简报不区分交易时间 | 数据流 | 🟡 |
| **P2** | 1.9 | `sys.path.insert` hack | 框架 | ⚪ |
| **P2** | 1.10 | 无结构化日志 | 框架 | ⚪ |
| **P2** | 1.11 | 双层 fork 重复 | 框架 | ⚪ |
| **P2** | 1.12 | `writer_thread()` 死代码 | 框架 | ⚪ |
| **P2** | 1.13 | `switch_profile()` 别名 | 框架 | ⚪ |
| **P2** | 2.13 | 日志权限过宽 | 安全 | ⚪ |
| **P2** | 2.14 | subprocess 无超时 | 安全 | ⚪ |
| **P2** | 2.15 | PID 文件 TOCTOU | 安全 | ⚪ |
| **P2** | 3.9 | watchlist 遍历重复 | 质量 | ⚪ |
| **P2** | 3.10 | `import yaml` 位置 | 质量 | ⚪ |
| **P2** | 3.11 | 类型注解不完整 | 质量 | ⚪ |
| **P2** | 3.12 | 硬编码绝对路径 | 质量 | ⚪ |
| **P2** | 3.13 | 空异常处理 | 质量 | ⚪ |
| **P2** | 3.16 | `pyproject.toml` 依赖为空 | 质量 | ⚪ |
| **P2** | 5.1 | 零测试代码 | 测试 | 🔴 |

---

## 七、推荐修复路线图

### Phase 1 — 止血 (1-2h)

1. 删除 `alert.py` 中重复的 `scan_and_alert()` 和 `generate_minimax_prompt()`（1.1, 3.1）
2. 修复 A 股简报 `.CN` → `.SH`/`.SZ`（1.2）
3. 修复 `scan_market()` 未使用 `market_key` BUG（1.3）
4. API Key 迁移至环境变量（1.4, 2.3）
5. `--test` 模式脱敏显示（2.1）
6. API 错误日志不再输出响应 body（2.2）
7. `cli.py` 改用 `llm.call_llm()`（1.8）

### Phase 2 — 抽取公共模块 (2-3h)

1. 创建 `scripts/core/config.py`（`load_config` + LRU 缓存）
2. 创建 `scripts/core/market.py`（`get_market` + `get_all_tickers` + 交易时间判断）
3. 消除所有 `sys.path.insert`，使用正式包导入
4. 合并 `format_alert_message` 和 `format_full_alert_message`
5. 提取统一 prompt 模板

### Phase 3 — 数据层重构 (3-4h)

1. 快照存储从文件系统迁移至 SQLite
2. 添加快照自动清理策略（7 天过期）
3. 实现 `daily_summary` 写入逻辑（或删除表）
4. 添加历史量比查询接口
5. SQLite 连接池化

### Phase 4 — 健壮性提升 (2-3h)

1. WebSocket 添加自动重连 + 指数退避
2. 引入 `logging` 模块 + `RotatingFileHandler`
3. 添加交易时间过滤（简报/扫描/采集）
4. 进程管理升级（`supervisord` 替代手动 fork）
5. 文件名添加毫秒精度防竞态

### Phase 5 — 测试覆盖 (3-4h)

1. 引入 `pytest` 框架
2. 优先覆盖：`calc_volume_ratio`, `calc_intraday_ratio`, `detect_signals`, `get_signal`, `extract_fields`
3. 添加 mock 隔离外部依赖

---

## 八、统计摘要

| 类别 | P0 | P1 | P2 | 合计 |
|:--|:--:|:--:|:--:|:--:|
| 框架设计 | 4 | 5 | 4 | 13 |
| 编码安全 | 3 | 9 | 3 | 15 |
| 编码质量 | 1 | 6 | 8 | 15 |
| 数据流 | 0 | 3 | 0 | 3 |
| 测试覆盖 | 0 | 0 | 1 | 1 |
| **合计** | **8** | **23** | **16** | **47** |

---

**结论**: 项目核心逻辑（量比计算、信号检测）功能正确，但存在 3 个实际 BUG 需要立即修复（1.1, 1.2, 1.3），3 个安全密钥泄露风险需要止血（1.4, 2.1, 2.2）。架构层面最大问题是缺少公共模块抽象和快照文件爆炸。测试覆盖率为 0%，建议尽早为核心算法补充测试。
