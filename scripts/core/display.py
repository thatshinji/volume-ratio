"""
共享显示模块 - 量比符号、格式化输出
"""


def format_ratio_display(ratio: float) -> str:
    """
    量比显示：符号 + 中文双标识
    用符号快速扫描，中文确认含义
    """
    if ratio > 5.0:
        return "⬆⬆⬆ 巨量"
    elif ratio > 2.0:
        return "⬆⬆  放量"
    elif ratio > 1.5:
        return "⬆   温放"
    elif ratio > 0.8:
        return "─    正常"
    elif ratio > 0.5:
        return "⬇   缩量"
    else:
        return "⬇⬇  地量"


def format_ticker_line(ticker: str, name: str, change_pct: float,
                       ratio: float, extra: str = "") -> str:
    """
    统一格式：代码  中文名  涨跌  量比 符号 中文  状态
    示例: CLF.US    克利夫兰   ↓3.0%  2.1 ⬆⬆  放量  🔥
    """
    direction = "↑" if change_pct > 0 else "↓"
    ratio_str = format_ratio_display(ratio)
    emoji = "🔥" if ratio > 2.0 else ("⚠️" if ratio < 0.8 else "✅")

    line = f"{ticker:<12} {name:<8} {direction}{abs(change_pct):>5.1f}%  {ratio:>4.1f} {ratio_str}  {emoji}"
    if extra:
        line += f"  {extra}"
    return line
