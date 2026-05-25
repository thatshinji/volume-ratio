"""共享量比阈值定义。

阈值与 get_signal() 和 format_ratio_display() 保持一致，
修改此处即可同步生效。
"""

RATIO_THRESHOLDS = [
    (0.0, 0.6, "数据不足", "缩量异常"),
    (0.6, 0.8, "缩量异常", "缩量"),
    (0.8, 1.2, "缩量", "正常"),
    (1.2, 2.0, "正常", "放量"),
    (2.0, 5.0, "放量", "显著放量"),
    (5.0, float("inf"), "显著放量", "巨量"),
]


def classify_ratio(ratio: float) -> str:
    """根据量比返回信号标签。"""
    if ratio <= 0:
        return "数据不足"
    for lo, hi, signal, display in RATIO_THRESHOLDS:
        if ratio <= hi:
            return signal
    return "巨量"


def ratio_display_label(ratio: float) -> str:
    """根据量比返回显示用符号+中文标识。"""
    if ratio <= 0:
        return "数据不足"
    for lo, hi, signal, display in RATIO_THRESHOLDS:
        if ratio <= hi:
            return display
    return "巨量"
