# 跨市场量比监控系统

实时监控 US/HK/CN 三大市场股票的成交量异动，结合 LLM 智能分析，信号触发即时推送飞书卡片。支持飞书机器人交互指令，可通过卡片按钮直接管理监控标的、查看信号历史、同步长桥持仓。

---

## 一、项目概述

### 1.1 核心能力

- **双量比引擎**：同时运行日内滚动量比（立即生效）和5日历史量比（需要数据积累）
- **多市场覆盖**：美股(US)、港股(HK)、A股(CN) 三大市场
- **智能信号检测**：放量突破、放量下跌、缩量止跌、尾盘放量等
- **假期自动检测**：通过 Longbridge trading_days API 判断交易日，假期/周末不推送
- **REST API 数据源**：量比计算使用 Longbridge REST API（K-line 历史数据 + 实时行情），数据准确可靠
- **LLM 多模型切换**：一键切换 MiniMax / Xiaomi 等模型，自动分析量比异常原因
- **飞书机器人**：WebSocket 长连接，支持交互指令（/status /scan /signals /brief /watchlist /allstock /sync /start /stop /mute /history）
- **交互式卡片**：关注列表可删除、全部股票可添加、长桥持仓自动同步
- **信号去重**：状态机模型，状态变化时推送，状态持续时静默，状态升级时再推送；支持 /mute 静默（自动过期）
- **JSONL 存储**：每日每标的一个 JSONL 文件，6万+文件/天 → 11文件/天
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
│   (JSONL行情快照，按天追加)    │    (SQLite量比+信号历史)      │
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
pip install pyyaml requests longbridge lark-oapi pytz
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
│   └── ratios.db            # SQLite 数据库（量比+信号历史）
│
└── logs/                    # 运行日志
```

---

## 四、核心概念：量比

### 4.1 什么是量比

量比 = 当前时段成交量 / 历史同期平均成交量

| 量比范围 | 符号 | 信号 | 含义 |
|:--:|:--:|:--|:--|
| > 5.0 | ⬆⬆⬆ | 巨量 | 重大事件 |
| 2.0 - 5.0 | ⬆⬆ | 放量 | 突破或出货信号 |
| 1.5 - 2.0 | ⬆ | 温放 | 有资金关注 |
| 0.8 - 1.5 | ─ | 正常 | 正常交易活跃度 |
| 0.5 - 0.8 | ⬇ | 缩量 | 无人关注 |
| < 0.5 | ⬇⬇ | 地量 | 流动性极低 |

### 4.2 双量比系统

本系统同时运行两套量比计算逻辑：

#### 日内滚动量比（立即生效）
- **原理**：今日最近N分钟成交量 vs 今日最近基线窗口成交量
- **优势**：今天就能用，不需要历史数据

#### 5日历史量比（需要数据积累）
- **原理**：今日同时段成交量 vs 过去5日同一时段平均成交量
- **优势**：消除日内节律（开盘放量/尾盘缩量）
- **局限**：需要至少5个交易日数据才能生效

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

相比旧方案（每条快照一个 JSON 文件），文件数从 6万+/天 降至 11个/天。

### 8.2 SQLite 数据库

`data/ratios.db` 包含以下表：

- `volume_ratios` — 量比实时记录（带 ticker + timestamp 索引）
- `signals` — 信号记录（带 timestamp 索引）
- `signal_states` — 信号去重状态
- `llm_calls` — LLM API 调用记录

### 8.3 数据清理

`cleanup.py` 自动清理过期数据（各市场收盘后 1 小时触发）：
- JSONL 快照：20 天
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
- 5日历史量比需要5个交易日数据才生效
- 查看 `ratio_intraday` 日内滚动量比，今天就能用

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
requires-python = ">=3.9"
dependencies = [
    "pyyaml",
    "requests",
    "longbridge>=2.0.0",
    "lark-oapi>=1.0.0",
    "pytz",
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

---

> 维护者：shinji | 技术支持：Claude Code + Longbridge OpenAPI
