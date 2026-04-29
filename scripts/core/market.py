"""
共享市场工具模块 - 统一的市场判断和 watchlist 遍历
所有脚本通过 `from core.market import get_market, get_all_tickers` 使用
"""


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
    """从 watchlist 提取所有 ticker"""
    watchlist = config.get("watchlist", {})
    tickers = []
    for market in ["us", "hk", "cn"]:
        tickers.extend(watchlist.get(market, []))
    return tickers
