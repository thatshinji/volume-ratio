#!/usr/bin/env python3
"""
命令行入口 - 随时查询任意标的的量比
支持 --ticker / --scan / --market 模式
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict:
    import yaml
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_minimax_analysis(ticker: str, ratio: float, price: float,
                          change_pct: float, avg_vol: float) -> Optional[str]:
    """调用 MiniMax M2.7 API 进行分析"""
    config = load_config()
    minimax = config.get("minimax", {})
    api_key = minimax.get("api_key", "")

    if not api_key:
        return None

    base_url = minimax.get("base_url", "https://api.minimax.chat/v1")
    model = minimax.get("model", "MiniMax-M2.7")

    prompt = f"""你是量比分析专家。给定以下数据：
- 标的: {ticker}
- 当前价: {price} ({change_pct:+.2f}%)
- 量比: {ratio}
- 近5日均量: {avg_vol}

请用中文简短分析：
1. 量比异常的原因
2. 是否构成买入/卖出信号
3. 风险提示
限制100字以内。"""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 200,
        "temperature": 0.3
    }

    try:
        import requests
        resp = requests.post(
            f"{base_url}/v1/messages",
            headers=headers,
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            # MiniMax 返回 content 数组，需找 type="text" 的元素
            for item in data.get("content", []):
                if item.get("type") == "text":
                    return item.get("text", "")
            return None
        else:
            print(f"[cli] MiniMax API 错误: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"[cli] MiniMax 调用异常: {e}", file=sys.stderr)
        return None


def query_ticker(ticker: str, analyze: bool = False) -> dict:
    """查询单个标的的量比"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from compute import compute_ticker, get_latest_snapshot

    result = compute_ticker(ticker)
    if not result:
        return None

    snapshot = get_latest_snapshot(ticker)
    if snapshot:
        result["price"] = snapshot.get("price", 0)
        result["change_pct"] = snapshot.get("change_pct", 0)

    if analyze:
        analysis = get_minimax_analysis(
            ticker,
            result.get("ratio", 0),
            result.get("price", 0),
            result.get("change_pct", 0),
            result.get("volume_avg5", 0)
        )
        result["analysis"] = analysis

    return result


def scan_holdings() -> list:
    """扫描所有持仓"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from compute import compute_all

    config = load_config()
    watchlist = config.get("watchlist", {})

    tickers = []
    for market in ["us", "hk", "cn"]:
        tickers.extend(watchlist.get(market, []))

    results = compute_all()
    return results


def scan_market(market: str, min_ratio: float = 2.0) -> list:
    """扫描市场中放量标的"""
    sys.path.insert(0, str(ROOT / "scripts"))
    from compute import compute_all

    market_map = {"US": "us", "HK": "hk", "CN": "cn"}
    market_key = market_map.get(market.upper(), "us")

    results = compute_all()

    filtered = [r for r in results if r.get("ratio", 0) >= min_ratio]

    return filtered


def format_ticker_output(result: dict, with_analysis: bool = False) -> str:
    """格式化单个标的输出"""
    ticker = result.get("ticker", "")
    price = result.get("price", 0)
    change = result.get("change_pct", 0)
    ratio = result.get("ratio", 0)
    signal = result.get("signal", "")
    signal_detail = result.get("signal_detail", "")

    direction = "↑" if change > 0 else "↓"
    emoji = "🔥" if ratio > 2.0 else ("⚠️" if ratio < 0.8 else "✅")

    output = f"{ticker}  ${price:.2f}  {direction}{abs(change):.1f}%  量比: {ratio} ({signal}) {emoji}"

    if signal_detail:
        output += f"\n信号: {signal_detail}"

    if with_analysis and result.get("analysis"):
        output += f"\n[MiniMax分析] {result['analysis']}"

    return output


def main():
    parser = argparse.ArgumentParser(description="跨市场量比监控 CLI")
    parser.add_argument("--ticker", type=str, help="查询单个标的")
    parser.add_argument("--scan", choices=["holdings"], help="扫描持仓列表")
    parser.add_argument("--market", type=str, help="扫描市场 (US/HK/CN)")
    parser.add_argument("--min-ratio", type=float, default=2.0, help="最小量比阈值")
    parser.add_argument("--analyze", action="store_true", help="调用 MiniMax AI 分析")
    parser.add_argument("--collect", action="store_true", help="先采集最新行情再查询")

    args = parser.parse_args()

    if args.collect:
        sys.path.insert(0, str(ROOT / "scripts"))
        from collect import collect_all
        print("=== 采集行情 ===")
        collect_all()
        print()

    if args.ticker:
        print("=== 查询单个标的 ===")
        result = query_ticker(args.ticker, analyze=args.analyze)
        if result:
            print(format_ticker_output(result, with_analysis=args.analyze))
        else:
            print(f"无法获取 {args.ticker} 的数据", file=sys.stderr)
            sys.exit(1)

    elif args.scan == "holdings":
        print("=== 扫描持仓 ===")
        results = scan_holdings()
        if not results:
            print("无数据")
            sys.exit(1)

        for r in sorted(results, key=lambda x: x.get("ratio", 0), reverse=True):
            print(format_ticker_output(r))
        print(f"\n共 {len(results)} 个标的")

    elif args.market:
        print(f"=== 扫描 {args.market} 市场 (量比≥{args.min_ratio}) ===")
        results = scan_market(args.market, min_ratio=args.min_ratio)
        if not results:
            print("无放量标的")
            sys.exit(1)

        for r in sorted(results, key=lambda x: x.get("ratio", 0), reverse=True):
            print(format_ticker_output(r))
        print(f"\n共 {len(results)} 个放量标的")

    else:
        parser.print_help()
        print("\n=== 示例 ===")
        print("python3 cli.py --ticker CLF.US --analyze")
        print("python3 cli.py --scan holdings")
        print("python3 cli.py --market US --min-ratio 2.0")
        print("python3 cli.py --collect --ticker CLF.US")


if __name__ == "__main__":
    main()
