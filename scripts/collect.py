#!/usr/bin/env python3
"""
行情采集脚本 - 从 Longbridge CLI 采集实时行情快照
cron: */1 9-16 * * 1-5  (A股/港股时段)
cron: */1 21-3  * * 1-5  (美股时段)
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 项目根目录
ROOT = Path(__file__).parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"

# 将 scripts/ 加入 sys.path
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config
from core.market import get_market, get_all_tickers


def get_longbridge_quote(ticker: str) -> Optional[dict]:
    """调用 longbridge CLI 获取行情"""
    try:
        result = subprocess.run(
            ["longbridge", "quote", "--format", "json", ticker],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            print(f"[collect] longbridge 调用失败: {result.stderr}", file=sys.stderr)
            return None

        output = result.stdout.strip()
        # 解析 longbridge 输出（JSON 格式）
        try:
            data = json.loads(output)
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            # 如果不是 JSON，尝试简单解析
            pass
        return None
    except FileNotFoundError:
        print("[collect] longbridge CLI 未找到，请先安装 longbridge", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print(f"[collect] longbridge 超时: {ticker}", file=sys.stderr)
        return None


def extract_fields(quote: dict, ticker: str) -> dict:
    """从行情数据中提取关键字段"""
    last = float(quote.get("last", 0) or 0)
    prev_close = float(quote.get("prev_close", 0) or 0)
    change = last - prev_close
    change_pct = (change / prev_close * 100) if prev_close > 0 else 0

    return {
        "ticker": ticker,
        "timestamp": datetime.now().isoformat(),
        "price": last,
        "open": float(quote.get("open", 0) or 0),
        "high": float(quote.get("high", 0) or 0),
        "low": float(quote.get("low", 0) or 0),
        "volume": int(quote.get("volume", 0) or 0),
        "turnover": float(quote.get("turnover", 0) or 0),
        "change": round(change, 4),
        "change_pct": round(change_pct, 2),
    }


def save_snapshot(ticker: str, data: dict):
    """追加写入 JSONL 文件（一行一条快照）"""
    market = get_market(ticker)
    market_dir = SNAPSHOT_DIR / market
    market_dir.mkdir(parents=True, exist_ok=True)

    day_str = datetime.now().strftime("%Y%m%d")
    filename = f"{ticker.replace('.', '_')}_{day_str}.jsonl"
    filepath = market_dir / filename

    with open(filepath, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")

    print(f"[collect] {ticker} -> {filepath.name}")


def collect_all():
    """采集所有监控标的"""
    config = load_config()
    tickers = get_all_tickers(config)

    print(f"[collect] 开始采集 {len(tickers)} 个标的...")

    for ticker in tickers:
        quote = get_longbridge_quote(ticker)
        if quote:
            data = extract_fields(quote, ticker)
            save_snapshot(ticker, data)
        else:
            print(f"[collect] 跳过 {ticker} (无数据)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ticker = sys.argv[1]
        quote = get_longbridge_quote(ticker)
        if quote:
            data = extract_fields(quote, ticker)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"无法获取 {ticker} 的行情数据", file=sys.stderr)
            sys.exit(1)
    else:
        collect_all()
