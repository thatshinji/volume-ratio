#!/usr/bin/env python3
"""
通用 LLM 调用层 - 支持多模型一键切换
配置来自 config.yaml 的 llm 节

Usage:
    from llm import call_llm
    result = call_llm("分析以下数据...")
"""

import sys
import requests
from typing import Optional
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.yaml"

# 将 scripts/ 加入 sys.path
sys.path.insert(0, str(ROOT / "scripts"))

from core.config import load_config


def mask_key(key: str) -> str:
    """脱敏显示 API Key，只显示前6位和后4位"""
    if not key or len(key) < 12:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def get_llm_config() -> dict:
    """获取当前激活的 LLM 配置"""
    config = load_config()
    llm = config.get("llm", {})
    # 兼容旧配置（minimax 节）
    if not llm:
        minimax = config.get("minimax", {})
        if minimax:
            return {
                "provider": "minimax",
                "model": minimax.get("model", "MiniMax-M2.7"),
                "base_url": minimax.get("base_url", "https://api.minimaxi.com/anthropic"),
                "api_key": minimax.get("api_key", ""),
            }
    return {
        "provider": llm.get("provider", "minimax"),
        "model": llm.get("model", "MiniMax-M2.7"),
        "base_url": llm.get("base_url", "https://api.minimaxi.com/anthropic"),
        "api_key": llm.get("api_key", ""),
    }


def list_profiles() -> list:
    """列出所有可用模型配置"""
    config = load_config()
    profiles = config.get("llm_profiles", {})
    return list(profiles.keys())


def switch_llm(profile_name: str) -> bool:
    """
    一键切换 LLM 模型
    将 llm_profiles.{profile_name} 的配置复制到顶层的 llm 配置
    Usage: switch_llm("xiaomi")
    """
    config = load_config()
    profiles = config.get("llm_profiles", {})
    if profile_name not in profiles:
        print(f"[llm] 未知的 profile: {profile_name}")
        return False

    profile = profiles[profile_name]
    # 更新顶层 llm 配置
    config["llm"] = {
        "provider": profile.get("provider", "minimax"),
        "model": profile.get("model", ""),
        "base_url": profile.get("base_url", ""),
        "api_key": profile.get("api_key", ""),
        "max_tokens": profile.get("max_tokens", 800),
        "temperature": profile.get("temperature", 0.3),
    }

    # 写回 config.yaml
    import yaml
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"[llm] 已切换到: {profile_name} ({profile.get('model', '')})")
    return True


def call_llm(prompt: str, model: str = None) -> Optional[str]:
    """
    通用 LLM 调用
    根据 config 里的 provider 自动选择调用方式
    """
    cfg = get_llm_config()
    api_key = cfg.get("api_key", "")
    if not api_key:
        print("[llm] API key 未配置")
        return None

    base_url = cfg.get("base_url", "")
    model_name = model or cfg.get("model", "")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": cfg.get("max_tokens", 800),
        "temperature": cfg.get("temperature", 0.3)
    }

    try:
        # Anthropic 兼容接口（MiniMax, Xiaomi, OpenAI 等都兼容）
        endpoint = f"{base_url}/v1/messages"
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=30)

        if resp.status_code == 200:
            data = resp.json()
            content = data.get("content", [])

            # 通用解析：找第一个 text 类型
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    return item.get("text", "")
            return None
        else:
            print(f"[llm] API 错误: {resp.status_code}")
            return None
    except (requests.ConnectionError, requests.Timeout, ValueError) as e:
        print(f"[llm] 调用异常: {e}")
        return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LLM 多模型切换工具")
    parser.add_argument("--list", action="store_true", help="列出所有可用模型")
    parser.add_argument("--switch", type=str, help="切换到指定模型 (minimax/xiaomi)")
    parser.add_argument("--test", action="store_true", help="测试当前配置")
    args = parser.parse_args()

    if args.list:
        profiles = list_profiles()
        current = get_llm_config().get("provider", "")
        print(f"可用模型: {profiles}")
        print(f"当前使用: {current}")
    elif args.switch:
        success = switch_llm(args.switch)
        if success:
            print(f"切换成功，当前模型: {get_llm_config().get('model', '')}")
        else:
            print("切换失败")
    elif args.test:
        cfg = get_llm_config()
        safe_cfg = {k: mask_key(v) if k == "api_key" else v for k, v in cfg.items()}
        print(f"当前配置: {safe_cfg}")
        result = call_llm("说一句简短的话，5字以内")
        print(f"测试结果: {result}")
    else:
        parser.print_help()
        print("\n示例:")
        print("  python3 llm.py --list                    # 查看所有模型")
        print("  python3 llm.py --switch xiaomi           # 切换到小米模型")
        print("  python3 llm.py --switch minimax         # 切换回 MiniMax")
        print("  python3 llm.py --test                   # 测试当前配置")
