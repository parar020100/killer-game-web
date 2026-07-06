"""Отправка сообщений во ВКонтакте через API сообщества — без long poll.

Веб-процесс доставляет игровые уведомления реальным VK-пользователям (identity с
числовым platform_uid = VK user id) прямым вызовом messages.send от имени
сообщества. Приём сообщений (/start, /link и т.п.) обрабатывает отдельный процесс
бота — vk_bot.py.

Ключ доступа сообщества и версия API — из config (VK_GROUP_TOKEN, VK_API_VERSION).
Если ключ не задан или отправка не удалась, вызывающий код откатывается на
эмуляцию чата (core/chat.py).
"""
import json
import random

import httpx

import config

_API = "https://api.vk.com/method/messages.send"


def enabled() -> bool:
    return (bool(getattr(config, "ENABLE_VK_BOT", True))
            and bool((getattr(config, "VK_GROUP_TOKEN", "") or "").strip()))


def _menu_keyboard() -> str:
    from core import botcommon
    return json.dumps({"inline": True, "buttons": [[
        {"action": {"type": "open_link", "link": botcommon.menu_url(),
                    "label": botcommon.MENU_BUTTON}}]]}, ensure_ascii=False)


def send(user_id, text: str, with_menu: bool = True, silent: bool = False) -> bool:
    """Отправить текст пользователю ВК. True при успехе, False при любой ошибке.

    with_menu=True добавляет к сообщению inline-кнопку «Открыть меню игры».
    silent принимается для единообразия с другими каналами; VK Bots API не
    поддерживает беззвучную доставку — флаг игнорируется.
    """
    if not enabled():
        return False
    token = (getattr(config, "VK_GROUP_TOKEN", "") or "").strip()
    params = {
        "access_token": token,
        "v": getattr(config, "VK_API_VERSION", "5.199"),
        "user_id": int(user_id),
        "message": text,
        "random_id": random.randint(1, 2_000_000_000),
    }
    if with_menu:
        params["keyboard"] = _menu_keyboard()
    try:
        r = httpx.get(_API, params=params, timeout=10).json()
        if "response" in r:
            return True
        _log_error(f"VK messages.send → user {user_id}: {str(r)[:200]}")
        return False
    except (httpx.HTTPError, ValueError, TypeError) as e:
        _log_error(f"VK messages.send → user {user_id}: {type(e).__name__}: {e}")
        return False


def _log_error(text: str):
    try:
        from core import bot_log
        bot_log.error(text)
    except Exception:
        pass
