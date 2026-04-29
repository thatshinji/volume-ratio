#!/usr/bin/env python3
"""
信号检测 + 飞书推送
cron 每1分钟扫描，触发信号时通过 webhook 推送到飞书
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests

ROOT = Path(__file__).parent.parent

# 将 scripts/ 加入 sys.path，供 from compute import 等使用
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config


# === 信号规则 ===

SIGNAL_RULES = {
    "放量突破": lambda ratio, change: ratio > 2.0 and change > 2,
    "放量下跌": lambda ratio, change: ratio > 2.0 and change < -2,
    "缩量止跌": lambda ratio, change: ratio < 0.6 and change > 0,
    "尾盘放量": lambda ratio, change: ratio > 1.5 and 14 <= datetime.now().hour <= 15,
}


# === LLM Prompt 模板 ===

PROMPT_ANALYSIS_TEMPLATE = """你是量比分析专家。给定以下数据：
- 标的: {ticker}
- 当前价: {price} ({change_pct:+.2f}%)
- 量比: {ratio}
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
    alerts = []
    config = load_config()
    params = config.get("params", {})
    alert_threshold = params.get("alert_threshold", 2.0)
    shrink_threshold = params.get("shrink_threshold", 0.6)

    for r in results:
        ticker = r.get("ticker", "")
        ratio = r.get("ratio", 0)
        change_pct = r.get("change_pct", 0)
        signal = r.get("signal", "")
        signal_detail = r.get("signal_detail", "")

        # === Historical 路径（5日历史量比）===
        triggered = []
        for name, rule in SIGNAL_RULES.items():
            if rule(ratio, change_pct):
                triggered.append(name)

        if signal != "数据不足":
            if ratio > alert_threshold:
                triggered.append(f"放量(>{alert_threshold})")
            elif ratio < shrink_threshold:
                triggered.append(f"缩量(<{shrink_threshold})")

        if triggered or signal_detail:
            alerts.append({
                "ticker": ticker,
                "ratio": ratio,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": signal,
                "signal_detail": signal_detail,
                "triggered_signals": triggered,
                "source": "historical",
            })

        # === Intraday 路径（滚动量比 + 三条件）===
        ratio_intraday = r.get("ratio_intraday", 0)
        signal_intraday = r.get("signal_intraday", "")
        cond_vol = r.get("cond_vol", False)
        cond_stop = r.get("cond_stop", False)
        cond_stable = r.get("cond_stable", False)

        if signal_intraday == "放量止跌":
            alerts.append({
                "ticker": ticker,
                "ratio": ratio_intraday,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": "放量止跌",
                "signal_detail": f"放量={cond_vol} 止跌={cond_stop} 企稳={cond_stable}",
                "triggered_signals": ["放量止跌"],
                "source": "intraday",
            })
        elif signal_intraday == "放量" and ratio_intraday > 1.5:
            alerts.append({
                "ticker": ticker,
                "ratio": ratio_intraday,
                "change_pct": change_pct,
                "price": r.get("price", 0),
                "signal": "放量",
                "signal_detail": f"放量={cond_vol} 止跌={cond_stop} 企稳={cond_stable}",
                "triggered_signals": ["放量"],
                "source": "intraday",
            })

    return alerts


def format_alert_message(alert: dict, analysis: Optional[str] = None) -> str:
    """格式化飞书消息（可选 LLM 分析）"""
    ticker = alert["ticker"]
    ratio = alert["ratio"]
    change = alert["change_pct"]
    price = alert["price"]
    signals = ", ".join(alert["triggered_signals"]) or alert["signal_detail"] or alert["signal"]
    source = alert.get("source", "historical")

    emoji = "🔥" if ratio > 2.0 else "⚠️"
    direction = "↑" if change > 0 else "↓"

    type_label = "【日内】" if source == "intraday" else "【5日】"

    msg = f"""{emoji} {type_label}{ticker}
当前价: {price} ({direction}{abs(change):.2f}%)
量比: {ratio} ({signals})
时间: {datetime.now().strftime('%H:%M:%S')}
"""
    if analysis:
        msg += f"\n[LLM分析] {analysis}"

    return msg


def send_feishu(message: str, webhook_url: str) -> bool:
    """发送飞书消息"""
    if not webhook_url:
        print("[alert] 飞书 webhook 未配置，跳过推送")
        return False

    payload = {
        "msg_type": "text",
        "content": {"text": message}
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"[alert] 飞书推送成功")
            return True
        else:
            print(f"[alert] 飞书推送失败: {resp.status_code}")
            return False
    except (requests.ConnectionError, requests.Timeout) as e:
        print(f"[alert] 飞书推送异常: {e}")
        return False


def get_llm_analysis(prompt: str) -> Optional[str]:
    """调用通用 LLM 分析（支持多模型切换）"""
    from llm import call_llm
    return call_llm(prompt)


def generate_llm_prompt(ticker: str, ratio: float, price: float,
                        change_pct: float, avg_vol: float,
                        recent_action: str = "") -> str:
    """生成 LLM 分析 prompt"""
    return PROMPT_ANALYSIS_TEMPLATE.format(
        ticker=ticker, price=price, change_pct=change_pct,
        ratio=ratio, avg_vol=avg_vol, recent_action=recent_action,
    )


def analyze_alert_with_llm(alert: dict, avg_vol: float) -> Optional[str]:
    """对触发信号的标的调用 LLM 分析"""
    prompt = generate_llm_prompt(
        ticker=alert["ticker"],
        ratio=alert["ratio"],
        price=alert["price"],
        change_pct=alert["change_pct"],
        avg_vol=avg_vol,
    )
    return get_llm_analysis(prompt)


def scan_and_alert():
    """扫描并发送告警，触发信号时调用 LLM 分析"""
    from compute import compute_all

    config = load_config()
    feishu_config = config.get("feishu", {})
    webhook_url = feishu_config.get("webhook_url", "")

    results = compute_all()
    alerts = detect_signals(results)

    if not alerts:
        print(f"[alert] 无触发信号，共扫描 {len(results)} 个标的")
        return

    print(f"[alert] 检测到 {len(alerts)} 个信号，开始推送...")

    # LLM 调用限制：只对强信号调用，同一 ticker 只调一次
    seen_tickers = set()
    for alert in alerts:
        ticker = alert.get("ticker", "")
        ratio = alert.get("ratio", 0)
        signal = alert.get("signal", "")

        # 判断是否需要 LLM 分析（只对强信号调用，省 API 额度）
        is_significant = (
            signal in ("放量突破", "放量下跌") or
            (ratio > 2.5 and alert.get("change_pct", 0) != 0)
        )

        # 同一 ticker 多个 source 时只调用一次
        if ticker in seen_tickers:
            analysis = None
        elif is_significant:
            avg_vol = alert.get("volume_avg5", 0)
            analysis = analyze_alert_with_llm(alert, avg_vol)
            seen_tickers.add(ticker)
        else:
            analysis = None

        message = format_alert_message(alert, analysis)
        print(message)
        if webhook_url:
            send_feishu(message, webhook_url)
        print("---")


def send_brief_report():
    """
    定时简报 - 每30分钟调用一次
    生成持仓组合量比概况，调用 LLM 做整体解读
    """
    from compute import compute_all

    config = load_config()
    feishu_config = config.get("feishu", {})
    webhook_url = feishu_config.get("webhook_url", "")

    results = compute_all()
    if not results:
        print("[alert] 简报：无数据")
        return

    # 按量比排序
    sorted_results = sorted(results, key=lambda x: x.get("ratio", 0), reverse=True)

    # 生成分市场简报
    lines = [f"📊 量比简报 {datetime.now().strftime('%H:%M')}"]
    lines.append("")

    us_tickers = [r for r in sorted_results if r["ticker"].endswith(".US")]
    hk_tickers = [r for r in sorted_results if r["ticker"].endswith(".HK")]
    cn_tickers = [r for r in sorted_results if r["ticker"].endswith((".SH", ".SZ"))]

    for label, tickers in [("🇺🇸 美股", us_tickers), ("🇭🇰 港股", hk_tickers), ("🇨🇳 A股", cn_tickers)]:
        if not tickers:
            continue
        lines.append(f"{label}:")
        for r in tickers:
            ratio = r.get("ratio", 0)
            change = r.get("change_pct", 0)
            direction = "↑" if change > 0 else "↓"
            emoji = "🔥" if ratio > 2.0 else ("⚠️" if ratio < 0.8 else "✅")
            status = "巨量" if ratio > 5 else ("显著放量" if ratio > 2 else ("放量" if ratio > 1.2 else ("缩量" if ratio < 0.8 else "正常")))
            lines.append(f"  {r['ticker']} {direction}{abs(change):.1f}% 量比{ratio} {emoji} {status}")
        lines.append("")

    brief_text = "\n".join(lines)

    # 调用 LLM 整体解读
    analysis = None
    prompt = PROMPT_BRIEF_TEMPLATE.format(brief_text=brief_text)
    analysis = get_llm_analysis(prompt)

    message = brief_text
    if analysis:
        message += f"\n[LLM解读] {analysis}"

    print(message)
    if webhook_url:
        send_feishu(message, webhook_url)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--brief", action="store_true", help="发送30分钟定时简报")
    args = parser.parse_args()

    if args.brief:
        send_brief_report()
    else:
        scan_and_alert()
