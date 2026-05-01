# 跨市场量比监控系统

实时监控 US/HK/CN 三大市场股票的成交量异动，结合 LLM 智能分析，信号触发即时推送飞书卡片。支持飞书机器人交互指令，可通过卡片按钮直接管理监控标的、查看信号历史、同步长桥持仓。

---

## 一、项目概述

### 1.1 核心能力

- **双量比引擎**：同时运行5日历史同期量比（主量比）和日内滚动量比（短线异动）
- **多市场覆盖**：美股(US)、港股(HK)、A股(CN) 三大市场
- **智能信号检测**：放量突破、放量下跌、缩量止跌、尾盘放量等
- **交易日过滤**：通过 Longbridge trading_days API 按市场和日期过滤历史样本，假期/周末不推送
- **分钟级计算**：WebSocket 快照实时聚合为 SQLite 分钟线，量比计算读取分钟聚合表；REST API 仅用于补充最新价格和涨跌幅
- **LLM 多模型切换**：一键切换 MiniMax / Xiaomi 等模型，自动分析量比异常原因
- **飞书机器人**：WebSocket 长连接，支持交互指令（/status /scan /signals /brief /watchlist /allstock /sync /start /stop /mute /history）
- **交互式卡片**：关注列表可删除、全部股票可添加、长桥持仓自动同步
- **信号去重**：同一标的会合并 5日与日内信号，再用状态机判断是否推送；支持 /mute 静默（自动过期）
- **JSONL + SQLite 存储**：JSONL 保存可回放行情快照，SQLite schema v3 保存分钟聚合、量比结果、原始快照索引和信号历史
- **中文名标识**：标的显示中文名（如 `CLF.US 克利夫兰`），量比用符号+中文双标识

### 1.2 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户层                                  │
│    飞书机器人（交互指令） / CLI 命令行 / 飞书信号推送          │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    脚本层                                    │
│  collect_ws.py │ compute.py │ alert.py │ cli.py │ feishu_bot.py │ llm.py │
│  core/config.py │ core/market.py │ core/display.py          │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    数据层                                    │
│     snapshots/*.jsonl        │         ratios.db            │
│  (JSONL行情快照，按天追加)     │ (SQLite分钟聚合+量比+信号历史) │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                    数据源层                                   │
│            Longbridge WebSocket API                         │
│         (美股/港股/A股 实时行情)                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、快速开始

### 2.1 环境准备

```bash
cd /Users/shinji/project-x/volume-ratio
python3 -m venv .venv
source .venv/bin/activate
pip install pyyaml requests longbridge lark-oapi
```

### 2.2 配置文件

复制并编辑配置文件：
```bash
cp config.yaml.example config.yaml
# 编辑 config.yaml，填入：
#   - watchlist: 监控标的（格式: TICKER.MARKET-中文名）
#   - llm: LLM API 配置
#   - feishu: 飞书 app_id + app_secret
```

配置格式示例：
```yaml
watchlist:
  us:
    - NVDL.US-英伟达2x
    - CLF.US-克利夫兰
  hk:
    - 1810.HK-小米集团
  cn:
    - 601899.SH-紫金矿业

params:
  volume_ratio_window: 5              # 历史同期量比：过去 N 个交易日
  intraday_signal_window_minutes: 5   # 日内滚动量比：最近 W 分钟
  intraday_baseline_minutes: 30       # 日内滚动量比：前 B 分钟基线
  intraday_baseline_method: mean      # mean / median
  intraday_alert_threshold: 1.5

llm:
  provider: "xiaomi"
  model: "mimo-v2.5-pro"
  base_url: "https://token-plan-cn.xiaomimimo.com/anthropic"
  api_key: "tp-xxx"

feishu:
  app_id: "cli_xxx"
  app_secret: "xxx"
```

### 2.3 一键启停

```bash
# 一键启动所有服务（cron + WebSocket + 飞书机器人）
python3 scripts/start_all.py

# 一键关停所有服务
python3 scripts/stop_all.py
```

### 2.4 历史数据回填

如果本地已经有历史 JSONL 快照，首次升级到 schema v3 后建议回填一次分钟聚合表：

```bash
python3 scripts/backfill_minute_bars.py
```

回填后 `compute.py` 和 `alert.py` 会优先读取 `quote_minute_bars`，不会每分钟扫描全量 JSONL。后续 WebSocket 写入新快照时会自动同步更新分钟聚合表。

---

## 三、目录结构

```
volume-ratio/
├── config.yaml              # 配置文件（标的/参数/LLM/飞书）— 不提交
├── config.yaml.example      # 配置模板
├── pyproject.toml           # 项目元数据
│
├── scripts/
│   ├── core/                # 核心模块
│   │   ├── config.py        #   配置加载（热加载）
│   │   ├── market.py        #   市场判断 + 标的管理
│   │   └── display.py       #   量比符号 + 格式化显示
│   ├── collect_ws.py        # WebSocket 实时行情采集
│   ├── collect_ws_launcher.py  # WebSocket 守护进程（cron）
│   ├── compute.py           # 量比计算引擎
│   ├── alert.py             # 信号检测 + 去重 + 飞书推送
│   ├── cli.py               # CLI 命令行入口
│   ├── feishu_bot.py        # 飞书机器人（WebSocket 长连接 + 卡片回调）
│   ├── feishu_bot_launcher.py  # 飞书机器人守护进程（cron）
│   ├── longbridge_sync.py   # 长桥持仓+自选股同步
│   ├── bot_start.py         # 一键启动飞书机器人
│   ├── bot_stop.py          # 一键停止飞书机器人
│   ├── cleanup.py           # 数据清理脚本
│   ├── start_all.py         # 一键启动所有服务
│   ├── stop_all.py          # 一键关停所有服务
│   └── llm.py               # LLM 多模型调用层
│
├── data/
│   ├── snapshots/           # 行情快照（JSONL，按市场分目录）
│   │   ├── US/              #   CLF_US_20260429.jsonl
│   │   ├── HK/              #   1810_HK_20260429.jsonl
│   │   └── CN/              #   601899_SH_20260429.jsonl
│   └── ratios.db            # SQLite 数据库（量比+快照索引+信号历史）
│
└── logs/                    # 运行日志
```

---

## 四、核心概念：量比

### 4.1 什么是量比

量比用于衡量“当前成交强度”相对基准是否异常。系统区分两类口径：

- **5日历史同期量比**：今天到当前市场时刻的成交量，相对过去5个交易日同一市场时刻的平均成交量。
- **日内滚动量比**：最近几分钟成交量，相对今天前一段时间的常态成交量。

二者用途不同：5日历史同期量比适合作为主展示指标，判断今天整体是否放量；日内滚动量比适合作为短线异动信号，判断刚刚是否突然加速。

| 量比范围 | 符号 | 信号 | 含义 |
|:--:|:--:|:--|:--|
| > 5.0 | ⬆⬆⬆ | 巨量 | 重大事件 |
| 2.0 - 5.0 | ⬆⬆ | 显著放量 | 突破或出货信号 |
| 1.2 - 2.0 | ⬆ | 放量 | 有资金关注 |
| 0.8 - 1.2 | ─ | 正常 | 正常交易活跃度 |
| 0.6 - 0.8 | ⬇ | 缩量 | 无人关注 |
| < 0.6 | ⬇⬇ | 缩量异常 | 流动性极低或交易冷清 |

### 4.2 双量比系统

本系统同时运行两套量比计算逻辑：

#### 5日历史同期量比（主量比）

用于回答：**今天截至当前时刻，成交是否明显强于过去同一时间？**

推荐公式：

```text
historical_ratio =
今日开盘至当前市场时刻累计成交量
/
过去5个交易日开盘至同一市场时刻累计成交量的平均值
```

示例：A股当前是 10:35，则比较：

```text
今日 09:30-10:35 成交量
/
过去5个交易日各自 09:30-10:35 成交量均值
```

特点：

- **优势**：能消除开盘、午盘、尾盘等日内节律影响。
- **局限**：至少需要若干个历史交易日数据，样本越少越不稳定。
- **关键约束**：必须使用市场本地时间，且历史样本必须是交易日，不是自然日。
- **适用场景**：`/scan`、`/brief`、主量比排序、全天放量/缩量判断。

当前实现细节：

- 历史样本优先来自 `quote_minute_bars` 分钟聚合表；没有完成回填时才降级读取 `data/snapshots/*/*.jsonl`。
- 过去样本会调用 `is_trading_day_on(market, date)` 过滤非交易日；交易日 API 查询失败时保守放行。
- 如果当前市场不在交易时段且今天没有快照，会回退到最近一个正常交易快照，用于 CLI/简报展示最近有效状态。
- `historical_sample_days` 会记录实际使用的历史样本数；样本少于最低门槛时信号显示 `样本不足(x/5)`。

#### 日内滚动量比（短线异动）

用于回答：**刚刚这几分钟，是否相对今天前面突然放量？**

推荐公式：

```text
intraday_ratio =
最近 W 分钟成交量
/
今天前 B 分钟内每 W 分钟成交量的均值或中位数
```

示例：`W=5`、`B=30`，当前是 10:35，则比较：

```text
今日 10:30-10:35 成交量
/
今日 10:00-10:30 内每5分钟成交量的均值或中位数
```

特点：

- **优势**：当天即可生效，不依赖历史数据，对突然放量敏感。
- **局限**：只和今天自己比，不能完全消除开盘/尾盘天然放量。
- **建议**：用中位数或截尾均值做基准，降低单笔异常成交的干扰。
- **适用场景**：放量止跌、突然放量、短线异动推送。

当前实现细节：

- 默认 `W=5`、`B=30`，即最近5分钟对比前30分钟内每5分钟成交量。
- 基准方法由 `intraday_baseline_method` 控制，支持 `mean` 或 `median`。
- 当前市场日期不是交易日时直接返回 `休市`，不会用昨天数据冒充今天日内信号。
- 基准窗口必须有足够有效样本；样本不足时返回 `数据不足`。
- 日内推送阈值来自 `intraday_alert_threshold`，避免在告警层硬编码。

### 4.3 计算实现约束

为了保证两套量比可信，成交量计算需要满足以下约束：

- **成交量口径**：如果数据源提供的是当日累计 `volume`，窗口成交量应使用 `当前累计量 - 窗口起点累计量`。
- **市场时间**：US 使用美东时间，HK 使用香港时间，CN 使用北京时间；不能用服务器自然日直接切分美股交易日。
- **交易日样本**：5日历史量比应取过去5个交易日，遇到周末和假期要继续向前补足。
- **假期过滤**：历史样本按具体日期调用交易日检测；日内算法遇到休市直接返回 `休市`。
- **快照清洗**：计算前应按 timestamp 排序、去重，并处理累计 volume 回落、跨日重置、盘前盘后混入等情况。
- **午休/跨段交易**：A股和港股午休不应被当成连续交易窗口；窗口差分需要跳过非交易时段。
- **REST 定位**：REST 最新行情只修正价格/涨跌幅，标准量比不使用日 K 全日量替代历史同期量。

推荐组合判断：

```text
historical_ratio > 2.0
表示今天整体显著放量

intraday_ratio > 1.5
表示刚刚出现短线放量

historical_ratio > 1.5 且 intraday_ratio > 1.5
表示全天活跃度偏高，且当前正在加速
```

---

## 五、飞书机器人

### 5.1 功能概览

飞书机器人通过 WebSocket 长连接运行，支持以下交互指令：

| 指令 | 功能 |
|:--|:--|
| `/start` | 一键启动量比系统（cron + WebSocket + 飞书机器人） |
| `/stop` | 一键关停量比系统 |
| `/status` | 系统健康状态（含今日 LLM 调用次数） |
| `/scan` | 当前量比快照（按量比排序） |
| `/signals` | 今日触发信号列表 |
| `/brief` | 立即发送量比简报（原生表格） |
| `/watchlist` | 关注列表（卡片按钮一键删除，同步长桥） |
| `/allstock` | 全部股票（二级导航，一键添加到量比监控） |
| `/sync` | 同步长桥持仓+自选股到 watchlist |
| `/add CLF.US-克利夫兰` | 添加监控标的 |
| `/remove CLF.US` | 移除监控标的 |
| `/mute CLF.US 2h` | 静默指定标的 |
| `/history CLF.US` | 近 7 日量比趋势 |

### 5.2 信号卡片

信号触发时推送富文本卡片，包含价格、量比、LLM 分析，底部按钮支持直接操作。

### 5.3 管理飞书机器人

```bash
# 一键启动飞书机器人
python3 scripts/bot_start.py

# 一键停止飞书机器人
python3 scripts/bot_stop.py

# 查看飞书机器人日志
tail -f logs/feishu_bot.log
```

飞书机器人有 cron 守护进程（`feishu_bot_launcher.py`），每分钟检查一次，如果进程挂掉会自动重启。

---

## 六、CLI 命令

### 6.1 查询量比

```bash
# 查询单个标的
python3 scripts/cli.py --ticker CLF.US

# 带 LLM 分析
python3 scripts/cli.py --ticker CLF.US --analyze

# 扫描所有持仓
python3 scripts/cli.py --scan holdings

# 扫描市场内放量标的
python3 scripts/cli.py --market US --min-ratio 2.0
```

### 6.2 系统管理

```bash
# 系统状态检查
python3 scripts/cli.py --status

# 查看今日信号
python3 scripts/cli.py --signals

# 查看历史量比
python3 scripts/cli.py --history CLF.US

# 添加/移除标的
python3 scripts/cli.py --add CLF.US-克利夫兰
python3 scripts/cli.py --remove CLF.US

# 静默标的
python3 scripts/cli.py --mute CLF.US 2h
```

### 6.3 LLM 模型切换

```bash
# 查看可用模型
python3 scripts/llm.py --list

# 切换模型
python3 scripts/llm.py --switch xiaomi

# 测试当前配置
python3 scripts/llm.py --test
```

---

## 七、定时任务

### 7.1 服务列表

项目有 5 个 cron 任务，由 `start_all.py` 自动配置：

| 服务 | 频率 | 作用 |
|:--|:--|:--|
| `collect_ws_launcher.py` | 每分钟（工作日） | 检查并确保 WebSocket 采集进程存活 |
| `feishu_bot_launcher.py` | 每分钟 | 检查并确保飞书机器人进程存活 |
| `alert.py` | 每分钟（工作日） | 扫描量比信号，触发时推送飞书（假期自动跳过） |
| `alert.py --brief` | 每30分钟（工作日） | 发送持仓组合量比简报 |
| `longbridge_sync.py` | 每30分钟（工作日） | 同步长桥持仓+自选股 |
| `cleanup.py` | 每小时 | 清理过期数据（各市场收盘后 1 小时触发） |

### 7.2 一键管理

```bash
# 启动所有服务
python3 scripts/start_all.py

# 关停所有服务
python3 scripts/stop_all.py
```

### 7.3 手动管理

```bash
# 查看 cron
crontab -l

# 查看进程
ps aux | grep collect_ws
ps aux | grep feishu_bot

# 查看日志
tail -f logs/ws_collect.log
tail -f logs/feishu_bot.log
tail -f logs/alert.log
```

---

## 八、数据存储

### 8.1 JSONL 快照

每个标的每天一个 JSONL 文件，追加写入：

```
data/snapshots/US/CLF_US_20260429.jsonl   # 一行一条快照
```

相比旧方案（每条快照一个 JSON 文件），文件数从 6万+/天 降至每个标的每天一个 JSONL。

JSONL 是可回放原始数据。实时量比计算优先读取 SQLite 的 `quote_minute_bars` 分钟聚合表，避免每分钟扫描几百 MB 文本文件；旧格式 `.json` 快照不参与新算法，可通过 `cleanup.py --force` 清理。

快照字段来自 Longbridge 推送结构：

```json
{
  "ticker": "CLF.US",
  "timestamp": "2026-05-01T21:30:01",
  "price": 10.31,
  "open": 10.34,
  "high": 10.35,
  "low": 10.25,
  "volume": 1234567,
  "turnover": 12345678.9,
  "change": 0.06,
  "change_pct": 0.59
}
```

### 8.2 SQLite 数据库

`data/ratios.db` 包含以下表：

- `volume_ratios` — 量比实时记录（带 ticker + timestamp 索引）
- `quote_snapshots` — WebSocket/REST 原始行情快照，字段对齐 `ticker/timestamp/price/open/high/low/volume/turnover/change/change_pct`
- `quote_minute_bars` — 每 ticker 每市场分钟一行的累计量快照，是 5日历史同期量比和日内滚动量比的主计算数据源
- `signals` — 信号记录（带 timestamp 索引）
- `signal_states` — 信号去重状态
- `llm_calls` — LLM API 调用记录
- `schema_meta` — 数据库 schema 版本。v3 起在 v2 基础上无损增加 `quote_minute_bars`，避免 `alert.py` 每分钟全量解析 JSONL。

`volume_ratios` 当前记录两套算法结果：

- `historical_ratio`、`historical_today_volume`、`historical_avg_volume`、`historical_sample_days`
- `intraday_ratio`、`intraday_window_volume`、`intraday_baseline_volume`、`intraday_baseline_samples`
- `historical_signal`、`intraday_signal`、`cond_vol`、`cond_stop`、`cond_stable`

schema v2 初始化时会清空旧口径结果表；v2 升级到 v3 只新增分钟聚合表，不清空旧数据：

```text
DROP TABLE IF EXISTS volume_ratios
DROP TABLE IF EXISTS signals
DROP TABLE IF EXISTS signal_states
```

`llm_calls` 会保留，因为它不是量比结果数据。

### 8.3 数据清理

`cleanup.py` 自动清理过期数据（各市场收盘后 1 小时触发）：
- JSONL 快照：20 天
- quote_snapshots：20 天
- quote_minute_bars：20 天
- volume_ratios：20 天
- signals：20 天

```bash
# 查看清理状态
python3 scripts/cleanup.py --status

# 干跑（不实际删除）
python3 scripts/cleanup.py --dry-run
```

---

## 九、故障排查

### 9.1 常见问题

**Q: 量比显示 0.0 "数据不足"**
- `historical_sample_days` 不足时，5日历史同期量比会显示 `样本不足(x/5)`
- 当前市场未开盘或没有正常交易时段快照时，日内滚动量比会显示 `休市` 或 `数据不足`
- 查看 `market_date`、`market_time`、`volume_today`、`volume_avg5` 判断是否取到了正确市场时刻
- REST API 连接失败只影响最新价格补充，不应影响基于分钟聚合表的量比核心计算

**Q: 日内滚动量比太敏感**
- 将 `intraday_alert_threshold` 从 `1.5` 调高到 `2.0`
- 或将 `intraday_baseline_method` 从 `mean` 改为 `median`
- 开盘前几分钟基准样本不足时，系统会主动返回 `数据不足`

**Q: 旧 snapshots JSON 文件还有用吗**
- 新算法优先读取 SQLite 分钟聚合表；`.jsonl` 用于审计、回放和 `backfill_minute_bars.py` 回填
- 旧 `.json` 文件不再参与计算，可以通过 `python3 scripts/cleanup.py --force` 清理

**Q: JSONL 文件太大导致 alert.py CPU 高怎么办**
- 运行一次 `python3 scripts/backfill_minute_bars.py`，把现有 JSONL 回填到 `quote_minute_bars`
- WebSocket 后续会在写入原始快照时同步更新分钟聚合表，正常运行不再需要每分钟扫描全量 JSONL

**Q: REST API 或 trading_days 日志出现 Bad file descriptor**
- 当前版本不再用 `os.dup2()` 抑制 Longbridge SDK 输出，只在 Python 层临时 redirect `sys.stdout`
- 如果仍看到旧日志，先确认运行的是最新代码并重启 WebSocket/飞书机器人/cron 相关进程

**Q: 飞书机器人不响应**
- 检查 `config.yaml` 中 `feishu.app_id` 和 `feishu.app_secret` 是否正确
- 确认飞书开放平台已开启机器人能力、配置权限、发布版本
- 查看日志：`tail -f logs/feishu_bot.log`

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
├── ws_collect.log        # WebSocket 采集主日志
├── ws_collect.err        # WebSocket 错误日志
├── ws_collect.pid        # WebSocket 进程 PID
├── feishu_bot.log        # 飞书机器人日志
├── feishu_bot.err        # 飞书机器人错误日志
├── feishu_bot.pid        # 飞书机器人 PID
├── alert.log             # 信号扫描日志
├── brief.log             # 简报日志
├── cleanup.log           # 数据清理日志
└── launcher.log          # 守护进程启动日志
```

---

## 十、依赖

```toml
# pyproject.toml
[project]
requires-python = ">=3.11"
dependencies = [
    "pyyaml",
    "requests",
    "longbridge>=2.0.0",
    "lark-oapi>=1.0.0",
]
```

---

## 版本历史

| 版本 | 日期 | 变更 |
|:--|:--|:--|
| v1.0 | 2026-04-28 | 初始实现方案 |
| v2.0 | 2026-04-29 | 切换 WebSocket 推送模式，新增日内滚动量比，LLM 多模型切换 |
| v3.0 | 2026-04-29 | 迭代v1：JSONL 存储、飞书机器人交互、信号去重、中文名标识、CLI 增强、数据清理 |
| v3.1 | 2026-04-29 | 迭代v2：/watchlist 交互删除、/allstock 二级导航、长桥持仓同步、卡片回调、WebSocket 重试、daemon 修复 |
| v3.2 | 2026-04-30 | US 股票量比修复、信号卡片涨跌方向、should_push 状态推送逻辑 |
| v3.3 | 2026-05-01 | 量比数据源切换 REST API、假期检测（trading_days API）、数据库索引优化、代码审查缺陷修复（FD double-close / 竞态条件 / PID 锁 / mute 过期等 8 项） |
| v3.4 | 2026-05-01 | 重写量比算法：5日历史同期量比 + 日内滚动量比；新增 schema v2、交易日按日期过滤、休市保护、日内基准样本门槛 |
| v3.5 | 2026-05-01 | 性能优化：新增 schema v3 `quote_minute_bars` 分钟聚合主计算表、JSONL 回填脚本、alert 扫描休市跳过；修复 Longbridge stdout fd 风险 |

---

> 维护者：shinji | 技术支持：Claude Code + Longbridge OpenAPI
