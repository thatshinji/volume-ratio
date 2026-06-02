# Changelog

## 2026-05-25

### Changed
- LLM 模型：`MiniMax-M2.7-highspeed` → `MiniMax-M3`
  - 涉及：`config.yaml` 中 `llm.model` 和 `llm_profiles.minimax.model`

### Fixed
- DuckDB 连接添加重试逻辑，绕过 macOS 文件锁冲突
  - `scripts/feishu_bot.py`、`scripts/alert.py`、`scripts/cleanup.py`、`scripts/cli.py`
  - `scripts/llm.py`、`scripts/backfill_signals.py`、`scripts/backfill_minute_bars.py`
- DeepSeek 审查问题修复
  - **#2** `_resolve_session_time` 死代码 + fallback 缺失
  - **#5** 飞书卡片超尺寸分批（`MAX_ALERTS_PER_CARD=20`）
  - **#9** 阈值重复维护 → `scripts/core/thresholds.py`
  - **#15** `cmd_status` 竞态条件 → `_pid_is_alive()`
  - **#18** 尾盘时间硬编码 → 引用 `MARKET_END_OF_DAY`

### Pending
详见 `deepseek-issue.md` 表格
