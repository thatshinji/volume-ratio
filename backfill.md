# 信号结果追踪（Signal Outcome Tracker）

## Context

当前量比系统在信号触发时记录了 ticker、价格、量比、信号类型，但**没有追踪信号触发后的实际走势**。无法回答"放量突破信号历史上胜率多少？"这类问题。

本方案目标：信号触发后自动回填后续价格走势，在飞书卡片中展示历史胜率，帮助用户判断信号可信度。

## 现有数据基础

| 数据 | 位置 | 现状 |
|:--|:--|:--|
| 信号记录 | `signals` 表 | 225 条，含 ticker/price/ratio/signal_type，**无后续走势** |
| 分钟行情 | `quote_minute_bars` 表 | 20,580 条，可回溯信号后价格 |
| 原始快照 | `data/snapshots/*/*.jsonl` | 按天按标的存储，20 天保留 |
| 保留期 | `cleanup.py` | signals 20 天，minute_bars 20 天 |

## 实现方案

### 1. 数据库 Schema 升级 (v3 → v4)

在 `signals` 表新增 6 列，记录信号触发后 T+1/T+3/T+5 的价格和收益率：

```sql
ALTER TABLE signals ADD COLUMN market TEXT DEFAULT '';
ALTER TABLE signals ADD COLUMN exit_price_1d REAL;
ALTER TABLE signals ADD COLUMN exit_price_3d REAL;
ALTER TABLE signals ADD COLUMN exit_price_5d REAL;
ALTER TABLE signals ADD COLUMN return_1d REAL;
ALTER TABLE signals ADD COLUMN return_3d REAL;
ALTER TABLE signals ADD COLUMN return_5d REAL;
```

**字段说明：**

| 字段 | 含义 | 计算方式 |
|:--|:--|:--|
| `market` | 市场 (US/HK/CN) | 从 ticker 后缀推导 |
| `exit_price_1d` | T+1 日收盘价 | 信号触发后下一个交易日的收盘价 |
| `exit_price_3d` | T+3 日收盘价 | 信号触发后第 3 个交易日的收盘价 |
| `exit_price_5d` | T+5 日收盘价 | 信号触发后第 5 个交易日的收盘价 |
| `return_1d` | T+1 收益率 | `(exit_price_1d - price) / price * 100` |
| `return_3d` | T+3 收益率 | `(exit_price_3d - price) / price * 100` |
| `return_5d` | T+5 收益率 | `(exit_price_5d - price) / price * 100` |

**升级位置：** `scripts/compute.py` 的 `init_db()` 函数，新增 schema v4 迁移逻辑。

### 2. 新增 `backfill_signals.py` 脚本

**职责：** 扫描 `signals` 表中尚未回填的信号，从 `quote_minute_bars` 查询后续收盘价，回填 exit_price 和 return 字段。

**核心逻辑：**

```python
def backfill_signal_results():
    """
    1. 查询 signals 表中 return_5d IS NULL 且信号时间在 5 个交易日前的记录
    2. 对每条信号：
       a. 从 quote_minute_bars 查询 T+1/T+3/T+5 交易日的收盘价
          - 收盘价 = 该交易日最后一根分钟 bar 的 close
          - 交易日判断复用 core.market.is_trading_day_on()
       b. 计算 return_1d/3d/5d
       c. UPDATE signals 表
    3. 输出统计摘要
    """
```

**查询后续收盘价的 SQL：**

```python
# 获取指定 ticker 在某交易日的收盘价（最后一根分钟 bar 的 close）
SELECT close FROM quote_minute_bars
WHERE ticker = ? AND market_date = ?
ORDER BY market_minute DESC LIMIT 1
```

**获取 T+N 交易日：**

复用 `core.market.is_trading_day_on()` 逐日向前推进，跳过非交易日：

```python
def get_nth_trading_day(market: str, from_date: date, n: int) -> date:
    """从 from_date 开始（不含），找到第 n 个交易日"""
    d = from_date
    count = 0
    while count < n:
        d += timedelta(days=1)
        if is_trading_day_on(market, d):
            count += 1
    return d
```

**写入位置：** `scripts/backfill_signals.py`（新文件）

**执行方式：**
- 手动执行：`python3 scripts/backfill_signals.py`
- Cron 自动执行：加入 `start_all.py`，每天收盘后运行一次（如 `0 17 * * 1-5`，美东 17:00，此时三个市场均已收盘）

### 3. 信号保存时同步写入 market 字段

修改 `scripts/compute.py` 的 `save_signal()` 函数，新增 `market` 参数：

```python
def save_signal(ticker: str, name: str, signal_type: str, ratio: float,
                price: float, change_pct: float, source: str = "",
                llm_analysis: str = "", notified: int = 1, market: str = ""):
```

同时修改 `scripts/alert.py` 中调用 `save_signal()` 的位置，传入 `market`：

```python
# alert.py scan_and_alert() 中：
from core.market import get_market
save_signal(..., market=get_market(ticker))
```

### 4. 保留期调整

修改 `scripts/cleanup.py`：

```python
SIGNAL_KEEP_DAYS = 20  # → 改为 90
```

理由：20 天只保留约 14 个交易日，不够做 T+5 回填 + 统计。90 天（约 60 个交易日）足以积累有意义的胜率数据，且 signals 表数据量很小（当前 225 条/7 天，90 天约 3000 条，占用 < 1MB）。

### 5. 统计查询函数

在 `scripts/compute.py` 中新增统计函数：

```python
def get_signal_stats(signal_type: str = "", ticker: str = "",
                     days: int = 30) -> dict:
    """
    查询指定条件的信号胜率统计。

    返回:
    {
        "total": 24,
        "win_1d": 14, "win_rate_1d": 58.3,
        "win_3d": 16, "win_rate_3d": 66.7,
        "win_5d": 15, "win_rate_5d": 62.5,
        "avg_return_1d": 1.2,
        "avg_return_3d": 2.1,
        "avg_return_5d": 1.8,
    }
    """
```

**SQL：**

```sql
SELECT
    COUNT(*) as total,
    SUM(CASE WHEN return_1d > 0 THEN 1 ELSE 0 END) as win_1d,
    AVG(return_1d) as avg_return_1d,
    SUM(CASE WHEN return_3d > 0 THEN 1 ELSE 0 END) as win_3d,
    AVG(return_3d) as avg_return_3d,
    SUM(CASE WHEN return_5d > 0 THEN 1 ELSE 0 END) as win_5d,
    AVG(return_5d) as avg_return_5d
FROM signals
WHERE return_5d IS NOT NULL
  AND timestamp >= ?
  AND (signal_type = ? OR ? = '')
  AND (ticker = ? OR ? = '')
```

### 6. 飞书卡片展示

#### 6a. 信号告警卡片 — 添加历史胜率

修改 `scripts/alert.py` 的 `_build_batch_card()`，在每个信号行旁展示该信号类型的历史胜率：

```
🔥 【5日】CLF.US-克利夫兰 ↑
当前价: $10.31 (+2.5%)
主触发量比(5日): 2.35 ⬆⬆  显著放量
信号: 放量突破 ↑
历史胜率: 58% (14/24) | 平均+1.2% (3日)
时间: 14:30:00
```

**实现：** 在 `_build_batch_card()` 中调用 `get_signal_stats(signal_type)` 获取胜率，插入到 LLM 分析之前。

#### 6b. 简报卡片 — 展示信号统计摘要

修改 `scripts/alert.py` 的 `send_brief_report()`，在简报底部添加最近 30 天的信号统计：

```
📊 信号统计（近30天）
放量突破: 58% 胜率 (14/24)，平均+1.2% (3日)
放量下跌: 45% 胜率 (5/11)，平均-0.8% (3日)
缩量止跌: 67% 胜率 (4/6)，平均+0.5% (3日)
```

**实现：** 遍历 `SIGNAL_RULES` 的信号类型，调用 `get_signal_stats(type)`，构建 markdown 文本追加到 elements。

#### 6c. `/signals` 命令 — 信号表格增加胜率列

修改 `scripts/feishu_bot.py` 的 `build_signals_card()`，在表格底部添加统计摘要（同 6b 格式）。

### 7. LLM prompt 增强

修改 `scripts/alert.py` 的 `PROMPT_BRIEF_TEMPLATE`，注入信号统计上下文：

```python
PROMPT_BRIEF_TEMPLATE = """你是量比分析专家。以下是当前持仓组合的量比简报：

{brief_text}

近期信号表现参考：
{signal_stats_text}

请用中文简短分析：
1. 当前市场整体情绪（哪些标的值得关注）
2. 是否有异常信号需要关注
3. 结合历史信号表现给出风险提示
限制150字以内。"""
```

## 涉及文件

| 文件 | 改动类型 | 说明 |
|:--|:--|:--|
| `scripts/compute.py` | 修改 | schema v4 迁移、`save_signal()` 加 market 参数、新增 `get_signal_stats()` |
| `scripts/backfill_signals.py` | **新建** | 信号结果回填脚本 |
| `scripts/alert.py` | 修改 | `_build_batch_card()` 展示胜率、`send_brief_report()` 展示统计、`save_signal()` 传 market |
| `scripts/feishu_bot.py` | 修改 | `build_signals_card()` 底部加统计摘要 |
| `scripts/cleanup.py` | 修改 | `SIGNAL_KEEP_DAYS` 20 → 90 |
| `scripts/start_all.py` | 修改 | 新增 backfill_signals.py cron 任务 |

## 执行顺序

1. `compute.py` — schema v4 迁移 + `save_signal()` 改造 + `get_signal_stats()`
2. `cleanup.py` — 保留期调整
3. `backfill_signals.py` — 新建回填脚本
4. `alert.py` — 卡片展示胜率 + 统计摘要 + LLM prompt 增强
5. `feishu_bot.py` — `/signals` 命令增加统计
6. `start_all.py` — 注册 cron 任务
7. 手动执行一次 `python3 scripts/backfill_signals.py` 回填历史数据

## 验证

1. `python3 -m py_compile scripts/compute.py scripts/alert.py scripts/feishu_bot.py scripts/cleanup.py scripts/backfill_signals.py`
2. 手动执行 `python3 scripts/backfill_signals.py`，检查 exit_price 和 return 是否正确回填
3. 执行 `python3 scripts/alert.py --brief`，检查简报底部是否有信号统计
4. 在飞书发送 `/signals`，检查底部是否有统计摘要
5. 触发一个测试信号，检查告警卡片中是否展示历史胜率
