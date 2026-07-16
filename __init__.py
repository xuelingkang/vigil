"""
vigil — Hermes post_llm_call 通知插件
======================================
基于 hermes-notification 改造，适配 tmux 工作流。
每次 Hermes 回复后发送 macOS 系统通知，按 tmux session:window.pane 分组。

关键行为：
- tmux 内：标题显示 session:window.pane，按 pane 分组，新通知替换旧通知
- tmux 外：标题显示 "Hermes"，不分组，每条通知独立显示
- 优先使用 terminal-notifier（支持 group），降级为 osascript
- 异步后台线程发送，不阻塞 Hermes
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default config
# ---------------------------------------------------------------------------

DEFAULTS = {
    "enabled": True,
    "title": "Vigil",
    "sound": "default",
    "body_length": 80,
}

_PLUGIN_DIR = Path.home() / ".hermes" / "plugins" / "vigil"


def _load_config() -> dict:
    config_path = _PLUGIN_DIR / "config.json"
    cfg = dict(DEFAULTS)
    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg.update(json.load(f))
        except Exception as e:
            logger.warning("vigil: failed to load config: %s", e)
    return cfg


# ---------------------------------------------------------------------------
# terminal-notifier path
# ---------------------------------------------------------------------------


def _tn_path() -> str | None:
    path = shutil.which("terminal-notifier")
    if path:
        return path
    for p in [
        "/opt/homebrew/bin/terminal-notifier",
        "/usr/local/bin/terminal-notifier",
    ]:
        if os.path.exists(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Title & group (tmux-aware)
# ---------------------------------------------------------------------------


def _get_title_and_group() -> tuple[str, str | None]:
    """返回 (subtitle, group)。subtitle 显示在通知副标题上，group 用于通知替换/分组。tmux 外返回 group=None。"""
    tmux_pane = os.environ.get("TMUX_PANE")
    if os.environ.get("TMUX") and tmux_pane:
        try:
            result = subprocess.run(
                ["tmux", "display-message", "-t", tmux_pane, "-p", "#S:#W.#P"],
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


# ---------------------------------------------------------------------------
# osascript fallback notification
# ---------------------------------------------------------------------------


def _send_osascript_notification(title: str, message: str, sound: str) -> None:
    safe_title   = title.replace("\\", "\\\\").replace('"', '\\"')
    safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    if sound:
        sound_path = f"/System/Library/Sounds/{sound}.aiff"
        if os.path.exists(sound_path):
            subprocess.Popen(
                ["afplay", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
    except Exception as e:
        logger.debug("vigil: osascript failed: %s", e)


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------


def _send_notification(
    title: str,
    subtitle: str,
    message: str,
    sound: str,
    group: str | None,
) -> None:
    tn = _tn_path()

    if tn:
        cmd = [
            tn,
            "-title",   title,
            "-subtitle", subtitle,
            "-message", message,
        ]
        if group:
            cmd += ["-group", group]
            # 点击通知移除当前组（关闭通知并清理通知中心）
            cmd += ["-execute", f"{tn} -remove {group}"]
        else:
            # tmux 外无分组，点击通知即关闭
            cmd += ["-execute", "true"]
        if sound:
            cmd += ["-sound", sound]

        try:
            result = subprocess.run(cmd, timeout=5, capture_output=True)
            logger.debug(
                "vigil: terminal-notifier exit=%d stderr=%s",
                result.returncode, result.stderr.decode(errors="replace"),
            )
            return
        except Exception as e:
            logger.debug("vigil: terminal-notifier failed: %s", e)

    # Fallback：osascript（无 group 支持）
    _send_osascript_notification(title, message, sound)


def _notify_async(
    title: str,
    subtitle: str,
    message: str,
    sound: str,
    group: str | None,
) -> None:
    threading.Thread(
        target=_send_notification,
        args=(title, subtitle, message, sound, group),
        daemon=True,
        name="vigil",
    ).start()


# ---------------------------------------------------------------------------
# Hook
# ---------------------------------------------------------------------------


def _on_post_llm_call(
    session_id: str = "",
    user_message: str = "",
    assistant_response: str = "",
    conversation_history: list = None,
    model: str = "",
    platform: str = "",
    **kwargs,
) -> None:
    cfg = _load_config()

    if not cfg.get("enabled", True):
        return

    # 构建通知标题和副标题
    subtitle, group = _get_title_and_group()
    config_title = cfg.get("title", DEFAULTS["title"])

    # 截取通知正文
    body_length = cfg.get("body_length", 80)
    body = assistant_response.strip()
    if body.startswith("```"):
        nl = body.find("\n")
        body = body[nl + 1:] if nl != -1 else body
    body = body[:body_length]
    if len(assistant_response.strip()) > body_length:
        body += "…"

    sound = cfg.get("sound", "Glass")

    _notify_async(config_title, subtitle, body, sound, group)


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    cfg = _load_config()
    if not cfg.get("enabled", True):
        logger.info("vigil: disabled, skipping")
        return

    ctx.register_hook("post_llm_call", _on_post_llm_call)
    logger.info("vigil: registered")
