"""Отправка сообщений во ВКонтакте через API сообщества — без long poll.

Веб-процесс доставляет игровые уведомления реальным VK-пользователям (identity с
числовым platform_uid = VK user id) прямым вызовом messages.send от имени
сообщества. Приём сообщений (/start, /link и т.п.) обрабатывает отдельный процесс
бота — vk_bot.py.

Ключ доступа сообщества и версия API — из config (VK_GROUP_TOKEN, VK_API_VERSION).
Если ключ не задан или отправка не удалась, вызывающий код откатывается на
эмуляцию чата (core/chat.py).
"""
import random

import httpx

import config

_API = "https://api.vk.com/method/messages.send"


def enabled() -> bool:
    return bool((getattr(config, "VK_GROUP_TOKEN", "") or "").strip())


def send(user_id, text: str) -> bool:
    """Отправить текст пользователю ВК. True при успехе, False при любой ошибке."""
    token = (getattr(config, "VK_GROUP_TOKEN", "") or "").strip()
    if not token:
        return False
    try:
        r = httpx.get(_API, params={
            "access_token": token,
            "v": getattr(config, "VK_API_VERSION", "5.199"),
            "user_id": int(user_id),
            "message": text,
            "random_id": random.randint(1, 2_000_000_000),
        }, timeout=10).json()
        return "response" in r
    except (httpx.HTTPError, ValueError, TypeError):
        return False
