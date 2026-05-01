"""
共享配置模块 - 统一的 load_config() 实现
所有脚本通过 `from core.config import load_config` 使用

支持热加载：修改 config.yaml 后自动生效，无需重启进程
"""

import fcntl
import os

import yaml
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"
CONFIG_LOCK_PATH = ROOT / "logs" / "config.lock"

# 热加载缓存
_config_cache = None
_config_mtime = 0


def load_config() -> dict:
    """加载 config.yaml（基于文件修改时间的热加载）"""
    global _config_cache, _config_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if mtime != _config_mtime:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f)
        _config_mtime = mtime
    return _config_cache or {}


def save_config(config: dict):
    """原子写入 config.yaml，并同步刷新本进程缓存。"""
    global _config_cache, _config_mtime
    CONFIG_LOCK_PATH.parent.mkdir(exist_ok=True)
    tmp_path = CONFIG_PATH.with_name(f"{CONFIG_PATH.name}.tmp")
    with open(CONFIG_LOCK_PATH, "a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, CONFIG_PATH)
            try:
                _config_mtime = CONFIG_PATH.stat().st_mtime
            except OSError:
                _config_mtime = 0
            _config_cache = config
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def remove_ticker_from_config(ticker: str) -> bool:
    """从 config.yaml watchlist 中移除指定标的"""
    config = load_config()
    removed = False
    for market in ["us", "hk", "cn"]:
        items = config.get("watchlist", {}).get(market, [])
        new_items = [item for item in items if not item.startswith(ticker + "-") and item != ticker]
        if len(new_items) < len(items):
            config["watchlist"][market] = new_items
            removed = True
    if removed:
        save_config(config)
    return removed


def parse_ticker(raw: str) -> Tuple[str, str]:
    """
    解析带中文名的 ticker 格式
    'CLF.US-克利夫兰'      → ('CLF.US', '克利夫兰')
    '1810.HK-XIAOMI-W'    → ('1810.HK', 'XIAOMI-W')
    'CLF.US'               → ('CLF.US', 'CLF.US')
    """
    import re
    # ticker 格式: 字母/数字.市场后缀，如 CLF.US、1810.HK、600029.SH
    m = re.match(r'^([A-Za-z0-9]+\.[A-Za-z]+)-(.+)$', raw)
    if m:
        return (m.group(1), m.group(2))
    return (raw, raw)
