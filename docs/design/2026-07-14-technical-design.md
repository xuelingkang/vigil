# Vigil — Hermes post_llm_call 通知插件技术设计文档

> **基于 [hermes-notification](https://github.com/itgoyo/hermes-notification) 改造，适配 tmux 工作流**

| 文档信息 | 内容 |
|---------|------|
| 插件名称 | `vigil` |
| 触发点 | `post_llm_call` |
| 目标环境 | macOS (Hermes CLI + tmux) |

---

## 一、概述

### 1.1 问题背景

在多 tmux session 工作流中，用户频繁在多个 pane 之间切换。Hermes 输出完成后无主动信号，用户需要手动切回 Hermes 所在 pane 检查状态，打断编码节奏。

### 1.2 解决方案

在 Hermes 每次完成 LLM 回复后（`post_llm_call` hook），通过 macOS 原生通知系统告知用户。通知标题根据运行环境动态生成：

- **tmux 内**：显示 `session:window.pane`（如 `work:1.0`），精确标识 Hermes 所在位置
- **tmux 外**：显示 `Hermes`，简洁通用

通知按 `session:window.pane` 分组（group），多个 pane 的通知互不影响、独立显示。

### 1.3 设计原则

- **最小依赖**：优先 `terminal-notifier`（需安装），缺失时降级为 `osascript`
- **无点击行为**：通知为纯信息展示，不绑定 `-execute` 动作
- **按 pane 分组**：每个 tmux pane 独立 group，不同 pane 通知互不替换
- **非阻塞**：通知发送在后台线程执行
- **零侵入**：纯插件，不修改 Hermes 核心代码

---

## 二、技术方案

### 2.1 架构概览

```
Hermes CLI 完成 LLM 回复
        │
        ▼
post_llm_call hook 触发 (_on_post_llm_call)
        │
        ▼
加载配置 (~/.hermes/plugins/vigil/config.json)
        │
        ▼
构建通知标题 + group key
  ├── tmux 内 → tmux display-message -p #S:#W.#P → "session:window.pane"
  │              group = "vigil:session:window:pane"（连字符避免粘连）
  └── tmux 外 → title = "Hermes", 不设置 group
        │
        │
        ▼
截取响应正文 (body_length)
        │
        ▼
后台线程发送通知
  ├── terminal-notifier（主路径，带 -group）
  └── osascript display notification（降级路径，无 group 支持）
```

### 2.2 通知标题与分组

```python
def _get_title_and_group() -> tuple[str, str | None]:
    """返回 (title, group)。title 显示在通知上，group 用于通知替换/分组。tmux 外返回 group=None。"""
    if os.environ.get("TMUX"):
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-p", "#S:#W.#P"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                tmux_info = result.stdout.strip()
                if tmux_info:
                    group_key = tmux_info.replace(":", "-").replace(".", "-")
                    return tmux_info, f"vigil:{group_key}"
        except Exception:
            pass
        return "tmux", None
    return "Hermes", None
```

**分组规则**：

| 环境 | title | group | 完整标题显示 |
|------|-------|-------|-------------|
| tmux session `work` window `1` pane `0` | `work:1.0` | `vigil:work-1-0` | `vigil:work:1.0` |
| tmux 内（tmux 命令失败/无输出） | `tmux` | 无 group | `vigil:tmux` |
| tmux 外 | `Hermes` | 无 group（每次新通知独立显示） | `vigil:Hermes` |

注：hook 内部会在 title 前拼接 `vigil:` 前缀作为最终显示标题（`_on_post_llm_call` 中：`full_title = f"vigil:{title}"`），标识通知来源。

- `:` 和 `.` 替换为 `-` 避免 terminal-notifier group 解析歧义
- 同一 pane 内新通知替换旧通知（同一个 group），不同 pane 独立显示
- tmux 外不设置 group，每条通知独立显示、不替换

### 2.3 通知发送流程

```python
def _send_notification(title, message, sound, group):
    tn = _tn_path()
    if tn:
        cmd = [
            tn,
            "-title",   title,
            "-message", message,
        ]
        if group:
            cmd += ["-group", group]
        if sound:
            cmd += ["-sound", sound]
        subprocess.run(cmd, timeout=5, capture_output=True)
        return
    # 降级：osascript (无 group，无点击)
    _send_osascript_notification(title, message, sound)
```

- tmux 内：`-group vigil:session-window-pane`，同一 pane 通知替换旧通知
- tmux 外：不传 `-group`，每条通知独立显示

### 2.5 消息正文截取

移除原版 `min_response_length` 过滤，`_on_post_llm_call` 内拼接 `vigil:` 标题前缀后发送。

> **注意**：Hermes 源码 `agent/turn_finalizer.py:390` 中 `post_llm_call` hook 的触发条件是 `if final_response and not interrupted:`，即 `final_response` 为空字符串时 hook 不会触发。因此 Vigil 只能对有实际内容的回复发送通知——这是 Hermes 框架的限制，非插件责任。

```python
def _on_post_llm_call(...):
    ...
    full_title = f"vigil:{title}"   # 标识通知来源

    # 截取通知正文
    body = assistant_response.strip()
    if body.startswith("```"):
        nl = body.find("\n")
        body = body[nl + 1:] if nl != -1 else body
    body = body[:body_length]
    if len(assistant_response.strip()) > body_length:
        body += "…"

    _notify_async(full_title, body, sound, group)
```

另外注意 `body_length = 0` 的特殊情况：`body[:0]` 得到空字符串 `""`，但之后判断成立会追加 `"…"`。

### 2.6 配置加载

```python
DEFAULTS = {
    "enabled": True,
    "sound": "Glass",
    "body_length": 80,
}
```

配置文件路径：`~/.hermes/plugins/vigil/config.json`

---

## 三、配置说明

### 3.1 配置文件

`~/.hermes/plugins/vigil/config.json`：

```json
{
    "enabled": true,
    "sound": "Glass",
    "body_length": 80
}
```

### 3.2 配置项明细

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `true` | 是否启用插件。`false` 时跳过注册，不加载 hook |
| `sound` | string | `"Glass"` | macOS 通知声音名称。空字符串或 null 表示静音。可用值：Basso, Blow, Bottle, Frog, Funk, Glass, Hero, Morse, Ping, Pop, Purr, Sosumi, Submarine, Tink |
| `body_length` | integer | `80` | 通知正文截取的最大字符数。设为 0 时正文为 `"…"` |

---

## 四、项目结构

```
~/.hermes/plugins/vigil/
├── __init__.py          # 主逻辑：hook 注册、通知发送、配置加载
├── plugin.yaml          # 插件元数据（Hermes 通过此文件发现插件）
├── config.example.json  # 配置示例（用户复制为 config.json）
└── config.json          # 用户配置（运行时可选创建）
```

### 5.1 plugin.yaml

```yaml
name: vigil
version: 1.0.0
description: "macOS system notification when Hermes finishes a response. Shows tmux session:window.pane in title. Notifications grouped by pane. Requires terminal-notifier (brew install terminal-notifier)."
author: vigil
requires_env: []
hooks:
  - post_llm_call
```

---

## 五、关键代码模块说明

### 5.1 `register(ctx)` — 插件入口

```python
def register(ctx) -> None:
    cfg = _load_config()
    if not cfg.get("enabled", True):
        logger.info("vigil: disabled, skipping")
        return
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    logger.info("vigil: registered")
```

### 5.2 `_on_post_llm_call(...)` — Hook 处理函数

接收 `session_id`, `user_message`, `assistant_response`, `conversation_history`, `model`, `platform` 等参数。

处理流程：
1. load config → `_load_config()`
2. check `enabled`
3. build title + group → `_get_title_and_group()`
4. truncate body → `body_length`
5. spawn background thread → `_notify_async()`

### 5.3 `_send_notification(...)` — 核心通知发送

优先使用 `terminal-notifier`，降级使用 `osascript`。group 参数由 `_get_title_and_group()` 返回。

### 5.4 `_get_title_and_group()` — 标题与分组生成

tmux `display-message -p #S:#W.#P` → 解析 stdout → 返回 `(title, group)` 元组。tmux 外返回 `("Hermes", None)`，不设置 group。

---

## 七、边界情况

| 场景 | 行为 | 说明 |
|------|------|------|
| tmux 未安装或不在 tmux 内 | 标题显示 `"vigil:Hermes"`, 不设置 group | `TMUX` 环境变量不存在时直接返回 `"Hermes"` |
| TMUX 变量存在但 tmux 命令失败 | 标题显示 `"vigil:tmux"`, 不设置 group | 兜底分支，保留 tmux 提示 |
| terminal-notifier 未安装 | 自动降级为 osascript 通知 | 通知正常弹出，**无 group 支持，多 pane 时桌面堆叠**；日志记录降级信息 |
| 响应正文为空字符串 | hook 不触发，不发送通知 | Hermes 框架 `if final_response and not interrupted:` 保证空响应不触发 `post_llm_call` |
| 响应正文有代码块标记（```） | 跳过开头的 ` ``` ` 标记行 | 使通知正文更可读，不展示语言标记 |
| 配置不存在或格式错误 | 使用全默认值 | `_load_config()` 捕获 JSON 解析异常，仅 log warning |
| `body_length = 0` | 正文为 `"…"` | `body[:0]` 得空串，但回复非空时 `len(assistant_response.strip()) > 0` 成立，追加 `"…"` |
| tmux session name 含特殊字符 | 直接透传至通知标题 | terminal-notifier 对标题字符无限制，group 中特殊字符已替换为 `-` |
| 通知发送超时 | 捕获 `subprocess.TimeoutExpired`，不影响 Hermes | 后台线程内 try/except，不会抛到 hook 调用方 |
| 多个 tmux pane 同时有 Hermes 实例 | 每个 pane 独立通知，互不替换 | group 按 `session:window:pane` 区分 |

---

## 八、错误处理与日志

### 8.1 日志

所有日志使用 `logging.getLogger(__name__)`，以 `vigil:` 前缀输出：

| 日志级别 | 场景 |
|---------|------|
| INFO | 插件注册成功 |
| WARNING | 配置加载失败（JSON 解析错误） |
| DEBUG | terminal-notifier exit code / stderr |
| DEBUG | 降级到 osascript |
| DEBUG | terminal-notifier 未找到 |

### 8.2 异常安全

- 所有 subprocess 调用设置 `timeout`，防止命令卡死
- 后台线程异常不会传播到主线程
- 配置加载异常不影响 Hermes 正常运行

---

## 九、安装与使用

```bash
# 1. 安装 terminal-notifier（推荐，否则降级为 osascript 无 group 支持）
brew install terminal-notifier

# 2. 安装插件
mkdir -p ~/.hermes/plugins/vigil/
cp __init__.py    ~/.hermes/plugins/vigil/
cp plugin.yaml    ~/.hermes/plugins/vigil/

# 3. 配置（可选）
cat > ~/.hermes/plugins/vigil/config.json <<EOF
{
    "enabled": true,
    "sound": "Glass",
    "body_length": 80
}
EOF

# 4. 重启 Hermes
# 日志中应出现：vigil: registered
```

---

## 十、附录：关键序列图

```
User                  Hermes CLI              Vigil Plugin          macOS
  │                       │                      │                    │
  │  发送消息              │                      │                    │
  │──────────────────────▶│                      │                    │
  │                       │                      │                    │
  │                       │  调用 LLM            │                    │
  │                       │──── ... ────▶        │                    │
  │                       │                      │                    │
  │  切到其他 pane         │                      │                    │
  │◀──────────────────────│                      │                    │
  │                       │                      │                    │
  │                       │  LLM 返回            │                    │
  │                       │◀──── ... ────        │                    │
  │                       │                      │                    │
  │                       │  post_llm_call       │                    │
  │                       │─────────────────────▶│                    │
  │                       │                      │                    │
  │                       │                      │── tmux 查标题+group│
  │                       │                      │◀─ S:W.P ─────────  │
  │                       │                      │                    │
  │                       │                      │── terminal-notifier│
  │                       │                      │   (group=S-W-P)    │
  │                       │                      │── (or osascript)   │
  │                       │                      │──────────────────▶ │
  │                       │                      │                    │
  │  收到通知              │                      │                    │
  │◀───────────────────────────────────────────────────────────────  │
  │                       │                      │                    │
  │  点击通知 → 无行为      │                      │                    │
  │───────────────────────────────────────────────────────────────▶  │
  │                       │                      │                    │
```
