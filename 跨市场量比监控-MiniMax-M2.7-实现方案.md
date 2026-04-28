---
title: 跨市场实时量比监控系统 — MiniMax M2.7 实现方案
created: 2026-04-28
updated: 2026-04-28
type: design
status: draft
tags: [design, quant, volume-ratio, minimax, claude-code]
---

# 跨市场实时量比监控系统

> **核心诉求**：随时通过不同市场，对正在交易中的股票计算量比，方便随时买入卖出决策。
> **技术栈**：MiniMax M2.7（AI分析） + Claude Code CLI（代码生成） + Longbridge CLI（行情） + Python（计算引擎）

---

## 一、量比定义

```
量比 = 当前时段成交量 ÷ 过去N日同时段平均成交量

例如：10:30 某股票成交 50万股
      过去5日 10:30 平均成交 30万股
      量比 = 50/30 = 1.67
```

| 量比范围 | 含义 |
|:--|:--|
| < 0.8 | 缩量（无人关注/止跌可能） |
| 0.8-1.2 | 正常 |
| 1.2-2.0 | 放量（有资金关注） |
| 2.0-5.0 | 显著放量（突破/出货信号） |
| > 5.0 | 巨量（重大事件） |

---

## 二、系统架构

```
┌─────────────────────────────────────────────────────┐
│                    用户界面                          │
│         飞书 / 终端 / Obsidian Wiki                  │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│               AI 分析层 (MiniMax M2.7)               │
│   • 量比异常识别  • 多因子综合判断                    │
│   • 自然语言输出  • 买卖建议生成                      │
│   API: 1500次/5小时 (免费额度)                       │
└──────────────────────┬──────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────┐
│              计算引擎 (Python)                       │
│   • 量比实时计算  • 历史对比  • 信号检测              │
│   • 数据持久化    • 定时任务调度                      │
└──────┬──────────────────────┬───────────────────────┘
       │                      │
┌──────▼──────┐    ┌──────────▼──────────┐
│ 美股行情     │    │ 港股行情              │
│ Longbridge   │    │ Longbridge CLI        │
│ CLI 免费     │    │ 免费                  │
└──────────────┘    └─────────────────────┘
```

---

## 三、文件结构

```
~/.hermes/volume-ratio/
├── config.yaml              # 监控标的 + 参数
├── scripts/
│   ├── collect.py           # 行情采集（cron 每1分钟）
│   ├── compute.py           # 量比计算引擎
│   ├── alert.py             # 信号检测 + 飞书推送
│   └── cli.py               # 命令行入口（随时查询任意标的）
├── data/
│   ├── snapshots/           # 原始行情快照
│   │   ├── US/              # 按市场分目录
│   │   ├── HK/
│   │   └── CN/
│   └── ratios.db            # SQLite 量比历史
└── output/
    └── wiki/                # 自动推送到 wiki 的量比日报
```

---

## 四、核心脚本设计

### 4.1 行情采集 (`collect.py`)

```python
# cron: */1 9-16 * * 1-5  (A股/港股时段)
# cron: */1 21-3  * * 1-5  (美股时段)

# 输入：config.yaml 里的监控列表
# 输出：{ticker}_{timestamp}.json 存入 data/snapshots/
# 数据：price, volume, turnover, high, low
```

### 4.2 量比计算 (`compute.py`)

```python
def calc_volume_ratio(ticker, current_time):
    """
    量比 = 今日同时段累计量 / 过去5日同时段均量
    
    Examples:
      10:35 查询 → 对比 09:30-10:35 时段
      14:00 查询 → 对比 09:30-14:00 时段
    """
    today_vol = get_today_volume(ticker, current_time)
    past_5d_vols = [get_day_volume(ticker, d, current_time) 
                    for d in past_5_days]
    avg_5d_vol = sum(past_5d_vols) / len(past_5d_vols)
    return today_vol / avg_5d_vol
```

### 4.3 信号检测 (`alert.py`)

```python
SIGNAL_RULES = {
    "放量突破": "量比 > 2.0 AND 价格涨幅 > 2%",
    "放量下跌": "量比 > 2.0 AND 价格跌幅 > 2%",
    "缩量止跌": "量比 < 0.6 AND 前3日跌幅 > 5% AND 当日收阳",
    "尾盘放量": "量比 > 1.5 AND 时间在 14:30-15:00 AND 价格涨",
}
```

### 4.4 CLI 入口 (`cli.py`)

```bash
# 查询单个标的实时量比
python3 cli.py --ticker NVDA.US

# 扫描整个持仓列表
python3 cli.py --scan holdings

# 扫描市场内放量标的
python3 cli.py --market US --min-ratio 2.0

# 输出格式
$ python3 cli.py --ticker CLF.US
CLF.US  $10.10  -4.8%  量比: 0.72 (缩量下跌)
信号: 缩量止跌 ← 前3日涨+18%后回调，量缩至均量72%
建议: 等量比回升至1.0以上+阳线确认
```

---

## 五、MiniMax M2.7 集成

### 使用场景

MiniMax 不是每笔计算都调，而是在以下场景调用：

| 场景 | 调用频率 | 说明 |
|:--|:--|:--|
| **信号解读** | 触发了才调 | 量比异常 → MiniMax 分析原因 |
| **多因子综合** | 触发了才调 | 量比+价格+新闻 → 综合判断 |
| **定时简报** | 每30分钟 | 持仓组合量比概况 |
| **手动查询** | 用户主动 | "帮我分析这些量比数据" |

### API 调用估算（按 1500次/5小时 免费额度）

| 功能 | 频率 | 每次标的数 | 5小时总调用 |
|:--|:--|:--|:--|
| 定时简报 | 每30分钟 × 10次 | 1 | 10 |
| 信号解读 | 平均每5分钟1个信号 | 1 | 60 |
| 手动查询 | 预估10次 | 1-3 | 30 |
| **合计** | | | **~100次/5小时** ✅ |

> 1500次额度，实际只用了 ~7%。大量富余。

### Prompt 模板

```
你是量比分析专家。给定以下数据：
- 标的: {ticker}
- 当前价: {price} ({change}%)
- 量比: {ratio}
- 近5日均量: {avg_vol}
- 近期走势: {recent_action}

请用中文简短分析：
1. 量比异常的原因
2. 是否构成买入/卖出信号
3. 风险提示
限制100字以内。
```

---

## 六、Claude Code CLI 开发流程

### 安装

```bash
npm install -g @anthropic-ai/claude-code
```

### 开发步骤（用 Claude Code 加速）

```bash
# Step 1: 初始化项目
cd /Users/shinji/project-x/volume-ratio
claude "初始化一个Python项目，包含config.yaml和scripts目录"

# Step 2: 实现行情采集
claude "写一个collect.py，用longbridge CLI采集行情快照，支持US/HK/CN市场"

# Step 3: 实现量比计算
claude "写compute.py，从快照数据计算实时量比，对比过去5日同时段均值"

# Step 4: 实现CLI入口
claude "写cli.py，支持 --ticker/--scan/--market 三个模式，调用compute.py"

# Step 5: 集成MiniMax分析
claude "在cli.py中添加 --analyze 参数，调用MiniMax M2.7 API做量比解读"

# Step 6: 定时任务 + 飞书推送
claude "写alert.py，cron每1分钟扫描，触发信号时通过webhook推送到飞书"
```

### 开发时间估算

| 步骤 | 内容 | 估计时间 |
|:--|:--|:--|
| 1 | 项目初始化 | 10分钟 |
| 2 | 行情采集 | 20分钟 |
| 3 | 量比计算 | 30分钟 |
| 4 | CLI入口 | 20分钟 |
| 5 | MiniMax集成 | 15分钟 |
| 6 | 定时+推送 | 15分钟 |
| **总计** | | **~2小时** |

---

## 七、成本模型

| 组件 | 费用 | 说明 |
|:--|:--|:--|
| Longbridge CLI | $0 | 已有账号，免费 |
| MiniMax M2.7 | **$0** | 1500次/5小时免费额度，实际只用~7% |
| Claude Code CLI | ~$5-10 | 开发期间代码生成消耗（一次性） |
| 服务器 | $0 | 本地 Mac 24小时运行 |
| **月费总计** | **$0** | |
| **一次性开发费** | **~$5-10** | Claude Code 消耗 |

---

## 八、配置文件 (`config.yaml`)

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
  volume_ratio_window: 5        # 量比对比天数
  snapshot_interval: 60         # 采集间隔（秒）
  alert_threshold: 2.0          # 放量告警阈值
  shrink_threshold: 0.6         # 缩量告警阈值

# MiniMax
minimax:
  base_url: "https://api.minimax.chat/v1"
  model: "MiniMax-M2.7"
  max_calls_per_5h: 1500

# 飞书推送
feishu:
  webhook_url: "已有配置"
```

---

## 九、使用示例

```bash
# 实时查一个标的
python3 cli.py --ticker CLF.US
# CLF.US  $10.10  -4.8%  量比: 0.72 (缩量)
# 信号: 缩量止跌 ← 等阳线确认

# 扫描所有持仓
python3 cli.py --scan holdings
# CLF    量比 0.72  缩量  ⚠️ 观望
# DRAM   量比 1.15  正常  ✅ 
# BMNR   量比 0.89  正常  ✅ 
# NVO    量比 0.95  正常  ✅ 

# 扫描放量标的
python3 cli.py --market US --min-ratio 2.0
# NVDL   量比 2.3   放量  🔥 关注
# MUU    量比 2.8   放量  🔥 关注

# 带 AI 分析
python3 cli.py --ticker CLF.US --analyze
# [MiniMax分析] CLF缩量0.72，前期+18%后正常回调。
# 量缩至均量72%，卖压衰竭迹象。建议等量比>1.0+阳线确认买入。
```

---

## 十、实施路线图

| Phase | 内容 | 时间 | 工具 |
|:--|:--|:--|:--|
| **1** | 环境搭建 + config.yaml | 10分钟 | Claude Code |
| **2** | 行情采集脚本 | 20分钟 | Claude Code |
| **3** | 量比计算引擎 | 30分钟 | Claude Code |
| **4** | CLI 入口 | 20分钟 | Claude Code |
| **5** | MiniMax 集成 | 15分钟 | 手写 API 调用 |
| **6** | 定时+飞书推送 | 15分钟 | Claude Code |
| **7** | 调试+上线 | 30分钟 | 手动测试 |
| **总计** | | **~2小时** | |

---

## 十一、与现有系统的差异

| | 原方案3 (Gemini + Antigravity) | 新方案 (MiniMax + 量比) |
|:--|:--|:--|
| 目标 | 本地编程环境 | **跨市场实时量比监控** |
| AI模型 | Gemini 3 (付费) | **MiniMax M2.7 (免费1500次/5h)** |
| 工具 | Antigravity IDE | **Claude Code CLI** |
| 月费 | $4-20 | **$0** |
| 开发周期 | 14天 | **~2小时** |
| 与投资相关 | 间接 | **直接解决买卖决策** |

---

> 版本: v1.0 | 日期: 2026-04-28 | 作者: Hermes Agent + shinji
