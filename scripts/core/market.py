"""
共享市场工具模块 - 统一的市场判断和 watchlist 遍历
所有脚本通过 `from core.market import get_market, get_all_tickers` 使用
"""

from .config import parse_ticker


def get_market(ticker: str) -> str:
    """根据 ticker 后缀判断市场"""
    if ticker.endswith(".US"):
        return "US"
    elif ticker.endswith(".HK"):
        return "HK"
    elif ticker.endswith(".SH") or ticker.endswith(".SZ"):
        return "CN"
    return "US"


def get_all_tickers(config: dict) -> list:
    """从 watchlist 提取所有 ticker（纯代码，兼容旧调用）"""
    watchlist = config.get("watchlist", {})
    tickers = []
    for market in ["us", "hk", "cn"]:
        for raw in watchlist.get(market, []):
            ticker, _ = parse_ticker(raw)
            tickers.append(ticker)
    return tickers


def get_all_tickers_with_names(config: dict) -> list:
    """从 watchlist 提取所有 (ticker, name) 元组"""
    watchlist = config.get("watchlist", {})
    result = []
    for market in ["us", "hk", "cn"]:
        for raw in watchlist.get(market, []):
            result.append(parse_ticker(raw))
    return result


def get_ticker_name(config: dict, target_ticker: str) -> str:
    """根据 ticker 代码查找中文名，未找到返回 ticker 本身"""
    for ticker, name in get_all_tickers_with_names(config):
        if ticker == target_ticker:
            return name
    return target_ticker
