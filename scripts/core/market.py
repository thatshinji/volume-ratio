"""
共享市场工具模块 - 统一的市场判断和 watchlist 遍历
所有脚本通过 `from core.market import get_market, get_all_tickers` 使用
"""

from datetime import datetime, date, timedelta
import contextlib
import io

from .config import parse_ticker

# 交易日缓存: {market: (date_fetched, trading_days_set)}
_trading_days_cache = {}
# 指定区间交易日缓存: {(market, start, end): trading_days_set}
_trading_days_range_cache = {}
# 交易日查找缓存: {market: (start, end, trading_days_set)}
_trading_days_lookup_cache = {}


def _fetch_trading_days(market: str, start: date, end: date) -> set:
    """查询指定日期区间内的交易日集合。"""
    cache_key = (market, start, end)
    if cache_key in _trading_days_range_cache:
        return _trading_days_range_cache[cache_key]
    try:
        import os
        from longbridge.openapi import OAuthBuilder, Config, QuoteContext, Market
        token_dir = os.path.expanduser("~/.longbridge/openapi/tokens")
        files = os.listdir(token_dir)
        if not files:
            return set()
        cid = files[0]

        with contextlib.redirect_stdout(io.StringIO()):
            oauth = OAuthBuilder(cid).build(lambda url: None)
            config = Config.from_oauth(oauth)
            ctx = QuoteContext(config)
            market_enum = getattr(Market, market, None)
            if market_enum is None:
                return set()
            result = ctx.trading_days(market_enum, start, end)

        raw_days = getattr(result, "trading_days", result)
        if isinstance(raw_days, date):
            days = {raw_days}
        else:
            days = set(raw_days or [])
        _trading_days_range_cache[cache_key] = days
        return days
    except Exception:
        return set()


def _check_trading_days(market: str) -> set:
    """查询交易日集合（每天只查一次）"""
    today = date.today()
    if market in _trading_days_cache:
        cached_date, cached_data = _trading_days_cache[market]
        if cached_date == today:
            return cached_data
    end = today + timedelta(days=1)
    start = end - timedelta(days=10)
    days = _fetch_trading_days(market, start, end)
    _trading_days_cache[market] = (today, days)
    return days


def _is_trading_day(market: str) -> bool:
    """判断今天是否为该市场的交易日"""
    trading_days = _check_trading_days(market)
    if not trading_days:
        return True  # 查询失败时默认交易日
    return date.today() in trading_days


def is_trading_day_on(market: str, target_date: date) -> bool:
    """判断指定日期是否为交易日；查询失败时保守放行。

    为历史量比循环优化：一次拉宽窗口，后续同市场日期命中内存缓存。
    """
    cached = _trading_days_lookup_cache.get(market)
    if cached:
        start, end, cached_days = cached
        if start <= target_date <= end:
            if not cached_days:
                return True
            return target_date in cached_days

    start = target_date - timedelta(days=30)
    end = target_date + timedelta(days=5)
    trading_days = _fetch_trading_days(market, start, end)
    _trading_days_lookup_cache[market] = (start, end, trading_days)
    if not trading_days:
        return True
    return target_date in trading_days


def is_market_trading(market: str) -> bool:
    """判断市场当前是否在交易时间内（含假期检测）"""
    now = datetime.now()
    weekday = now.weekday()  # 0=周一, 6=周日

    # 周末不交易
    if weekday >= 5:
        return False

    # 假期检测：用 trading_days API 判断今天是否为交易日
    if not _is_trading_day(market):
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
        # 需要先把系统本地时间转为 UTC，再转为 ET
        try:
            import pytz
            et = pytz.timezone("US/Eastern")
            # 先获取当前 UTC 时间，再转为 ET
            utc_now = datetime.now(pytz.UTC)
            et_now = utc_now.astimezone(et)
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
