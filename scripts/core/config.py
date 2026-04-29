"""
共享配置模块 - 统一的 load_config() 实现
所有脚本通过 `from core.config import load_config` 使用
"""

import functools
import yaml
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config.yaml"


@functools.lru_cache(maxsize=1)
def load_config() -> dict:
    """加载 config.yaml（带 LRU 缓存，避免重复读取磁盘）"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
