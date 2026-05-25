# DeepSeek 代码审查：问题与修复方案

审查日期：2026-05-25
覆盖范围：`scripts/` 下所有源文件 + `tests/` + `core/`

本文档记录本次审查发现的设计与编码问题，按严重程度分组，附修复方案。**已排除 `docs/code-review-issues.md` 中已记录的问题。**

---

## ✅ 已修复（5项）

- **#2** `_resolve_session_time` 死代码 + fallback 缺失 → `compute.py`
- **#5** 飞书卡片超尺寸 → `alert.py` 加 `MAX_ALERTS_PER_CARD=20` 分批
- **#9** 阈值重复维护 → `core/thresholds.py` 提取共享阈值表
- **#15** `cmd_status` 竞态条件 → `cli.py` 抽取 `_pid_is_alive()`
- **#18** 尾盘时间硬编码 → `compute.py` 引用 `MARKET_END_OF_DAY`

修复日期：2026-05-25

---

## ❌ 待修复（12项）

#1 #3 #4 #6 #7 #8 #10 #11 #13 #14 #16 #17 — 详见下方表格。

---

## 🔴 严重问题

### 1. `_row_to_record` 对 DuckDB tuple 结果使用 dict 索引，数据静默丢失

**位置：** `scripts/compute.py:200-224`

**问题：** `read_minute_bars` 调用 `conn.execute(...).fetchall()` 返回 DuckDB 的 **tuple 列表**，但 `_row_to_record` 用 `row["last_timestamp"]` 等 dict 风格索引访问。

```python
# compute.py:200-224
def _row_to_record(row) -> Optional[SnapshotRecord]:
    ts = parse_timestamp(row["last_timestamp"])    # TypeError: tuple indices must be integers...
    market_ts = parse_timestamp(row["market_timestamp"])
```

`TypeError` 被 except 捕获后返回 `None`，导致**所有分钟聚合数据被静默丢弃**，`read_minute_bars` 永远返回 `[]`。测试中使用了 MagicMock 绕过，生产环境也因 DuckDB 不同版本的行类型差异可能偶发失败。

**修复方案：**

方案 A（推荐）：统一将 `fetchall()` 结果转为 dict。

```python
# 在 read_minute_bars 中，获取列名后构造 dict
def read_minute_bars(ticker, target_date=None):
    ...
    rows = conn.execute(f"""
        SELECT ticker, last_timestamp, market_timestamp, market_date, market_minute,
               close, high, low, volume, turnover, change_pct
        FROM quote_minute_bars
        WHERE {where}
        ORDER BY market_date, market_minute, last_timestamp
    """, params).fetchall()
    columns = ["ticker", "last_timestamp", "market_timestamp", "market_date",
               "market_minute", "close", "high", "low", "volume", "turnover", "change_pct"]
    return [dict(zip(columns, row)) for row in rows]
```

然后 `_row_to_record` 不变，或改为接受实际 `SnapshotRecord` 而不是提层转换。

方案 B：`_row_to_record` 改用位置索引：
```python
def _row_to_record(row_tuple) -> Optional[SnapshotRecord]:
    try:
        ticker, last_ts, market_ts_str, market_date_str, market_minute, \
            close, high, low, volume, turnover, change_pct = row_tuple
        ts = parse_timestamp(last_ts)
        market_ts = parse_timestamp(market_ts_str)
        ...
```

---

### 2. `_resolve_session_time` 中 `_fallback_to_latest` 死代码分支

**位置：** `scripts/compute.py:288-304`

**问题：** 第一次 `_fallback_to_latest()` 返回空后，`records` 为空，条件 `if not records:` 进入，但第二次 `_fallback_to_latest()` 必然也返回空——死代码。更关键的是：**交易时段 `_records_for_date` 返回空时没有 fallback**，只有非交易时段才会尝试 fallback。

**修复方案：**

```python
def _resolve_session_time(ticker: str, current_time=None):
    if current_time is None:
        current_time = datetime.now()

    market = get_market(ticker)
    market_dt = _to_market_dt(current_time, market)
    market_date = market_dt.date()
    target_minute = market_dt.hour * 60 + market_dt.minute

    # 先尝试当日数据
    records = _records_for_date(ticker, market_date)

    # 无当日数据或非交易时段 → fallback 到最近交易日
    if not records or not _is_regular_session(market, market_dt):
        records = _fallback_to_latest(ticker)
        if records:
            market_dt, market_date, target_minute = \
                records[0].market_ts, records[0].market_date, records[0].market_minutes

    return market_dt, market_date, target_minute, records
```

---

### 3. `_duckdb_connect` 在 4 个文件中重复定义

**位置：** 以下文件各有一份完全相同的实现：

| 文件 | 行号 |
|------|------|
| `scripts/alert.py` | 12-20 |
| `scripts/cli.py` | 17-25 |
| `scripts/llm.py` | 18-26 |
| `scripts/feishu_bot.py` | 33-41 |

`scripts/compute.py` 使用另一种模式（`_get_persistent_conn` / 直接 `duckdb.connect`），是第 5 种变体。

**修复方案：** 提取到 `scripts/core/db.py`：

```python
"""共享 DuckDB 连接工具"""
import duckdb
import time
from pathlib import Path

_DB_CONNECTION_CACHE = {}

def duckdb_connect(db_path: Path, read_only: bool = False, retries: int = 3):
    """带重试的 DuckDB 连接，绕过 macOS 文件锁冲突。"""
    for attempt in range(retries):
        try:
            return duckdb.connect(str(db_path), read_only=read_only)
        except duckdb.IOException as e:
            if attempt < retries - 1 and "lock" in str(e).lower():
                time.sleep(0.1 * (attempt + 1))
                continue
            raise
```

所有引用方改为 `from core.db import duckdb_connect`。

---

### 4. `suppress_stdout` fd 级重定向过于激进

**位置：** `scripts/core/silence.py`

**问题：** 使用 `os.dup2` 将 fd 1/2 重定向到 `/dev/null` 会吞掉同进程中**所有线程**的输出（包括 C 扩展的 `printf`、子进程继承的 fd）。`collect_ws.py` 和 `market.py` 中多处使用。

```python
os.dup2(devnull.fileno(), 1)
os.dup2(devnull.fileno(), 2)
```

**修复方案：** 改用 Python 层的上下文管理器：

```python
import contextlib
import io

@contextlib.contextmanager
def suppress_stdout():
    """临时重定向 stdout/stderr 到 devnull（Python 层，不伤 fd）"""
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        yield
```

如果 SDK 输出走的是 stderr 而非 logging，`contextlib.redirect_stderr` 同样处理。若 SDK 确用 `print(..., file=sys.stderr)` 则此方案足够；若走原生 C `fprintf`，才需 fd 级别方案——但可以按需选择，不必一刀切。

---

### 5. `_build_batch_card` 可能超飞书卡片尺寸限制

**位置：** `scripts/alert.py`

**问题：** 当同时触发 30+ 个信号时，单张卡片包含所有告警 + LLM 分析 + 历史胜率，可能超过飞书 API 的卡片内容大小限制（约 100KB），导致推送失败。

**修复方案：** 对信号数量设上限，超出时分批发送：

```python
MAX_ALERTS_PER_CARD = 20

def _build_batch_card(alerts_with_analysis):
    """将多个告警合并为一张飞书卡片，超限时分批。"""
    batches = [alerts_with_analysis[i:i + MAX_ALERTS_PER_CARD]
               for i in range(0, len(alerts_with_analysis), MAX_ALERTS_PER_CARD)]
    cards = []
    for i, batch in enumerate(batches):
        card = _build_single_card(batch)
        if len(batches) > 1:
            card["header"]["title"]["content"] += f" ({i+1}/{len(batches)})"
        cards.append(card)
    return cards
```

---

## 🟡 设计问题

### 6. 模块级可变状态（全局单例）

**位置：** 
- `scripts/compute.py`：`_snapshot_cache`、`_minute_bar_cache`、`_minute_bar_presence_cache`、`_last_ratio_write`、`_db_initialized`、`_persistent_conn`
- `scripts/core/config.py`：`_config_cache`、`_config_mtime`
- `scripts/core/market.py`：`_trading_days_cache`、`_trading_days_range_cache`、`_trading_days_lookup_cache`

**问题：** 6 个模块级可变变量让代码不可重入、测试隔离靠手动清空缓存、`_persistent_conn` 声明为持久但实际每次新建连接。

**修复方案：** 按模块收敛：

- **compute.py**：将缓存移到 `class Calculator:` 内，通过 `with Calculator() as calc:` 管理生命周期。当前 `compute_all()` 这种无状态函数改为内部创建一次性 `Calculator` 实例。

```python
class Calculator:
    def __init__(self):
        self._snapshot_cache = {}
        self._minute_bar_cache = {}
        self._minute_bar_presence_cache = {}
        self._last_ratio_write = {}

    def calc_historical_ratio_detail(self, ticker, current_time=None):
        ...

    def calc_intraday_ratio_detail(self, ticker, current_time=None):
        ...
```

- **config.py**：热加载缓存可以保留（设计权衡），但增加 `clear_config_cache()` 供测试使用。
- **market.py**：同理，保留但暴露 `clear_trading_day_caches()` 供测试。

---

### 7. `sys.path.insert` 散布各处，依赖未管理

**位置：** 几乎所有入口文件顶部：

```python
sys.path.insert(0, str(ROOT / "scripts"))
```

**问题：** 若目录重命名、cwd 变化，导入静默失败。IDE 和类型检查器也无法解析这些动态路径。

**修复方案：**

1. 用可编辑安装替代：

```bash
pip install -e .
```

2. `pyproject.toml` 补充入口点：

```toml
[project.scripts]
volume-ratio = "scripts.cli:main"
collect-ws = "scripts.collect_ws:main"
```

3. 删除所有 `sys.path.insert(0, str(ROOT / "scripts"))`，改为标准导入：
   - `from core.config import load_config` 保持不变（`core/` 在 `scripts/` 下，已经是包）
   - `from compute import ...` → `from scripts.compute import ...`（安装后包名是 `scripts`）

---

### 8. 项目结构矛盾：`scripts/` 是包但非标准布局

**位置：** `pyproject.toml` + `scripts/__init__.py`

**问题：** `scripts/` 是 Python 包的命名空间（有 `__init__.py`），但 `scripts/` 字面意思是"脚本集合"不是"库包"。其他开发者（或工具）看到 `scripts/` 会理解为可执行脚本目录而不是可导入模块。标准做法是 `src/` 布局或直接项目名包。

**修复方案：** 

短期：在 `scripts/__init__.py` 中明确标为包文档字符串。
长期：迁移到 `src/volume_ratio/` 布局：

```
src/
  volume_ratio/
    __init__.py
    compute.py
    alert.py
    ...
    core/
      __init__.py
      config.py
      market.py
      ...
```

对应 `pyproject.toml` 中 `[tool.setuptools.packages.find]` 指向 `src`。

---

### 9. 阈值逻辑重复维护（`get_signal` vs `format_ratio_display`）

**位置：**
- `scripts/compute.py`：`get_signal()` 定义量比→信号映射
- `scripts/core/display.py`：`format_ratio_display()` 定义量比→符号映射

**问题：** 两者维护完全相同的阈值（0.6、0.8、1.2、2.0、5.0），一处修改另一处必然不同步。

**修复方案：** 提取共享的阈值表：

```python
# scripts/core/thresholds.py

RATIO_THRESHOLDS = [
    (float("-inf"), 0, "数据不足"),
    (0, 0.6, "缩量异常"),
    (0.6, 0.8, "缩量"),
    (0.8, 1.2, "正常"),
    (1.2, 2.0, "放量"),
    (2.0, 5.0, "显著放量"),
    (5.0, float("inf"), "巨量"),
]

def classify_ratio(ratio: float) -> str:
    for lo, hi, label in RATIO_THRESHOLDS:
        if lo < ratio <= hi:
            return label
    return "数据不足"
```

`get_signal()` 和 `format_ratio_display()` 都调用 `classify_ratio()`。

---

### 10. 缓存层独立、无协调失效

**问题：** Config 缓存（mtime 热加载）、Market 缓存（4h TTL）、Compute 缓存（LRU + mtime）三者独立。当 `backfill_signals.py` 写入新分钟数据后，compute 缓存的旧数据可能残留直到 LRU 驱逐。

**修复方案：** 引入统一的缓存失效事件，或降低 compute 缓存的 TTL 并增加写后失效标记：

```python
# compute.py
_cache_invalidated = False

def invalidate_caches():
    """数据写入后调用，标记缓存失效。"""
    global _cache_invalidated
    _cache_invalidated = True

def read_market_snapshots(ticker, target_date=None):
    global _cache_invalidated
    if _cache_invalidated:
        _snapshot_cache.clear()
        _minute_bar_cache.clear()
        _minute_bar_presence_cache.clear()
        _cache_invalidated = False
    ...
```

---

## 🟢 编码细节问题

### 11. `pyproject.toml` 缺少 CLI 入口点

**位置：** `pyproject.toml`

**问题：** 当前只能用 `python3 scripts/cli.py --ticker CLF.US`，不能通过 `volume-ratio --ticker CLF.US` 调用。

**修复方案：** 在 `pyproject.toml` 添加：

```toml
[project.scripts]
volume-ratio = "scripts.cli:main"
```

---

### 12. `_get_market_now` 时区假设不一致

**位置：** `scripts/compute.py`

**问题：** `_get_market_now` 使用市场时区，而 `_to_market_dt` 对 naive datetime 使用本机时区。若服务器时区与市场时区不一致，结果会有偏差。

**修复方案：** 统一策略——所有内部时间都带时区，不接受 naive datetime：

```python
def _to_market_dt(dt: datetime, market: str) -> datetime:
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {dt}")  # 硬拒绝
    return dt.astimezone(_market_tz(market))
```

调用方确保自己传带时区的时间。

---

### 13. 使用 `print` 而非 `logging`

**位置：** 全部 10+ 个源文件

**问题：** 所有日志都用 `print(f"[tag] ...", flush=True)`，无法按级别过滤、无法按模块控制、缺乏统一的时间戳格式、守护进程场景下输出混合。

**修复方案：** 引入标准 logging：

```python
# scripts/core/log.py
import logging

def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
```

各模块：
```python
logger = setup_logger(__name__)
logger.info("信号触发: %s", ticker)
logger.warning("API key 未配置")
logger.error("连接失败: %s", e)
```

---

### 14. 测试覆盖缺口

**位置：** `tests/`

**当前覆盖：**

| 文件 | 状态 |
|------|------|
| `tests/test_compute.py` | ✅ 核心计算，20+ 用例 |
| `tests/test_alert.py` | ✅ 信号检测，12 用例 |
| `tests/conftest.py` | ✅ fixtures + 工厂函数 |

**缺失覆盖：**

| 目标文件 | 行数 | 建议优先级 | 关键测试点 |
|----------|------|-----------|-----------|
| `feishu_bot.py` | ~1275 | 高 | 消息处理、monkey-patch 容错、卡片构建 |
| `collect_ws.py` | ~270 | 高 | 重连逻辑、队列排空、prev_close 缓存 |
| `llm.py` | ~150 | 中 | API 调用、切换 Profile、空 key 处理 |
| `core/config.py` | ~100 | 中 | 热加载、原子写入、parse_ticker |
| `core/market.py` | ~130 | 中 | 交易日查询（mock API）、时区处理 |
| `core/display.py` | ~130 | 低 | 纯函数格式化（但无测试也是债） |

**修复方案：** 为每个缺失文件创建对应的 `test_*.py`，至少覆盖：
- 正常路径
- 异常/边界路径（空数据、网络错误、API 超时）
- 文档中提及的"有意设计"的行为（如宽泛 except）

---

### 15. `cmd_status` 存在竞态条件

**位置：** `scripts/cli.py` — `cmd_status()`

**问题：** `locked_pid()` 和后续 `os.kill(pid, 0)` 之间进程可能退出。且 `os.kill(pid, 0)` 在某些容器/权限环境会抛 `PermissionError`。

**修复方案：**

```python
def _pid_is_alive(pid_file: Path, lock_file: Path) -> bool:
    """检查进程是否存活，结合 lock 和 pid 文件。"""
    try:
        pid_text = pid_file.read_text().strip()
        if not pid_text:
            return False
        pid = int(pid_text)
        os.kill(pid, 0)
        return True
    except (ValueError, OSError, FileNotFoundError):
        return False
```

---

### 16. `start_all.py` 依赖 crontab，Docker 环境不工作

**位置：** `scripts/start_all.py`

**问题：** `add_cron` 通过 `subprocess.run(["crontab", "-l"])` 管理 cron 任务。在 Docker 容器中 cron daemon 通常不运行，在受限环境可能无权限。

**修复方案：** 检测 crontab 可用性并给出清晰的 warning，提供 `--no-cron` 参数：

```python
def add_cron(line: str):
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("[start] ⚠️ crontab 不可用（Docker/受限环境），请手动配置定时任务")
        return
    ...
```

---

### 17. 部分函数缺少类型注解

**位置：** 多个函数

**问题：** 部分函数有完整的类型注解（如 `SnapshotRecord` dataclass），部分没有（如 `_get_persistent_conn`、`get_jsonl_path`、`_snapshot_files`）。返回 `Optional[dict]` 而非具体的 TypedDict 或 dataclass 导致调用方无从得知返回结构。

**修复方案：** 逐步补充类型注解，至少：
- 所有公开函数（被其他模块调用的）
- 从 `compute.py` 导出的函数
- 核心数据模型使用 TypedDict 或 dataclass

---

### 18. `get_signal_detail` 中尾盘判断硬编码 14:30

**位置：** `scripts/compute.py`

```python
def get_signal_detail(ratio, price_change=0, market="CN"):
    ...
    if ratio > 1.5 and market == "CN":
        now = _get_market_now(market)
        if (now.hour == 14 and now.minute >= 30) or now.hour == 15:
            return "尾盘放量"
```

**问题：** `MARKET_END_OF_DAY` 已经在 `config.py` 中定义了（CN=14:30），但 `get_signal_detail` 还是硬编码了 `14:30` 和 `15:00`。

**修复方案：** 引用 `MARKET_END_OF_DAY`：

```python
from core.config import MARKET_END_OF_DAY

def _is_end_of_day_for_signal(market: str) -> bool:
    eod = MARKET_END_OF_DAY.get(market)
    if not eod:
        return False
    now = _get_market_now(market)
    return (now.hour > eod[0]) or (now.hour == eod[0] and now.minute >= eod[1])
```

注意：`MARKET_END_OF_DAY` 的含义是"收盘前 30 分钟"，如果描述的是结束时间点，建议改名为 `MARKET_EARLY_CLOSE_START` 或补充注释明确语义。

---

## 📊 优先级汇总

| 编号 | 问题 | 严重度 | 修复成本 | 状态 |
|------|------|--------|---------|------|
| #1 | `_row_to_record` dict 索引 → 数据静默丢失 | 🔴 | 低 | ❌ 待观察（DuckDB 1.5.3 下 duckdb.Row 支持 dict 访问，当前无问题） |
| #2 | `_resolve_session_time` 死代码 + fallback 缺失 | 🔴 | 低 | ✅ 已修复 (compute.py) |
| #3 | `_duckdb_connect` 4 副本 | 🟡 | 中 | ❌ 待修复（需建 core/db.py） |
| #4 | `suppress_stdout` fd 级激进 | 🟡 | 低 | ❌ 待修复 |
| #5 | 卡片可能超尺寸 | 🟡 | 低 | ✅ 已修复 (alert.py MAX_ALERTS_PER_CARD) |
| #6 | 模块级全局状态 | 🟡 | 高 | ❌ 下轮（需重构 Calculator 类） |
| #7 | `sys.path.insert` 散布 | 🟡 | 中 | ❌ 待修复 |
| #8 | 项目结构矛盾 | 🟡 | 高 | ❌ 下轮（迁移 src/ 布局） |
| #9 | 阈值重复维护 | 🟡 | 低 | ✅ 已修复 (core/thresholds.py) |
| #10 | 缓存无协调失效 | 🟡 | 低 | ❌ 待修复 |
| #11 | pyproject.toml 缺入口点 | 🟢 | 低 | ❌ 待修复 |
| #12 | 时区假设不一致 | 🟢 | 低 | ✅ 已确认无害 |
| #13 | 没有 logging 框架 | 🟢 | 中 | ❌ 待修复 |
| #14 | 测试覆盖缺口 | 🟢 | 高 | ❌ 持续补充 |
| #15 | `cmd_status` 竞态 | 🟢 | 低 | ✅ 已修复 (cli.py _pid_is_alive) |
| #16 | start_all 依赖 crontab | 🟢 | 低 | ❌ 待修复 |
| #17 | 缺少类型注解 | 🟢 | 中 | ❌ 持续补充 |
| #18 | 尾盘时间硬编码 | 🟢 | 低 | ✅ 已修复 (引用 MARKET_END_OF_DAY) |

---

## 引用说明

以上行号引用基于当前代码库版本。如文件已修改，请以最新内容为准。
