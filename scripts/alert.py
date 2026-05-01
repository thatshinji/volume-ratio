#!/usr/bin/env python3
"""
信号检测 + 飞书推送
cron 每1分钟扫描，触发信号时通过飞书机器人推送
"""

import json
import fcntl
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).parent.parent

# 将 scripts/ 加入 sys.path，供 from compute import 等使用
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_ticker_name
from core.display import format_ratio_display, format_ticker_line


# === 信号规则 ===

SIGNAL_RULES = {
    "放量突破": lambda ratio, change: ratio > 2.0 and change > 2,
    "放量下跌": lambda ratio, change: ratio > 2.0 and change < -2,
    "缩量止跌": lambda ratio, change: ratio < 0.6 and change > 0,
}


def _is_end_of_day(market: str) -> bool:
    """判断是否为市场尾盘时段（仅 CN 14:30-15:00）"""
    if market != "CN":
        return False
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now = datetime.now()
    return (now.hour == 14 and now.minute >= 30) or now.hour == 15


# === LLM Prompt 模板 ===

PROMPT_ANALYSIS_TEMPLATE = """你是量比分析专家。给定以下数据：
- 标的: {ticker}
- 当前价: {price} ({change_pct:+.2f}%)
- 信号来源: {source_label}
- 5日历史同期量比: {historical_ratio}
- 日内滚动量比: {intraday_ratio}
- 近5日均量: {avg_vol}
- 近期走势: {recent_action}

请用中文简短分析：
1. 量比异常的原因
2. 是否构成买入/卖出信号
3. 风险提示
限制100字以内。"""

PROMPT_BRIEF_TEMPLATE = """你是量比分析专家。以下是当前持仓组合的量比简报：

{brief_text}

请用中文简短分析：
1. 当前市场整体情绪（哪些标的值得关注）
2. 是否有异常信号需要关注
3. 整体风险提示
限制150字以内。"""


def detect_signals(results: List[dict]) -> List[dict]:
    """检测触发的信号（historical + intraday 双路径）"""
    from core.market import get_market, is_market_trading

    alerts = []
    config = load_config()
    params = config.get("params", {})
    alert_threshold = params.get("alert_threshold", 2.0)
    shrink_threshold = params.get("shrink_threshold", 0.6)
    mute_list = config.get("mute", {})
    now = datetime.now()
    mute_dirty = False

    for r in results:
        ticker = r.get("ticker", "")
        if ticker in mute_list:
            # 检查是否过期
            try:
                until = datetime.fromisoformat(mute_list[ticker])
                if now < until:
                    continue  # 仍在静默期，跳过
                else:
                    del mute_list[ticker]  # 过期，移除
                    mute_dirty = True
            except (ValueError, TypeError):
                del mute_list[ticker]  # 格式错误，移除
                mute_dirty = True
        market = get_market(ticker)
        if not is_market_trading(market):
            continue
        name = r.get("name", ticker)
        ratio = r.get("ratio", 0)
        ratio_intraday = r.get("ratio_intraday", 0)
        change_pct = r.get("change_pct", 0)
        signal = r.get("signal", "")
        signal_detail = r.get("signal_detail", "")
        data_quality = r.get("data_quality", "")
        historical_sample_days = r.get("historical_sample_days", 0)
        historical_ready = data_quality == "ok" and not str(signal).startswith("样本不足")

        # === Historical 路径（5日历史量比）===
        triggered = []
        if historical_ready:
            for sig_name, rule in SIGNAL_RULES.items():
                if rule(ratio, change_pct):
                    triggered.append(sig_name)

        # 尾盘放量（仅 CN 市场 14:30-15:00）
        if historical_ready and not triggered and ratio > 1.5 and _is_end_of_day(market):
            triggered.append("尾盘放量")

        # 阈值检查（仅在 SIGNAL_RULES 未匹配时）
        if historical_ready and not triggered:
            if ratio > alert_threshold:
                triggered.append(f"放量(>{alert_threshold})")
            elif ratio < shrink_threshold:
                triggered.append(f"缩量(<{shrink_threshold})")

        if triggered or (historical_ready and signal_detail):
            alerts.append({
                "ticker": ticker,
                "name": name,
                "ratio": ratio,
                "historical_ratio": ratio,
                "intraday_ratio": ratio_intraday,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": signal,
                "signal_detail": signal_detail,
                "triggered_signals": triggered,
                "source": "historical",
                "volume_avg5": r.get("volume_avg5", 0),
                "historical_sample_days": historical_sample_days,
            })

        # === Intraday 路径（滚动量比 + 三条件）===
        signal_intraday = r.get("signal_intraday", "")
        cond_vol = r.get("cond_vol", False)
        cond_stop = r.get("cond_stop", False)
        cond_stable = r.get("cond_stable", False)

        if signal_intraday == "放量止跌":
            alerts.append({
                "ticker": ticker,
                "name": name,
                "ratio": ratio_intraday,
                "historical_ratio": ratio,
                "intraday_ratio": ratio_intraday,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": "放量止跌",
                "signal_detail": f"放量={cond_vol} 止跌={cond_stop} 企稳={cond_stable}",
                "triggered_signals": ["放量止跌"],
                "source": "intraday",
                "volume_avg5": r.get("volume_avg5", 0),
                "historical_sample_days": historical_sample_days,
            })
        elif signal_intraday == "放量":
            alerts.append({
                "ticker": ticker,
                "name": name,
                "ratio": ratio_intraday,
                "historical_ratio": ratio,
                "intraday_ratio": ratio_intraday,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": "放量",
                "signal_detail": f"放量={cond_vol} 止跌={cond_stop} 企稳={cond_stable}",
                "triggered_signals": ["放量"],
                "source": "intraday",
                "volume_avg5": r.get("volume_avg5", 0),
                "historical_sample_days": historical_sample_days,
            })

    # 保存配置（仅在移除过期 mute 时）
    if mute_dirty:
        import yaml
        from pathlib import Path
        CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # 按 ticker 合并：同一 ticker 同时保留 historical 和 intraday 信息
    seen = {}
    for alert in alerts:
        ticker = alert["ticker"]
        if ticker not in seen:
            seen[ticker] = alert
        else:
            seen[ticker] = merge_alerts(seen[ticker], alert)
    return list(seen.values())


def merge_alerts(existing: dict, incoming: dict) -> dict:
    """合并同一 ticker 的多路径信号，避免 intraday 覆盖 historical 细节。"""
    merged = dict(existing)
    sources = []
    for source in (existing.get("source"), incoming.get("source")):
        if source and source not in sources:
            sources.append(source)
    merged["source"] = "mixed" if len(sources) > 1 else (sources[0] if sources else "")
    merged["sources"] = sources

    triggered = []
    for item in existing.get("triggered_signals", []) + incoming.get("triggered_signals", []):
        if item and item not in triggered:
            triggered.append(item)
    merged["triggered_signals"] = triggered

    details = []
    for detail in (existing.get("signal_detail", ""), incoming.get("signal_detail", "")):
        if detail and detail not in details:
            details.append(detail)
    merged["signal_detail"] = " / ".join(details)

    # 用优先级最高的信号作为状态机主信号，但保留所有 triggered_signals 用于展示。
    candidates = [existing.get("signal", ""), incoming.get("signal", "")] + triggered
    merged["signal"] = max(candidates, key=lambda s: SIGNAL_PRIORITY.get(s, 0)) if candidates else existing.get("signal", "")

    # 卡片主量比取更强的一路；历史均量等辅助字段保留已有值。
    if incoming.get("ratio", 0) > existing.get("ratio", 0):
        merged["ratio"] = incoming.get("ratio", 0)
    merged["historical_ratio"] = max(existing.get("historical_ratio", 0), incoming.get("historical_ratio", 0))
    merged["intraday_ratio"] = max(existing.get("intraday_ratio", 0), incoming.get("intraday_ratio", 0))
    merged["historical_sample_days"] = max(existing.get("historical_sample_days", 0), incoming.get("historical_sample_days", 0))
    return merged


def format_alert_card(alert: dict, analysis: Optional[str] = None) -> dict:
    """格式化飞书卡片消息"""
    ticker = alert["ticker"]
    name = alert.get("name", ticker)
    ratio = alert["ratio"]
    historical_ratio = alert.get("historical_ratio", 0)
    intraday_ratio = alert.get("intraday_ratio", 0)
    change = alert["change_pct"]
    price = alert["price"]
    signals = ", ".join(alert["triggered_signals"]) or alert["signal_detail"] or alert["signal"]
    source = alert.get("source", "historical")

    direction = "↑" if change > 0 else ("↓" if change < 0 else "─")
    ratio_display = format_ratio_display(ratio)
    type_label = "日内+5日" if source == "mixed" else ("日内" if source == "intraday" else "5日")
    # 信号显示加方向（如"放量 ↑" 或 "缩量 ↓"）
    signal_name = alert["signal_detail"] or alert["signal"]
    if signal_name in ("放量", "缩量", "放量突破", "放量下跌", "缩量止跌", "尾盘放量"):
        signal_display = f"{signal_name} {direction}"
    else:
        signal_display = signals

    # 标题
    if ratio > 5.0:
        header_icon = "🔥🔥"
    elif ratio > 2.0:
        header_icon = "🔥"
    elif ratio < 0.6:
        header_icon = "⚠️"
    else:
        header_icon = "📊"

    title = f"{header_icon} 【{type_label}】{ticker}-{name} {direction}"

    # 内容
    lines = [
        f"**当前价:** ${price} ({direction}{abs(change):.2f}%)",
        f"**主触发量比:** {ratio:.2f} {ratio_display}",
        f"**信号:** {signal_display}",
        f"**时间:** {datetime.now().strftime('%H:%M:%S')}",
    ]
    sample_days = alert.get("historical_sample_days", 0)
    if source in ("historical", "mixed") or historical_ratio > 0:
        hist_text = f"{historical_ratio:.2f} {format_ratio_display(historical_ratio)}" if historical_ratio > 0 else "数据不足"
        lines.insert(3, f"**5日量比:** {hist_text}（样本 {sample_days}/5）")
    if source in ("intraday", "mixed") or intraday_ratio > 0:
        intraday_text = f"{intraday_ratio:.2f} {format_ratio_display(intraday_ratio)}" if intraday_ratio > 0 else "数据不足"
        insert_at = 4 if any(line.startswith("**5日量比:**") for line in lines) else 3
        lines.insert(insert_at, f"**日内量比:** {intraday_text}")
    if analysis:
        lines.append("")
        lines.append(f"**LLM分析:** {analysis}")

    content = "\n".join(lines)

    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}}
        ]
    }


def send_feishu_card(card: dict, chat_id: str = "") -> bool:
    """通过飞书机器人发送卡片消息"""
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    config = load_config()
    feishu = config.get("feishu", {})
    app_id = feishu.get("app_id", "")
    app_secret = feishu.get("app_secret", "")

    if not app_id or not app_secret:
        print("[alert] 飞书 app_id/app_secret 未配置，跳过推送")
        return False

    if not chat_id:
        chat_id = feishu.get("chat_id", "")
    if not chat_id:
        print("[alert] 飞书 chat_id 未配置，跳过推送")
        return False

    try:
        client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        content = json.dumps(card)
        req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(content)
                .build()) \
            .build()

        resp = client.im.v1.message.create(req)
        if resp.success():
            print(f"[alert] 飞书卡片推送成功")
            return True
        else:
            print(f"[alert] 飞书卡片推送失败: code={resp.code} msg={resp.msg}")
            return False
    except Exception as e:
        print(f"[alert] 飞书卡片推送异常: {e}")
        return False


def get_llm_analysis(prompt: str) -> Optional[str]:
    """调用通用 LLM 分析（支持多模型切换）"""
    from llm import call_llm
    return call_llm(prompt)


def generate_llm_prompt(ticker: str, ratio: float, price: float,
                        change_pct: float, avg_vol: float,
                        recent_action: str = "", source_label: str = "",
                        historical_ratio: float = 0, intraday_ratio: float = 0) -> str:
    """生成 LLM 分析 prompt"""
    return PROMPT_ANALYSIS_TEMPLATE.format(
        ticker=ticker, price=price, change_pct=change_pct,
        ratio=ratio, avg_vol=avg_vol, recent_action=recent_action,
        source_label=source_label,
        historical_ratio=historical_ratio,
        intraday_ratio=intraday_ratio,
    )


def analyze_alert_with_llm(alert: dict, avg_vol: float) -> Optional[str]:
    """对触发信号的标的调用 LLM 分析"""
    source = alert.get("source", "")
    source_label = "日内+5日" if source == "mixed" else ("日内" if source == "intraday" else "5日")
    prompt = generate_llm_prompt(
        ticker=alert["ticker"],
        ratio=alert["ratio"],
        price=alert["price"],
        change_pct=alert["change_pct"],
        avg_vol=avg_vol,
        source_label=source_label,
        historical_ratio=alert.get("historical_ratio", 0),
        intraday_ratio=alert.get("intraday_ratio", 0),
    )
    return get_llm_analysis(prompt)


# === 信号去重状态机 ===

# 信号优先级（用于判断状态升级）
SIGNAL_PRIORITY = {
    "正常": 0,
    "缩量": 1,
    "放量": 2,
    "温放": 2,
    "放量突破": 3,
    "放量下跌": 3,
    "放量止跌": 3,
    "缩量止跌": 3,
    "尾盘放量": 3,
    "巨量": 4,
}

DB_PATH = ROOT / "data" / "ratios.db"


def get_signal_state(ticker: str) -> Optional[str]:
    """获取 ticker 的上一次信号状态"""
    if not DB_PATH.exists():
        return None
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            # 确保表存在
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_states (
                    ticker TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            row = conn.execute(
                "SELECT state FROM signal_states WHERE ticker = ?", (ticker,)
            ).fetchone()
            return row[0] if row else None
    except sqlite3.Error:
        return None


def update_signal_state(ticker: str, state: str):
    """更新 ticker 的信号状态"""
    if not DB_PATH.exists():
        return
    try:
        with sqlite3.connect(DB_PATH, timeout=5) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_states (
                    ticker TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO signal_states (ticker, state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET state = ?, updated_at = ?
            """, (ticker, state, datetime.now().isoformat(), state, datetime.now().isoformat()))
    except sqlite3.Error:
        pass


def should_push(ticker: str, new_state: str) -> bool:
    """
    信号去重判断：
    - 状态变化时推送
    - 状态持续时静默
    - 状态升级时再推送
    """
    old_state = get_signal_state(ticker)

    if old_state is None:
        # 首次出现，推送
        return True

    if old_state == new_state:
        # 状态持续，静默
        return False

    # 状态变化，检查是否升级
    old_priority = SIGNAL_PRIORITY.get(old_state, 0)
    new_priority = SIGNAL_PRIORITY.get(new_state, 0)

    if new_priority > old_priority:
        return True

    # 状态降级或同级变化也推送（不遗漏任何方向变化）
    return True


def scan_and_alert():
    """扫描并发送告警，触发信号时调用 LLM 分析（带去重状态机）"""
    from compute import compute_all, save_signal
    from core.config import load_config
    from core.market import get_all_tickers, get_market, is_market_trading

    lock_file = ROOT / "logs" / "alert.lock"
    lock_file.parent.mkdir(exist_ok=True)
    with open(lock_file, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lf.write(str(os.getpid()))
            lf.flush()
        except BlockingIOError:
            print("[alert] 上一次扫描仍在运行，跳过本轮")
            return

        config = load_config()
        tickers = get_all_tickers(config)
        active_markets = {get_market(t) for t in tickers}
        if not any(is_market_trading(market) for market in active_markets):
            print("[alert] 当前无开盘市场，跳过扫描")
            return

        results = compute_all()
        alerts = detect_signals(results)

        if not alerts:
            print(f"[alert] 无触发信号，共扫描 {len(results)} 个标的")
            return

        print(f"[alert] 检测到 {len(alerts)} 个信号，开始去重判断...")

        # LLM 调用限制：只对强信号调用，同一 ticker 只调一次
        seen_tickers = set()
        pushed_count = 0

        for alert in alerts:
            ticker = alert.get("ticker", "")
            ratio = alert.get("ratio", 0)
            signal = alert.get("signal", "")
            source = alert.get("source", "historical")

            # 确定信号状态
            if signal in ("放量止跌", "放量突破", "放量下跌", "缩量止跌"):
                new_state = signal
            elif ratio > 2.0:
                new_state = "放量突破" if alert.get("change_pct", 0) > 0 else "放量下跌"
            elif ratio > 1.5:
                new_state = "放量"
            elif ratio < 0.6:
                new_state = "缩量"
            else:
                new_state = "正常"

            # 去重判断
            if not should_push(ticker, new_state):
                print(f"[alert] {ticker} 状态持续 ({new_state})，跳过推送")
                continue

            # 更新状态
            update_signal_state(ticker, new_state)

            # 判断是否需要 LLM 分析
            is_significant = (
                signal in ("放量突破", "放量下跌") or
                (ratio > 2.5 and alert.get("change_pct", 0) != 0)
            )

            if ticker in seen_tickers:
                analysis = None
            elif is_significant:
                avg_vol = alert.get("volume_avg5", 0)
                analysis = analyze_alert_with_llm(alert, avg_vol)
                seen_tickers.add(ticker)
            else:
                analysis = None

            # 保存信号记录
            name = alert.get("name", ticker)
            save_signal(
                ticker=ticker, name=name, signal_type=new_state,
                ratio=ratio, price=alert.get("price", 0),
                change_pct=alert.get("change_pct", 0), source=source,
                llm_analysis=analysis or "", notified=1,
            )

            # 推送
            card = format_alert_card(alert, analysis)
            # 同时打印文本日志
            ticker = alert["ticker"]
            name = alert.get("name", ticker)
            ratio = alert["ratio"]
            change = alert["change_pct"]
            direction = "↑" if change > 0 else "↓"
            print(f"[alert] {ticker}-{name} {direction}{abs(change):.2f}% 量比{ratio:.2f} {alert.get('source','')}")
            send_feishu_card(card)
            pushed_count += 1
            print("---")

        print(f"[alert] 推送完成: {pushed_count}/{len(alerts)} 个信号")


def send_brief_report():
    """
    定时简报 - 每30分钟调用一次
    生成持仓组合量比概况，调用 LLM 做整体解读
    """
    from compute import compute_all
    from core.market import get_all_tickers, get_market, is_market_trading

    config = load_config()
    active_markets = {get_market(t) for t in get_all_tickers(config)}
    if not any(is_market_trading(market) for market in active_markets):
        print("[alert] 简报：当前无开盘市场")
        return

    results = compute_all()
    if not results:
        print("[alert] 简报：无数据")
        return

    # 只保留正在交易的市场
    results = [r for r in results if is_market_trading(get_market(r["ticker"]))]
    if not results:
        print("[alert] 简报：当前无开盘市场")
        return

    # 按量比排序
    sorted_results = sorted(results, key=lambda x: x.get("ratio", 0), reverse=True)

    # 构建飞书原生表格
    from core.display import build_brief_elements
    elements = build_brief_elements(sorted_results)

    # 构建纯文本用于 LLM prompt
    brief_lines = []
    for r in sorted_results:
        name = r.get("name", r["ticker"])
        brief_lines.append(f"{r['ticker']}-{name} 价格${r.get('price',0)} 涨跌{r.get('change_pct',0):.1f}% 量比{r.get('ratio',0):.2f}")
    brief_text = "\n".join(brief_lines)

    # 调用 LLM 整体解读
    analysis = None
    prompt = PROMPT_BRIEF_TEMPLATE.format(brief_text=brief_text)
    analysis = get_llm_analysis(prompt)

    if analysis:
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**LLM解读:** {analysis}"}})

    # 构建卡片
    now = datetime.now().strftime('%H:%M')
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": f"📋 量比简报 {now}"}},
        "elements": elements
    }

    print(f"[alert] 简报发送: {len(sorted_results)} 个标的")
    send_feishu_card(card)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", action="store_true", help="发送30分钟定时简报")
    args = parser.parse_args()

    if args.brief:
        send_brief_report()
    else:
        scan_and_alert()
