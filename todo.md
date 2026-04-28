# 跨市场量比监控系统 - TODO

> 创建时间：2026-04-29
> 状态：进行中

---

## TODO 1：优化放量突破策略

**当前问题**：现有三条件（放量/止跌/企稳）与预期不符

**目标条件**（将三个条件反过来改）：

| 条件 | 当前逻辑 | 目标逻辑 |
|:--|:--|:--|
| 放量 | 量比 > 1.5 ✅ | 量比 > 1.5（不变） |
| 突破 | 信号期最低价 >= 基线最低价 × 0.995（止跌） | **最新价 > 基线最高价 × 1.005**（突破新高） |
| 加速 | 最新价 > 信号期最低价 × 1.005（企稳） | **最新价 > 信号期均价**（加速上涨） |

**修改文件**：
- `scripts/compute.py` → `calc_intraday_ratio()` 函数

**验收标准**：
- 满足三条件时 `signal_intraday = "放量突破"`
- 量比 > 1.5 但不满足突破/加速条件时 `signal_intraday = "放量"`（非突破）

---

## TODO 2：自选标的 + 持仓同步

**需求描述**：
- 不需要全量监控，只监控用户指定的标的
- 标的可手动自选（修改 config.yaml）
- 支持从 Longbridge 获取持仓/关注列表，自动同步

**实现方案**：
1. **手动模式**：直接在 `config.yaml` 的 `watchlist` 中填入标的
2. **自动模式**：
   - 调用 Longbridge API 获取持仓（`longbridge quote --format json` 可获取持仓）
   - 或通过 Longbridge WebSocket 订阅自己关注的标的列表
   - 每日/每次启动时同步一次

**修改文件**：
- `scripts/compute.py` → `get_all_tickers()` 改为支持动态获取
- `scripts/collect_ws.py` → 订阅列表改为从持仓 API 获取
- 新增 `scripts/sync_watchlist.py` → 同步持仓到 watchlist

**验收标准**：
- 可通过命令手动指定标的：`python3 scripts/cli.py --ticker CLF.US`
- 可自动从 Longbridge 同步持仓标的
- 自选标的优先级高于自动同步（用户手动选择优先）

---

## TODO 3：前端可视化交互页面

**需求描述**：
- 需要一个可视化页面展示量比数据
- 支持交互操作（切换标的、调整参数、查看历史）
- 实时显示推送状态和 LLM 分析结果

**技术方案**（待定）：
- [ ] Web 页面（React/Vue）+ 后端 API
- [ ] 或 Obsidian 插件（原生 Markdown 展示）
- [ ] 或终端 UI（TUI，如 Rich/Textual）

**验收标准**：
- 实时显示所有监控标的状态
- 支持查看单个标的的日内/5日量比详情
- 支持查看历史 K 线 + 量比叠加图
- 触发信号时页面高亮/推送通知

---

## 优先级

| 优先级 | TODO | 说明 |
|:--|:--|:--|
| P0 | TODO 1 | 策略逻辑修正，影响核心功能 |
| P1 | TODO 2 | 用户体验优化，标的自选 |
| P2 | TODO 3 | 前端可视化，长期目标 |
| P1 | TODO 4 | 交易时间过滤，非开盘时间不监控 |

---

## TODO 4：交易时间自动过滤

**需求描述**：
- 各市场仅在开盘时间内执行监控，非开盘时间跳过
- 避免无效计算、减少 LLM 调用、降低系统负载

**各市场交易时间（北京时间）**：

| 市场 | 开盘时间 | 收盘时间 | 备注 |
|:--|:--|:--|:--|
| A股 (CN) | 09:30 | 15:00 | 午休 11:30-13:00 |
| 港股 (HK) | 09:30 | 16:00 | 午休 12:00-13:00 |
| 美股 (US) | 21:30 | 04:00 (+1天) | 夏令时为 22:30-03:00 |

**跨市场互斥逻辑**：
- 北京白天（09:00-16:00）：只监控 A股 + 港股，不监控美股
- 北京深夜（21:30-03:00）：只监控美股，不监控 A股 + 港股

**实现方案**：
```python
def is_market_trading(market: str, now: datetime = None) -> bool:
    """判断当前市场是否在交易时间内（北京时间）"""
    if now is None:
        now = datetime.now()

    h, m = now.hour, now.minute
    time_in_day = h * 60 + m  # 当天分钟数

    if market == "CN":
        # A股：09:30-11:30 上午盘，13:00-15:00 下午盘
        return (time_in_day >= 9*60+30 and time_in_day < 11*60+30) or \
               (time_in_day >= 13*60 and time_in_day < 15*60)
    elif market == "HK":
        # 港股：09:30-12:00 上午盘，13:00-16:00 下午盘
        return (time_in_day >= 9*60+30 and time_in_day < 12*60) or \
               (time_in_day >= 13*60 and time_in_day < 16*60)
    elif market == "US":
        # 美股：21:30-04:00（次日），跨午夜
        return time_in_day >= 21*60+30 or time_in_day < 4*60
    return False

def get_active_markets(now: datetime = None) -> list:
    """获取当前应该监控的市场列表"""
    active = []
    for market in ["CN", "HK", "US"]:
        if is_market_trading(market, now):
            active.append(market)
    return active
```

**修改文件**：
- 新增 `scripts/market_hours.py` → 交易时间判断工具函数
- `scripts/collect_ws.py` → 订阅前根据交易时间过滤市场
- `scripts/alert.py` → 扫描前过滤非交易市场的标的

**验收标准**：
- 美股 21:30 前不订阅美股行情（节省 WebSocket 资源）
- 港股/A股 16:00 后不扫描（节省 LLM 调用）
- 非交易日（周末/节假日）自动跳过
- 日志清晰显示当前监控的市场和时间段