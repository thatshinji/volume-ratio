"""
共享配置模块 - 统一的 load_config() 实现
所有脚本通过 `from core.config import load_config` 使用

支持热加载：修改 config.yaml 后自动生效，无需重启进程
"""

import yaml
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"

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


def parse_ticker(raw: str) -> Tuple[str, str]:
    """
    解析带中文名的 ticker 格式
    'CLF.US-克利夫兰' → ('CLF.US', '克利夫兰')
    'CLF.US'          → ('CLF.US', 'CLF.US')
    """
    if '-' in raw:
        parts = raw.rsplit('-', 1)
        return (parts[0], parts[1])
    return (raw, raw)
