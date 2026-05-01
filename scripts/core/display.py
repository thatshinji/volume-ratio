"""
共享显示模块 - 量比符号、格式化输出、飞书表格构建
"""

from typing import List


def format_ratio_display(ratio: float) -> str:
    """
    量比显示：符号 + 中文双标识
    阈值与 get_signal() 保持一致
    """
    if ratio > 5.0:
        return "⬆⬆⬆ 巨量"
    elif ratio > 2.0:
        return "⬆⬆  显著放量"
    elif ratio > 1.2:
        return "⬆   放量"
    elif ratio > 0.8:
        return "─    正常"
    elif ratio > 0.6:
        return "⬇   缩量"
    else:
        return "⬇⬇  缩量异常"


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


def build_market_table(label: str, tickers: list) -> list:
    """为一个市场构建飞书原生表格元素"""
    # 飞书表格 page_size 限制，最大 100
    MAX_PAGE_SIZE = 100
    columns = [
        {"name": "ticker", "display_name": "标的", "width": "auto", "horizontal_align": "left", "data_type": "text"},
        {"name": "price", "display_name": "价格", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "change", "display_name": "涨跌", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "ratio", "display_name": "5日量比", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "intraday", "display_name": "日内", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "samples", "display_name": "样本", "width": "auto", "horizontal_align": "right", "data_type": "text"},
        {"name": "status", "display_name": "状态", "width": "auto", "horizontal_align": "left", "data_type": "text"},
    ]

    rows = []
    for r in tickers[:MAX_PAGE_SIZE]:
        ratio = r.get("ratio", 0)
        intraday_ratio = r.get("ratio_intraday", 0)
        change = r.get("change_pct", 0)
        name = r.get("name", r["ticker"])
        ticker = r["ticker"]
        price = r.get("price", 0)
        direction = "↑" if change > 0 else ("↓" if change < 0 else "─")
        ratio_display = format_ratio_display(ratio)
        emoji = "🔥" if ratio > 2.0 else ("⚠️" if ratio < 0.8 else "✅")
        rows.append({
            "ticker": f"{ticker}-{name}",
            "price": f"${price}",
            "change": f"{direction}{abs(change):.1f}%",
            "ratio": f"{ratio:.2f}",
            "intraday": f"{intraday_ratio:.2f}" if intraday_ratio else "-",
            "samples": f"{r.get('historical_sample_days', 0)}/5",
            "status": f"{emoji} {ratio_display}",
        })

    return [
        {"tag": "markdown", "content": f"**{label}**"},
        {
            "tag": "table",
            "page_size": len(rows),
            "row_height": "low",
            "header_style": {
                "text_align": "left",
                "text_size": "normal",
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "columns": columns,
            "rows": rows,
        },
    ]


def build_brief_elements(sorted_results: list) -> list:
    """构建简报的飞书卡片元素列表（原生表格）"""
    us = [r for r in sorted_results if r["ticker"].endswith(".US")]
    hk = [r for r in sorted_results if r["ticker"].endswith(".HK")]
    cn = [r for r in sorted_results if r["ticker"].endswith((".SH", ".SZ"))]

    elements = []
    for label, tickers in [("🇺🇸 美股", us), ("🇭🇰 港股", hk), ("🇨🇳 A股", cn)]:
        if not tickers:
            continue
        elements.extend(build_market_table(label, tickers))
    return elements
