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
    "title_prefix": "vigil",
    "sound": "Glass",
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
    message: str,
    sound: str,
    group: str | None,
) -> None:
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
        # 点击通知关闭（无跳转）
        cmd += ["-execute", "true"]

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
    message: str,
    sound: str,
    group: str | None,
) -> None:
    threading.Thread(
        target=_send_notification,
        args=(title, message, sound, group),
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

    # 构建通知标题（带前缀）
    title, group = _get_title_and_group()
    title_prefix = cfg.get("title_prefix")
    if title_prefix:
        full_title = f"{title_prefix}:{title}"
    else:
        full_title = title

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

    _notify_async(full_title, body, sound, group)


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
