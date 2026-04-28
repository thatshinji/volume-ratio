# 跨市场量比监控系统

实时监控 US/HK/CN 三大市场股票的成交量异动，结合 LLM 智能分析，信号触发即时推送飞书。

---

## 一、项目概述

### 1.1 核心能力

- **双量比引擎**：同时运行日内滚动量比（立即生效）和5日历史量比（需要数据积累）
- **多市场覆盖**：美股(US)、港股(HK)、A股(CN) 三大市场
- **智能信号检测**：放量突破、放量下跌、缩量止跌、尾盘放量等
- **LLM 多模型切换**：一键切换 MiniMax / Xiaomi 等模型，自动分析量比异常原因
- **飞书实时推送**：触发信号时即时推送，包含价格/量比/LLM解读

### 1.2 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户层                                  │
│         飞书推送 / CLI 命令行 / 日志查看                      │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    脚本层                                    │
│  collect_ws.py  │  compute.py  │  alert.py  │  cli.py  │ llm.py│
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    数据层                                    │
│        snapshots/         │         ratios.db               │
│     (行情原始快照)          │        (SQLite量比历史)           │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    数据源层                                   │
│            Longbridge WebSocket API                         │
│         (美股/港股/A股 实时行情)                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、目录结构

```
volume-ratio/
├── config.yaml              # 配置文件（标的/参数/LLM/飞书）
├── pyproject.toml           # 项目元数据
│
├── scripts/
│   ├── collect.py           # [弃用] cron轮询采集（旧方案）
│   ├── collect_ws.py        # WebSocket实时行情采集（主方案）
│   ├── collect_ws_launcher.py  # cron守护进程启动器
│   ├── compute.py           # 量比计算引擎（核心）
│   ├── alert.py             # 信号检测 + 飞书推送
│   ├── cli.py               # 命令行入口
│   └── llm.py               # LLM多模型调用层
│
├── data/
│   ├── snapshots/           # 行情快照（按市场分目录）
│   │   ├── US/              #   CLF_US_20260429_015832.json
│   │   ├── HK/              #   1810_HK_20260429_093000.json
│   │   └── CN/              #   601899_SH_20260429_093005.json
│   └── ratios.db            # SQLite数据库（量比历史记录）
│
└── logs/                    # 运行日志
    ├── ws_collect.log        # WebSocket采集进程日志
    ├── ws_collect.err        # WebSocket采集错误日志
    └── launcher.log          # 守护进程启动器日志
```

---

## 三、核心概念：量比

### 3.1 什么是量比

量比 = 当前时段成交量 / 历史同期平均成交量

| 量比范围 | 信号 | 含义 |
|:--:|:--|:--|
| < 0.6 | 缩量异常 | 流动性极低，可能止跌 |
| 0.6 - 0.8 | 缩量 | 无人关注 |
| 0.8 - 1.2 | 正常 | 正常交易活跃度 |
| 1.2 - 2.0 | 放量 | 有资金关注 |
| 2.0 - 5.0 | 显著放量 | 突破或出货信号 |
| > 5.0 | 巨量 | 重大事件 |

### 3.2 双量比系统

本系统同时运行两套量比计算逻辑：

#### 日内滚动量比（立即生效）
- **原理**：今日最近N分钟成交量 vs 今日最近基线窗口成交量
- **窗口**：信号窗口5个间隔(~2.5分钟) vs 基线窗口10个间隔(~5分钟)
- **优势**：今天就能用，不需要历史数据
- **判断**：
  - 放量：量比 > 1.5
  - 止跌：信号期最低价 >= 基线最低价 × 0.995
  - 企稳：最新价 > 信号期最低价 × 1.005

#### 5日历史量比（需要数据积累）
- **原理**：今日同时段成交量 vs 过去5日同一时段平均成交量
- **优势**：消除日内节律（开盘放量/尾盘缩量）
- **局限**：需要至少5个交易日数据才能生效

---

## 四、配置文件

### 4.1 完整配置示例
项目所需 config.yml 主要存放 llm 配置 & 量比参数
```yaml
# 监控标的
watchlist:
  us:
    - NVDL.US    # 2x NVDA
    - MUU.US     # 2x 美光
    - CLF.US     # 持仓
    - BMNR.US    # 持仓
    - DRAM.US    # 持仓
    - NVO.US     # 持仓
  hk:
    - 1810.HK    # 小米
    - 700.HK     # 腾讯
    - 9988.HK    # 阿里
  cn:
    - 601899.SH  # 紫金矿业
    - 603308.SH  # 应流股份

# 参数
params:
  volume_ratio_window: 5        # 历史对比天数
  snapshot_interval: 60         # 采集间隔（秒）
  alert_threshold: 2.0          # 放量告警阈值
  shrink_threshold: 0.6          # 缩量告警阈值

# LLM 配置（支持多模型切换）
llm:
  provider: "minimax"            # 当前使用: minimax / xiaomi
  model: "MiniMax-M2.7"
  base_url: "https://api.minimaxi.com/anthropic"
  api_key: "sk-xxx"
  max_tokens: 200
  temperature: 0.3

# LLM 模型配置（切换时使用）
llm_profiles:
  minimax:
    provider: "minimax"
    model: "MiniMax-M2.7"
    base_url: "https://api.minimaxi.com/anthropic"
    api_key: "sk-xxx"
  xiaomi:
    provider: "xiaomi"
    model: "mimo-v2.5-pro"
    base_url: "https://token-plan-cn.xiaomimimo.com/anthropic"
    api_key: "tp-xxx"

# 飞书推送
feishu:
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
```

---

## 五、脚本详解

### 5.1 行情采集 - collect_ws.py

WebSocket 实时推送模式，解决后台运行文件丢失问题。

**前台运行**：
```bash
python3 scripts/collect_ws.py
```

**后台守护运行**：
```bash
python3 scripts/collect_ws.py --daemon
```

**原理**：
- 回调线程只管入队（`quote_queue.put()`）
- 主线程负责写出到磁盘（`f.flush()` + `os.fsync()`）
- 双进程守护：`collect_ws_launcher.py` 每分钟检查进程是否存活

**快照数据结构**：
```json
{
  "ticker": "CLF.US",
  "timestamp": "2026-04-29T01:58:32.366093",
  "price": 10.475,
  "open": 10.41,
  "high": 10.57,
  "low": 9.905,
  "volume": 17705720,        # 累计成交量（持续增长）
  "turnover": 181445719.732,
  "change": -0.12,
  "change_pct": -1.13
}
```

### 5.2 量比计算 - compute.py

核心计算引擎，同时计算日内和历史两种量比。

**计算单标的**：
```bash
python3 scripts/compute.py CLF.US
```

**计算所有标的**：
```bash
python3 scripts/compute.py
```

**输出字段**：
```json
{
  "ticker": "CLF.US",
  "ratio": 0.0,              // 5日历史量比（需5日数据才生效）
  "ratio_intraday": 1.74,    // 日内滚动量比（立即可用）
  "volume_today": 106902.0,  // 今日差分成交量
  "volume_avg5": 0.0,        // 5日平均成交量
  "price": 10.45,
  "change_pct": -1.51,
  "signal": "数据不足",       // 基于历史量比
  "signal_detail": "",
  "signal_intraday": "放量",  // 基于日内量比
  "cond_vol": true,          // 放量条件
  "cond_stop": true,         // 止跌条件
  "cond_stable": false       // 企稳条件
}
```

### 5.3 信号检测与推送 - alert.py

检测量比异常信号，调用 LLM 分析后推送到飞书。

**普通扫描（仅信号检测）**：
```bash
python3 scripts/alert.py
```

**发送简报（含LLM解读）**：
```bash
python3 scripts/alert.py --brief
```

**LLM 调用限制**：
- 只对强信号（放量突破/放量下跌/巨量>2.5）调用 LLM
- 同一 ticker 多个信号源时只调一次
- 节省 API 额度，预计 50-100 次/天

**飞书消息格式**：
```
🔥 【日内】MUU.US
当前价: 263.0 (↓5.85%)
量比: 2.1 (放量)
时间: 02:29:43

[LLM分析] 下跌中放量，可能伴随恐慌抛售...
```

### 5.3.1 飞书推送消息模板

#### 普通放量信号（无 LLM）

当量比异常但不满足强信号条件时，推送基本信息：

```
⚠️ 【5日】BMNR.US
当前价: 21.629 (↑0.37%)
量比: 0.0 (缩量止跌)
时间: 02:56:45
```

#### 强信号触发 LLM 分析

当满足「放量突破」或「放量下跌」时，推送包含 LLM 解读：

```
🔥 【日内】CLF.US
当前价: 10.475 (↓1.27%)
量比: 2.01 (放量)
时间: 02:31:56

[LLM分析] CLF放量2.01且下跌1.27%，可能伴随恐慌抛售。
短线偏弱，建议等量比>2.5+价格站稳再关注。
```

#### 30分钟简报（--brief 模式）

每30分钟发送一次持仓组合概况：

```
📊 量比简报 02:30

🇺🇸 美股:
  MUU.US  ↓5.9%  量比2.1  🔥 巨量
  CLF.US  ↓1.3%  量比2.0  🔥 显著放量
  NVDL.US ↓3.4%  量比3.8  🔥 巨量

🇭🇰 港股:
  1810.HK  ↑0.5%  量比0.8  ⚠️ 缩量

🇨🇳 A股:
  601899.SH  ↑1.2%  量比1.1  ✅ 正常

[LLM解读] 市场整体偏弱，美股大宗商品板块领跌，
建议关注 CLF 是否能在 10 元上方企稳...
```

#### 消息前缀说明

| 前缀 | 含义 |
|:--|:--|
| `【日内】` | 日内滚动量比信号（立即可用，今天的数据） |
| `【5日】` | 5日历史量比信号（需要5日数据积累） |
| `🔥` | 量比 > 2.0（显著放量/巨量） |
| `⚠️` | 量比 < 0.8（缩量异常） |
| `✅` | 量比正常（0.8-1.2） |

### 5.4 命令行入口 - cli.py

随时查询任意标的的量比，支持 AI 分析。

**查询单个标的**：
```bash
python3 scripts/cli.py --ticker CLF.US
# CLF.US  $10.48  ↓1.23%  量比: 0.0 (数据不足) ⚠️
```

**带 LLM 分析**：
```bash
python3 scripts/cli.py --ticker CLF.US --analyze
```

**扫描所有持仓**：
```bash
python3 scripts/cli.py --scan holdings
```

**扫描市场内放量标的**：
```bash
python3 scripts/cli.py --market US --min-ratio 2.0
```

**采集最新行情再查询**：
```bash
python3 scripts/cli.py --collect --ticker CLF.US
```

### 5.5 LLM 多模型切换 - llm.py

统一封装 LLM 调用，支持一键切换模型。

**查看所有可用模型**：
```bash
python3 scripts/llm.py --list
# 可用模型: ['minimax', 'xiaomi']
# 当前使用: minimax
```

**切换模型**：
```bash
python3 scripts/llm.py --switch xiaomi
# [llm] 已切换到: xiaomi (mimo-v2.5-pro)
# 切换成功，当前模型: mimo-v2.5-pro
```

**测试当前配置**：
```bash
python3 scripts/llm.py --test
```

**添加新模型**：在 `config.yaml` 的 `llm_profiles` 下新增配置：

```yaml
llm_profiles:
  openai:
    provider: "openai"
    model: "gpt-4o"
    base_url: "https://api.openai.com/v1"
    api_key: "sk-xxx"
```

---

## 六、定时任务配置

### 6.1 cron 任务列表

```cron
# 每分钟检查并确保 WebSocket 采集进程运行
*/1 * * * * python3 /Users/shinji/project-x/volume-ratio/scripts/collect_ws_launcher.py

# 每分钟扫描信号（alert.py）
*/1 * * * * cd /Users/shinji/project-x/volume-ratio && python3 scripts/alert.py >> logs/alert.log 2>&1

# 每30分钟发送简报（可选）
*/30 * * * * cd /Users/shinji/project-x/volume-ratio && python3 scripts/alert.py --brief >> logs/brief.log 2>&1
```

### 6.2 进程管理

```bash
# 查看 WebSocket 采集进程
cat logs/ws_collect.pid

# 查看运行状态
ps aux | grep collect_ws

# 手动重启
python3 scripts/collect_ws_launcher.py
```

---

## 七、数据存储

### 7.1 快照文件

存储位置：`data/snapshots/{市场}/{ticker}_{date}_{time}.json`

命名规则：`CLF_US_20260429_015832.json` = `{ticker}_{market}_{date}_{time}.json`

### 7.2 SQLite 数据库

存储位置：`data/ratios.db`

表结构：

```sql
-- 量比实时记录
CREATE TABLE volume_ratios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    ratio REAL,
    volume_today REAL,
    volume_avg5 REAL,
    price REAL,
    change_pct REAL,
    signal TEXT,
    UNIQUE(ticker, timestamp)
);

-- 每日汇总
CREATE TABLE daily_summary (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    avg_ratio REAL,
    max_ratio REAL,
    min_ratio REAL,
    final_price REAL,
    final_change_pct REAL,
    signal TEXT,
    UNIQUE(ticker, date)
);
```

---

## 八、信号检测与推送规则

### 8.1 量比阈值规则（alert.py）

```python
SIGNAL_RULES = {
    "放量突破": lambda ratio, change: ratio > 2.0 and change > 2,
    "放量下跌": lambda ratio, change: ratio > 2.0 and change < -2,
    "缩量止跌": lambda ratio, change: ratio < 0.6 and change > 0,
    "尾盘放量": lambda ratio, change: ratio > 1.5 and 14 <= hour <= 15,
}
```

### 8.2 日内三条件（compute.py）

```python
# 条件 1: 放量
cond_vol = vol_ratio > 1.5

# 条件 2: 止跌（信号期最低价 >= 基线最低价 × 0.995）
cond_stop = sig_min_low >= base_min_price * 0.995

# 条件 3: 企稳（最新价 > 信号期最低价 × 1.005）
cond_stable = latest_price > sig_min_price * 1.005
```

### 8.3 LLM 调用策略（省 API 额度）

LLM 只用于解读强信号，避免浪费调用。调用条件：

```python
is_significant = (
    signal in ("放量突破", "放量下跌") or    # 量比>2.0 且 |change|>2%
    (ratio > 2.5 and change_pct != 0)       # 巨量（ratio>2.5）才解读
)
```

**调用频率估算**：
- 普通扫描（每分钟）：无 LLM 调用 → 0次
- 强信号触发时：每个 ticker 最多 1 次
- 预计：50-100 次/天（远低于 MiniMax 7200次/天额度）

**同一 ticker 限制**：同一标的多个信号源（5日+日内）时，LLM 只调用一次。

---

## 九、故障排查

### 9.1 常见问题

**Q: 量比显示 0.0 "数据不足"**
- 5日历史量比需要5个交易日数据才生效
- 查看 `ratio_intraday` 日内滚动量比，今天就能用

**Q: 飞书推送失败**
- 检查 `webhook_url` 是否正确
- 检查网络连接

**Q: WebSocket 进程不存在**
- 查看日志：`cat logs/launcher.log`
- 手动重启：`python3 scripts/collect_ws_launcher.py`

**Q: LLM API 调用失败**
- 确认 `config.yaml` 中 api_key 正确
- 测试连接：`python3 scripts/llm.py --test`
- 切换模型：`python3 scripts/llm.py --switch minimax`

### 9.2 日志位置

```
logs/
├── ws_collect.log      # WebSocket 采集主日志
├── ws_collect.err      # WebSocket 错误日志
├── ws_collect.pid      # 进程 PID
└── launcher.log        # 守护进程启动日志
```

### 9.3 数据验证

```bash
# 查看今日快照数量
ls data/snapshots/US/ | grep $(date +%Y%m%d) | wc -l

# 查看最新快照
ls -lt data/snapshots/US/ | head -5

# 查看数据库记录
sqlite3 data/ratios.db "SELECT * FROM volume_ratios ORDER BY timestamp DESC LIMIT 5;"
```

---

## 十、依赖

```toml
# pyproject.toml
[project]
requires-python = ">=3.9"
dependencies = [
    "pyyaml",
    "requests",
    "longbridge>=2.0.0",
]
```

安装依赖：
```bash
cd /Users/shinji/project-x/volume-ratio
source .venv/bin/activate
pip install pyyaml requests
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|:--|:--|:--|
| v1.0 | 2026-04-28 | 初始实现方案 |
| v2.0 | 2026-04-29 | 切换 WebSocket 推送模式，新增日内滚动量比，LLM 多模型切换 |

---

> 维护者：shinji | 技术支持：Claude Code + Longbridge OpenAPI