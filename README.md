# Vigil — Hermes 通知插件

> Vigil，来自拉丁语 *vigilia*（守夜、值夜）。深夜的沉默守夜人。

Vigil 是一款 [Hermes](https://github.com/NousResearch/hermes-agent) 插件，在 Hermes 完成 LLM 回复后，通过 macOS 原生通知系统告知用户。专为多 tmux session 工作流设计。基于 [hermes-notification](https://github.com/itgoyo/hermes-notification) 改造。

## 功能

- **LLM 回复完成通知**：Hermes 每次生成回复后自动弹出 macOS 通知
- **通知标题可配置**：通知标题通过配置项 `title` 自定义（默认 `"Vigil"`），副标题动态显示 tmux `session:window.pane`（如 `work:1.0`）或 `Hermes`（tmux 外），精确定位 Hermes 所在位置
- **按 pane 分组**：同一 tmux pane 内的后续通知自动替换前一条，多个 pane 的通知独立显示、互不干扰
- **点击移除通知**：tmux 内点击通知正文执行 `terminal-notifier -remove <group>`，关闭当前 pane 通知并清理通知中心；tmux 外无此行为
- **异步非阻塞**：通知发送在后台线程执行，不影响 Hermes 主流程
- **通知正文截取**：自动截取过长正文，跳过开头的代码块标记行（`` ``` ``），正文最大长度可配置
- **智能降级**：优先使用 `terminal-notifier`（支持分组），未安装时自动降级为 `osascript`
- **可配置**：支持启用/禁用、通知声音、正文长度

## 安装

### 前置依赖

推荐安装 `terminal-notifier`（支持通知分组，多个 pane 时体验更佳）：

```bash
brew install terminal-notifier
```

未安装时自动降级为 `osascript`，通知正常弹出但**无分组支持**，多 pane 时通知会堆叠显示。

### 安装插件

```bash
# 创建插件目录
mkdir -p ~/.hermes/plugins/vigil/

# 复制插件文件
cp __init__.py  ~/.hermes/plugins/vigil/
cp plugin.yaml  ~/.hermes/plugins/vigil/

# 复制配置模板并按需修改
cp config.example.json ~/.hermes/plugins/vigil/config.json
```

### 启用插件

编辑 `~/.hermes/config.yaml`，在末尾添加：

```yaml
plugins:
  enabled:
    - vigil
```

重启 Hermes 后日志中应出现：

```
vigil: registered
```

## 配置

配置文件位于 `~/.hermes/plugins/vigil/config.json`：

```json
{
    "enabled": true,
    "title": "Vigil",
    "sound": "default",
    "body_length": 80
}
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | boolean | `true` | 是否启用插件。设为 `false` 时跳过注册，不加载 hook |
| `title` | string | `"Vigil"` | 通知标题。标识通知来源插件 |
| `sound` | string | `"default"` | macOS 通知声音。推荐 `"default"`（系统提示音）。如需其他声音，从 系统设置 → 声音 → 提示音 中查看名称，填入即可。空字符串或 `null` 表示静音 |
| `body_length` | integer | `80` | 通知正文截取的最大字符数。设为 `0` 时正文为 `"…"` |

## 使用说明

安装配置完成后无需任何额外操作。每次 Hermes 完成 LLM 回复后，系统会自动弹出通知：

- **tmux 内**：通知标题为配置项 `title`（默认 `"Vigil"`），副标题为 `session:window.pane`（如 `work:1.0`），同一 pane 的新通知替换旧通知；点击通知移除该 pane 的所有通知
- **tmux 外**：通知标题为配置项 `title`（默认 `"Vigil"`），副标题为 `Hermes`，每条通知独立显示、不会自动移除

> **注意**：Hermes 框架在 `final_response` 为空字符串或回复被中断时不会触发 `post_llm_call` hook，因此 Vigil 只能对有实际内容的回复发送通知——这是 Hermes 框架的设计行为，非插件问题。

## 注意事项

- **macOS only**：依赖 `terminal-notifier` 或 `osascript`，仅支持 macOS
- **点击通知移除**：tmux 内点击通知执行 `terminal-notifier -remove <group>`，关闭并清理通知中心；tmux 外点击通知仅关闭弹窗，不清理通知中心
- **tmux 依赖**：tmux 位置标识功能需要 `tmux` 命令可用且进程在 tmux 环境中运行
- **分组依赖**：通知按 pane 分组需 `terminal-notifier` 支持；降级为 `osascript` 时所有通知独立显示
- **日志前缀**：所有插件日志以 `vigil:` 前缀输出，可在 Hermes 日志中查看

## 项目结构

```
~/.hermes/plugins/vigil/
├── __init__.py          # 主逻辑：hook 注册、通知发送、配置加载
├── plugin.yaml          # 插件元数据（Hermes 通过此文件发现插件）
├── config.example.json  # 配置示例
└── config.json          # 用户配置（可选创建）
```
