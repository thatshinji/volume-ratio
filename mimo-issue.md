# 跨市场量比监控系统 — 问题清单

**扫描日期**: 2026-04-29
**项目版本**: v2.0
**扫描范围**: 框架设计、编码安全、编码质量

---

## 一、框架设计问题

### 🔴 P0 严重问题

#### 1.1 `scan_and_alert()` 函数重复定义（BUG）
- **文件**: `scripts/alert.py` 第 151 行和第 257 行
- **问题**: 同一函数定义两次，Python 使用最后一个定义，前者永远不会被调用
- **影响**: 开发者以为调用的是简单版本，实际调用的是会消耗 LLM API 额度的复杂版本
- **修复**: 删除第 151-174 行的旧版本

#### 1.2 A 股简报 BUG
- **文件**: `scripts/alert.py` 第 332 行
- **问题**: A 股使用 `.SH`/`.SZ` 后缀，但代码用 `.CN` 过滤
- **影响**: A 股标的永远不会出现在简报中
- **修复**: 改为判断 `.SH` 或 `.SZ`

#### 1.3 API Key 明文存储
- **文件**: `config.yaml` 第 68、81、87 行
- **问题**: 3 个 API Key 和 1 个飞书 Webhook URL 明文存储
- **影响**: 密钥泄露可导致 API 滥用、资金损失
- **修复**: 迁移至环境变量或密钥管理服务

### 🟡 P1 中等问题

#### 1.4 `load_config()` 在 6 个文件中重复
- **文件**: collect.py, collect_ws.py, compute.py, alert.py, cli.py, llm.py
- **问题**: 完全相同的函数分散在 6 个文件，无缓存，每次读磁盘
- **影响**: 性能浪费，维护困难
- **修复**: 创建 `scripts/core/config.py`，使用 LRU 缓存

#### 1.5 `get_market()` 在 3 个文件中重复
- **文件**: collect.py, collect_ws.py, compute.py
- **问题**: 市场判断逻辑重复，A 股 `.SZ` 后缀判断可能遗漏
- **修复**: 创建 `scripts/core/market.py`

#### 1.6 快照文件爆炸
- **文件**: `scripts/collect_ws.py`, `scripts/compute.py`
- **问题**: 每秒一个 JSON 文件，3 小时产生 15,993 个文件，线性扫描性能退化
- **影响**: `list_snapshots()` 每次遍历全部文件，计算 11 个标的需要 O(11N)
- **修复**: 改用 SQLite 或 JSONL 单文件追加存储

#### 1.7 `cli.py` 硬编码 MiniMax API
- **文件**: `scripts/cli.py` 第 25-84 行
- **问题**: 未使用 `llm.py` 的统一抽象层，直接硬编码调用
- **影响**: 修改 LLM 逻辑需同时改两处
- **修复**: 改用 `llm.call_llm()`

### ⚪ P2 低等问题

#### 1.8 `sys.path.insert` hack
- **文件**: `scripts/alert.py`, `scripts/cli.py`（共 4 处）
- **问题**: 动态修改 `sys.path` 而非使用正式包导入
- **修复**: 添加正确的 `__init__.py`，使用相对导入

#### 1.9 双层 fork daemon 实现重复
- **文件**: `scripts/collect_ws.py`, `scripts/collect_ws_launcher.py`
- **问题**: 两处各自实现双层 fork，代码高度重复，无优雅停机
- **修复**: 使用 `python-daemon` 或 `supervisord`

#### 1.10 无日志框架
- **文件**: 所有脚本
- **问题**: 使用 `print()` 作为日志，无轮转、无级别、无结构化
- **影响**: `ws_collect.log` 已达 8.2MB，调试困难
- **修复**: 引入 `logging` 模块 + `RotatingFileHandler`

---

## 二、编码安全问题

### 🔴 P0 高风险

#### 2.1 `--test` 模式泄露 API Key
- **文件**: `scripts/llm.py` 第 162 行
- **问题**: `--test` 参数将完整配置（含 API Key）打印到 stdout
- **影响**: 日志中可见密钥
- **修复**: 脱敏显示，API Key 只显示前 8 位

#### 2.2 API 错误日志泄露请求参数
- **文件**: `scripts/llm.py` 第 129 行, `scripts/cli.py` 第 80 行
- **问题**: API 响应 body 可能包含请求参数回显（含 API Key）
- **修复**: 只记录状态码，不记录 response body

### 🟡 P1 中风险

#### 2.3 路径遍历风险
- **文件**: `scripts/collect.py`, `scripts/collect_ws.py`
- **问题**: `ticker` 如果包含 `../` 等路径字符，可写入任意路径
- **修复**: 添加 `sanitize_ticker()` 函数过滤路径分隔符

#### 2.4 CLI ticker 参数无验证
- **文件**: `scripts/collect.py` 第 131 行, `scripts/compute.py` 第 422 行, `scripts/cli.py` 第 170 行
- **问题**: 命令行参数直接使用，无格式验证
- **修复**: 添加正则验证 `^[A-Za-z0-9_]+\.(US|HK|SH|SZ)$`

#### 2.5 类型转换缺乏异常处理
- **文件**: `scripts/collect.py` 第 74-77 行
- **问题**: `float()` 转换可能抛出 `ValueError`，导致采集流程中断
- **修复**: 使用 `safe_float()` 包装

#### 2.6 全局变量无线程保护
- **文件**: `scripts/collect_ws.py` 第 26-28 行
- **问题**: `running` 和 `_prev_close_cache` 无锁保护
- **修复**: 使用 `threading.Event()` 和 `threading.Lock()`

#### 2.7 文件写入竞态条件
- **文件**: `scripts/collect_ws.py` 第 152-163 行
- **问题**: 文件名使用秒级时间戳，同一秒内多条推送会覆盖
- **修复**: 文件名添加毫秒或 UUID

#### 2.8 SQLite 并发写入无超时
- **文件**: `scripts/compute.py` 第 354-367 行
- **问题**: 多个 cron 任务同时触发时可能 `database is locked`
- **修复**: `sqlite3.connect(get_db_path(), timeout=30)`

#### 2.9 过于宽泛的异常捕获
- **文件**: `scripts/alert.py` 第 147 行, `scripts/llm.py` 第 132 行
- **问题**: `except Exception` 会掩盖真正的 bug
- **修复**: 捕获具体异常，记录 traceback

#### 2.10 WebSocket 无自动重连
- **文件**: `scripts/collect_ws.py`
- **问题**: WebSocket 断开后进程静默退出，依赖 cron 重启
- **修复**: 添加重连循环和指数退避

### ⚪ P2 低风险

#### 2.11 日志文件权限过宽
- **文件**: `scripts/collect_ws.py` 第 245 行
- **问题**: 权限 `0o644`，所有用户可读
- **修复**: 改为 `0o640` 或 `0o600`

#### 2.12 subprocess 超时不一致
- **文件**: `scripts/start_all.py`, `scripts/stop_all.py`
- **问题**: `crontab` 命令无超时设置，可能永久阻塞
- **修复**: 添加 `timeout=30`

#### 2.13 PID 文件 TOCTOU 竞态
- **文件**: `scripts/start_all.py` 第 29-35 行
- **问题**: check-then-act 模式存在竞态
- **修复**: 使用文件锁或 `fcntl.flock()`

---

## 三、编码质量问题

### 🔴 P0 严重问题

#### 3.1 `alert.py` 中函数重复定义
- **问题**: `scan_and_alert()` 定义两次，`generate_minimax_prompt()` 和 `generate_llm_prompt()` 内容几乎相同
- **修复**: 合并重复函数

#### 3.2 `format_alert_message()` 与 `format_full_alert_message()` 高度重复
- **文件**: `scripts/alert.py` 第 106-124 行和第 232-254 行
- **修复**: 合并为一个函数，LLM 分析作为可选参数

#### 3.3 LLM prompt 模板重复 3 次
- **文件**: `scripts/alert.py` 第 177-192 行和第 214-229 行, `scripts/cli.py` 第 38-48 行
- **修复**: 提取为常量或模板函数

### 🟡 P1 中等问题

#### 3.4 `compute.py` 中重复读取同一快照文件
- **问题**: `calc_intraday_ratio()` 读取所有快照（100+ 个文件），每个都是独立的 `open()` + `json.load()`
- **修复**: 批量读取并缓存

#### 3.5 `load_config()` 无缓存
- **问题**: 每次调用都重新读取 YAML 文件，`scan_and_alert()` 流程中至少调用 3 次
- **修复**: 使用 `functools.lru_cache`

#### 3.6 快照文件粒度过细
- **问题**: 每秒一个文件，一天交易 6 小时 = 21,600 个文件/标的/天
- **修复**: 改用 SQLite 或每标的一天一个 JSONL 文件

#### 3.7 数据库操作每次重建连接
- **文件**: `scripts/compute.py` 第 350-368 行
- **问题**: `save_ratio()` 每次调用都 `init_db()` + `connect()` + `close()`
- **修复**: 使用连接池或持久连接

#### 3.8 `collect_ws.py` 中 `writer_thread()` 未使用
- **文件**: `scripts/collect_ws.py` 第 138-148 行
- **问题**: 已定义但从未启动，是死代码
- **修复**: 删除该函数

#### 3.9 `llm.py` 中 `switch_profile()` 是无意义别名
- **文件**: `scripts/llm.py`
- **问题**: `switch_profile()` 只是调用 `switch_llm()`，增加不必要的间接层
- **修复**: 删除 `switch_profile()`

#### 3.10 错误处理风格不一致
- **问题**: 有的地方 `print(..., file=sys.stderr)`，有的地方 `print(...)`，无统一日志框架
- **修复**: 引入 `logging` 模块

#### 3.11 watchlist 遍历模式重复 5 次
- **文件**: collect.py, collect_ws.py, compute.py, alert.py, cli.py
- **问题**: `for market in ["us", "hk", "cn"]: ...` 重复出现
- **修复**: 提取为 `get_all_tickers()` 函数

### ⚪ P2 低等问题

#### 3.12 `import yaml` 位于函数内部
- **文件**: 所有包含 `load_config()` 的文件
- **问题**: 不符合 PEP 8 惯例
- **修复**: 移到文件顶部

#### 3.13 类型注解不完整
- **问题**: `load_config()` 返回 `dict`，应使用 `TypedDict`
- **修复**: 定义 `Config` 类型

#### 3.14 硬编码路径
- **文件**: `scripts/start_all.py` 第 72-74 行
- **问题**: `/Users/shinji/project-x/volume-ratio/` 硬编码
- **修复**: 使用 `Path(__file__).parent.parent` 动态获取

#### 3.15 中文/英文混合命名不一致
- **问题**: 函数名英文，信号字符串中文，注释中英文混杂
- **建议**: 统一风格，信号可考虑使用英文枚举

#### 3.16 缺乏 `__all__` 导出控制
- **文件**: `scripts/__init__.py`
- **问题**: 仅一行注释，未定义公共接口
- **修复**: 添加 `__all__` 列表

---

## 四、数据流问题

### 4.1 数据库只写不读
- **文件**: `scripts/compute.py`
- **问题**: `volume_ratios` 表有 3044 条记录，但没有任何脚本读取；`daily_summary` 表完全空置
- **影响**: 浪费存储，历史数据未被利用
- **修复**: 要么移除数据库，要么实现历史查询功能

### 4.2 数据冗余
- **问题**: 同一数据同时存在于文件系统（snapshots）和数据库（ratios.db）中
- **影响**: 无一致性保证
- **修复**: 统一数据源

### 4.3 时间戳精度不足
- **问题**: 快照文件名使用秒级时间戳，同一秒内多条推送会覆盖
- **修复**: 添加毫秒或序列号

---

## 五、测试覆盖

### 🔴 严重缺失

#### 5.1 零测试代码
- **问题**: 项目中没有任何测试文件，1869 行代码测试覆盖率 0%
- **影响**: 核心算法无验证，回归风险高
- **修复**: 至少覆盖以下函数：
  - `calc_volume_ratio()` — 量比计算核心
  - `calc_intraday_ratio()` — 日内滚动量比
  - `detect_signals()` — 信号检测
  - `get_signal()` — 信号分类
  - `get_signal_detail()` — 信号详情

#### 5.2 无 mock/fixture 机制
- **问题**: 所有函数直接依赖文件系统和配置文件
- **修复**: 使用 `pytest` + `tmp_path` fixture + mock

---

## 六、综合优先级矩阵

| 优先级 | 编号 | 问题 | 类别 | 严重度 |
|:--:|:--:|:--|:--:|:--:|
| **P0** | 1.1 | `scan_and_alert()` 重复定义 | 框架 | 🔴 |
| **P0** | 1.2 | A 股简报 `.CN` BUG | 框架 | 🔴 |
| **P0** | 1.3 | API Key 明文存储 | 安全 | 🔴 |
| **P0** | 2.1 | `--test` 模式泄露 API Key | 安全 | 🔴 |
| **P0** | 3.1 | 函数重复定义 | 质量 | 🔴 |
| **P1** | 1.4 | `load_config()` 重复 6 次 | 框架 | 🟡 |
| **P1** | 1.5 | `get_market()` 重复 3 次 | 框架 | 🟡 |
| **P1** | 1.6 | 快照文件爆炸 | 框架 | 🔴 |
| **P1** | 1.7 | `cli.py` 硬编码 LLM | 框架 | 🟡 |
| **P1** | 2.3 | 路径遍历风险 | 安全 | 🟡 |
| **P1** | 2.4 | CLI 参数无验证 | 安全 | 🟡 |
| **P1** | 2.5 | 类型转换无异常处理 | 安全 | 🟡 |
| **P1** | 2.6 | 全局变量无线程保护 | 安全 | 🟡 |
| **P1** | 2.7 | 文件写入竞态 | 安全 | 🟡 |
| **P1** | 2.8 | SQLite 并发无超时 | 安全 | 🟡 |
| **P1** | 2.9 | 过于宽泛的异常捕获 | 安全 | 🟡 |
| **P1** | 2.10 | WebSocket 无自动重连 | 安全 | 🟡 |
| **P1** | 3.4 | 重复读取快照文件 | 质量 | 🟡 |
| **P1** | 3.5 | `load_config()` 无缓存 | 质量 | 🟡 |
| **P1** | 3.6 | 快照粒度过细 | 质量 | 🟡 |
| **P1** | 3.7 | 数据库连接每次重建 | 质量 | 🟡 |
| **P1** | 4.1 | 数据库只写不读 | 数据流 | 🟡 |
| **P1** | 5.1 | 零测试代码 | 测试 | 🔴 |
| **P2** | 1.8 | `sys.path.insert` hack | 框架 | ⚪ |
| **P2** | 1.9 | 双层 fork daemon 重复 | 框架 | ⚪ |
| **P2** | 1.10 | 无日志框架 | 框架 | ⚪ |
| **P2** | 2.11 | 日志文件权限过宽 | 安全 | ⚪ |
| **P2** | 2.12 | subprocess 超时不一致 | 安全 | ⚪ |
| **P2** | 2.13 | PID 文件 TOCTOU | 安全 | ⚪ |
| **P2** | 3.8 | `writer_thread()` 死代码 | 质量 | ⚪ |
| **P2** | 3.9 | `switch_profile()` 无意义别名 | 质量 | ⚪ |
| **P2** | 3.10 | 错误处理风格不一致 | 质量 | ⚪ |
| **P2** | 3.11 | watchlist 遍历重复 5 次 | 质量 | ⚪ |
| **P2** | 3.12 | `import yaml` 位置不当 | 质量 | ⚪ |
| **P2** | 3.13 | 类型注解不完整 | 质量 | ⚪ |
| **P2** | 3.14 | 硬编码路径 | 质量 | ⚪ |
| **P2** | 3.15 | 中英文命名不一致 | 质量 | ⚪ |
| **P2** | 3.16 | 缺乏 `__all__` 导出 | 质量 | ⚪ |

---

## 七、推荐修复路径

### Phase 1 — 止血（1-2 小时）

1. 修复 `alert.py` 中 `scan_and_alert()` 重复定义
2. 修复 A 股简报 `.CN` → `.SH`/`.SZ` BUG
3. API Key 迁移至环境变量
4. 修复 `--test` 模式的密钥泄露
5. `cli.py` 改用 `llm.call_llm()`

### Phase 2 — 抽取公共模块（2-3 小时）

1. 创建 `scripts/core/config.py`（单一 `load_config` + 缓存）
2. 创建 `scripts/core/market.py`（`get_market` + 交易时间判断）
3. 消除 `sys.path.insert`，使用正式包导入

### Phase 3 — 数据层重构（3-4 小时）

1. 快照存储从文件系统迁移至 SQLite（或 JSONL 单文件追加）
2. 添加快照清理策略（自动删除 7 天前数据）
3. 统一数据模型，消除冗余

### Phase 4 — 进程管理升级（2-3 小时）

1. 使用 `supervisord` 替代手动 fork + cron
2. 添加 WebSocket 重连和心跳机制
3. 引入 `logging` 模块 + 日志轮转

### Phase 5 — 测试覆盖（3-4 小时）

1. 添加 `pytest` 测试框架
2. 至少覆盖核心算法函数
3. 使用 mock 隔离外部依赖

---

## 八、统计摘要

| 类别 | P0 | P1 | P2 | 合计 |
|:--|:--:|:--:|:--:|:--:|
| 框架设计 | 3 | 4 | 3 | 10 |
| 编码安全 | 2 | 8 | 3 | 13 |
| 编码质量 | 1 | 8 | 9 | 18 |
| 数据流 | 0 | 1 | 0 | 1 |
| 测试覆盖 | 0 | 1 | 0 | 1 |
| **合计** | **6** | **22** | **15** | **43** |

---

**结论**: 项目存在 6 个严重问题需要立即修复，22 个中等问题建议在 1 周内解决，15 个低等问题可纳入后续迭代。测试覆盖率为 0%，建议优先补充核心算法的单元测试。
