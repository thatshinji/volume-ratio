# Hermes Agent 飞书机器人集成方案

## 概述

在现有量比监控系统的基础上，引入第二个飞书机器人——**Hermes Agent**，用于自然语言对话和 AI 分析。

两个机器人分工：

| 机器人 | 职责 |
|:------|:------|
| 量比机器人（已有） | /start /stop /status /scan /signals /add /remove 等监控指令 |
| Hermes 机器人（新增） | 交易分析、行情查询、策略讨论、自由对话 |

两者互不干扰，可在同一个飞书群里共存。

---

## 架构

```
飞书
├── 量比机器人（已有）   ← 量比系统 feishu_bot.py
│   └── 指令: /start /status /scan /signals ...
│
└── Hermes 机器人（新增） ← Hermes Gateway
    └── 自然语言对话、交易分析、工具调用
```

Hermes 通过 Gateway 直接连接飞书，拥有完整的交互能力：
- 显示工具调用过程（查数据、搜新闻等）
- 保持对话上下文
- 发送图片/文件
- 支持追问和确认

---

## 前置条件

- 已安装 Hermes Agent（`hermes --version` 确认）
- 已有飞书企业自建应用权限
- 量比系统正常运行

---

## 步骤一：创建飞书机器人应用

### 1.1 在飞书开放平台创建应用

1. 打开 https://open.feishu.cn/app
2. 点击「创建企业自建应用」
3. 填写：
   - 名称：`Hermes Agent`（或你喜欢的名字）
   - 描述：AI 交易分析助手
4. 创建完成后，进入应用

### 1.2 获取凭证

1. 左侧菜单 → **凭证与基础信息**
2. 记下 **App ID** 和 **App Secret**

### 1.3 配置权限

左侧菜单 → **权限管理**，开启以下权限：

**机器人：**
- `im:message` 获取用户发送的消息
- `im:message.send_as_bot` 以机器人身份发送消息
- `im:resource` 获取消息中的图片/文件资源

**搜索：**
- `drive:drive` 获取云文档（可选）

### 1.4 配置事件

左侧菜单 → **事件与回调** → 添加事件：

- `im.message.receive_v1` — 接收用户消息

### 1.5 发布应用

1. 左侧菜单 → **版本管理与发布**
2. 创建版本 → 填写说明 → 保存
3. 点击「申请发布」
4. 在企业管理员审核通过后，应用上线

### 1.6 添加机器人

在飞书搜索刚创建的应用名称，添加到需要对话的群聊或个人会话。

---

## 步骤二：创建 Hermes Profile

为量比项目创建一个独立的 Hermes 配置，与主配置隔离。

```bash
hermes profile create volume-ratio
```

这会在 `~/.hermes/profiles/volume-ratio/` 下创建独立的 config.yaml。

---

## 步骤三：配置飞书 Gateway

编辑 profile 的配置文件：

```bash
hermes config edit --profile volume-ratio
```

添加飞书平台配置：

```yaml
gateway:
  platforms:
    feishu:
      enabled: true
      app_id: "cli_xxxxxxxxxxxxxxx"          # 替换为步骤一获取的 App ID
      app_secret: "xxxxxxxxxxxxxxxxxxxxxxx"   # 替换为步骤一获取的 App Secret
      # 可选：默认发送到的群 chat_id
      # 可以从群链接或飞书 API 获取
      home_chat_id: "oc_xxxxxxxxxxxxxxxx"

  # 可选：同时启动 API Server，供量比系统调用
  # api_server:
  #   enabled: true
  #   port: 9090
```

---

## 步骤四：配置模型

推荐使用与量比系统相同的模型（MiniMax 或小米）：

```bash
hermes model --profile volume-ratio
```

选择 MiniMax 或小米模型，按提示配置 API Key。

---

## 步骤五：安装必要 Skills

```bash
# 以 volume-ratio profile 启动时加载投资相关技能
hermes skills install trading-plan-parallel
hermes skills install longbridge
hermes skills install futuapi
```

这些 skill 会按需加载到对话中，Hermes 可以直接调用它们分析股票。

---

## 步骤六：启动 Gateway

```bash
hermes gateway run --profile volume-ratio
```

首次启动后验证：
1. 在飞书向 Hermes 机器人发消息
2. 应收到回复

若需后台运行：

```bash
hermes gateway install --profile volume-ratio
hermes gateway start --profile volume-ratio
```

查看状态：

```bash
hermes gateway status --profile volume-ratio
```

---

## 使用

### 群聊模式

将量比机器人和 Hermes 机器人加入同一个飞书群：

```
[群聊]
├── 量比机器人: 收到信号时推送
└── 用户 @Hermes 机器人: 分析一下 CLF 今天的放量
```

### 私聊模式

直接给 Hermes 机器人发消息，与一对一对话无异。

### 示例对话

```
用户: 分析一下 CLF 今晚能不能进
Hermes: [显示查行情、查新闻过程...]
        趋势判定 / 回调状态 / 信号状态 / 结论 / 入场条件

用户: 那洛阳钼业呢？
Hermes: [能记住上文，继续分析]
```

---

## 后台服务管理

```bash
# 查看服务状态
hermes gateway status --profile volume-ratio

# 重启
hermes gateway restart --profile volume-ratio

# 查看日志
tail -f ~/.hermes/profiles/volume-ratio/logs/gateway.log

# 停止
hermes gateway stop --profile volume-ratio
```

---

## 与量比系统的联动（可选增强）

如果想让两个机器人互相感知（例如量比检测到信号后，让 Hermes 分析），可以在 `feishu_bot.py` 中增加配置：

```yaml
# config.yaml
hermes:
  enabled: true                     # 启用 Hermes 联动（可选）
  chat_id: "oc_xxxxxxxxxxxx"        # Hermes 机器人所在群聊的 chat_id
```

然后在 `feishu_bot.py` 发送信号时，也转发一份到 Hermes 机器人所在的群，形成「信号触发→AI 分析→推送结论」的闭环。这是后续增强，非必须。

---

## 故障排查

| 问题 | 检查项 |
|:----|:-------|
| 机器人无响应 | `hermes gateway status --profile volume-ratio` 是否运行中 |
| 收不到消息 | 飞书开放平台 → 事件配置 → im.message.receive_v1 是否添加 |
| 权限错误 | 飞书开放平台 → 权限管理 → 机器人权限是否已开通 |
| 连接失败 | `tail -f ~/.hermes/logs/gateway.log` 查看详细日志 |
| Secret 错误 | 确认 app_id 和 app_secret 与飞书开放平台一致 |
