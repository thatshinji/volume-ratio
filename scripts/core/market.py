"""
共享市场工具模块 - 统一的市场判断和 watchlist 遍历
所有脚本通过 `from core.market import get_market, get_all_tickers` 使用
"""

from datetime import datetime

from .config import parse_ticker


def is_market_trading(market: str) -> bool:
    """判断市场当前是否在交易时间内"""
    now = datetime.now()
    weekday = now.weekday()  # 0=周一, 6=周日

    # 周末不交易
    if weekday >= 5:
        return False

    if market == "CN":
        # A股: 9:30-11:30, 13:00-15:00 (北京时间)
        t = now.hour * 100 + now.minute
        return (930 <= t <= 1130) or (1300 <= t <= 1500)

    elif market == "HK":
        # 港股: 9:30-12:00, 13:00-16:00 (香港时间，同北京时间)
        t = now.hour * 100 + now.minute
        return (930 <= t <= 1200) or (1300 <= t <= 1600)

    elif market == "US":
        # 美股: 9:30-16:00 ET (夏令时 UTC-4, 冬令时 UTC-5)
        # 北京时间: 夏令时 21:30-次日4:00, 冬令时 22:30-次日5:00
        # 简化处理: 用 pytz 转换
        try:
            import pytz
            et = pytz.timezone("US/Eastern")
            et_now = now.astimezone(et) if now.tzinfo else datetime.now(et)
            t = et_now.hour * 100 + et_now.minute
            return 930 <= t <= 1600
        except ImportError:
            # 无 pytz 时，粗略判断（北京时间 21:30-次日5:00）
            t = now.hour * 100 + now.minute
            return (2130 <= t <= 2359) or (0 <= t <= 500)

    return False


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
