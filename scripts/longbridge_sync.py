#!/usr/bin/env python3
"""
长桥持仓 + 自选股同步到 config.yaml watchlist
合并逻辑：持仓 + 指定分组 → 去重 → 按市场分类 → 写入 config.yaml
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import yaml
from core.config import CONFIG_PATH, load_config
from core.market import get_market


def _get_longbridge_context():
    """获取长桥 API 上下文"""
    from longbridge.openapi import OAuthBuilder, Config, QuoteContext, TradeContext

    token_dir = Path.home() / ".longbridge" / "openapi" / "tokens"
    files = list(token_dir.iterdir())
    if not files:
        raise OSError("长桥 token 目录为空，请先登录长桥")
    cid = files[0].name
    oauth = OAuthBuilder(cid).build(lambda url: None)
    config = Config.from_oauth(oauth)
    return QuoteContext(config), TradeContext(config)


def fetch_positions(trade_ctx) -> list:
    """获取持仓列表，返回 [(ticker, name), ...]"""
    result = []
    try:
        resp = trade_ctx.stock_positions()
        for ch in resp.channels:
            for pos in ch.positions:
                symbol = pos.symbol
                name = pos.symbol_name
                # 过滤期权（ticker 含日期格式如 260717C）
                if _is_option(symbol):
                    continue
                result.append((symbol, name))
    except Exception as e:
        print(f"[sync] 获取持仓失败: {e}")
    return result


def fetch_watchlist_group(quote_ctx, group_name: str) -> list:
    """获取指定自选股分组，返回 [(ticker, name), ...]"""
    result = []
    try:
        for group in quote_ctx.watchlist():
            if group.name == group_name:
                for sec in group.securities:
                    result.append((sec.symbol, sec.name))
                break
    except Exception as e:
        print(f"[sync] 获取自选股分组失败: {e}")
    return result


def _is_option(symbol: str) -> bool:
    """粗略判断是否为期权 ticker（含 C/P + 日期格式）"""
    import re
    return bool(re.search(r'\d{6}[CP]\d+', symbol))


def merge_tickers(positions: list, watchlist: list) -> dict:
    """
    合并持仓和自选股，按市场分组
    返回: {"us": ["CLF.US-克利夫兰", ...], "hk": [...], "cn": [...]}
    """
    # 去重，ticker 为 key，name 取后者覆盖
    seen = {}
    for ticker, name in positions + watchlist:
        seen[ticker] = name

    result = {"us": [], "hk": [], "cn": []}
    for ticker, name in sorted(seen.items()):
        market = get_market(ticker).lower()
        if market in result:
            result[market].append(f"{ticker}-{name}")

    return result


def sync_to_config(new_watchlist: dict) -> dict:
    """
    将新的 watchlist 写入 config.yaml
    返回: {"added": [...], "removed": [...], "unchanged": [...]}
    """
    config = load_config()
    old_watchlist = config.get("watchlist", {})

    # 收集旧的和新的 ticker 集合
    old_tickers = set()
    for market in ["us", "hk", "cn"]:
        for raw in old_watchlist.get(market, []):
            old_tickers.add(raw.split("-")[0])

    new_tickers = set()
    for market in ["us", "hk", "cn"]:
        for raw in new_watchlist.get(market, []):
            new_tickers.add(raw.split("-")[0])

    added = sorted(new_tickers - old_tickers)
    removed = sorted(old_tickers - new_tickers)
    unchanged = sorted(old_tickers & new_tickers)

    # 写入 config.yaml
    config["watchlist"] = {
        "us": new_watchlist.get("us", []),
        "hk": new_watchlist.get("hk", []),
        "cn": new_watchlist.get("cn", []),
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return {"added": added, "removed": removed, "unchanged": unchanged}


def remove_from_watchlist(ticker: str, group_name: str = "量比监控") -> bool:
    """从长桥自选股分组中移除指定标的"""
    try:
        quote_ctx, _ = _get_longbridge_context()
        for group in quote_ctx.watchlist():
            if group.name == group_name:
                from longbridge.openapi import SecuritiesUpdateMode
                quote_ctx.update_watchlist_group(
                    id=group.id,
                    securities=[ticker],
                    mode=SecuritiesUpdateMode.Remove,
                )
                print(f"[sync] 已从「{group_name}」移除: {ticker}")
                return True
        print(f"[sync] 未找到分组: {group_name}")
        return False
    except Exception as e:
        print(f"[sync] 移除失败: {e}")
        return False


def run_sync(groups: list = None) -> dict:
    """
    执行完整同步流程
    groups: 长桥自选股分组名列表，默认 ["量比监控"]
    返回: {"added": [...], "removed": [...], "positions": [...], "watchlist": [...]}
    """
    if groups is None:
        groups = ["量比监控"]

    quote_ctx, trade_ctx = _get_longbridge_context()

    # 1. 获取持仓
    positions = fetch_positions(trade_ctx)
    print(f"[sync] 持仓: {[t[0] for t in positions]}")

    # 2. 获取自选股分组
    watchlist = []
    for group_name in groups:
        group_tickers = fetch_watchlist_group(quote_ctx, group_name)
        print(f"[sync] 分组「{group_name}」: {[t[0] for t in group_tickers]}")
        watchlist.extend(group_tickers)

    # 3. 合并
    new_watchlist = merge_tickers(positions, watchlist)

    # 4. 写入 config
    changes = sync_to_config(new_watchlist)

    total = sum(len(v) for v in new_watchlist.values())
    print(f"[sync] 完成: {total} 个标的, 新增 {len(changes['added'])}, 移除 {len(changes['removed'])}")

    return {
        "added": changes["added"],
        "removed": changes["removed"],
        "positions": [t[0] for t in positions],
        "watchlist": [t[0] for t in watchlist],
        "final": new_watchlist,
    }


if __name__ == "__main__":
    result = run_sync()
    print(f"\n=== 同步结果 ===")
    print(f"持仓: {result['positions']}")
    print(f"自选股: {result['watchlist']}")
    print(f"新增: {result['added']}")
    print(f"移除: {result['removed']}")
    for market, tickers in result['final'].items():
        if tickers:
            print(f"\n{market.upper()}:")
            for t in tickers:
                print(f"  {t}")
