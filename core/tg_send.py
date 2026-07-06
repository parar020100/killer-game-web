"""Отправка сообщений в Telegram через HTTP API — без event loop.

Веб-процесс доставляет игровые уведомления реальным Telegram-пользователям
(identity с числовым platform_uid = chat_id) прямым HTTP-запросом к Bot API,
не поднимая асинхронный контекст python-telegram-bot. Приём сообщений (/start и
т.п.) обрабатывает отдельный процесс бота — tg_bot.py.

Токен берётся из config.TELEGRAM_BOT_TOKEN. Если он не задан или отправка не
удалась, вызывающий код откатывается на эмуляцию чата (core/chat.py).
"""
import httpx

import config

_API = "https://api.telegram.org/bot{token}/sendMessage"


def enabled() -> bool:
    return bool((getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip())


def send(chat_id, text: str, with_menu: bool = True) -> bool:
    """Отправить текст в чат Telegram. True при успехе, False при любой ошибке.

    with_menu=True добавляет к сообщению inline-кнопку «Открыть меню игры».
    """
    token = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        return False
    payload = {"chat_id": int(chat_id), "text": text,
               "disable_web_page_preview": True}
    if with_menu:
        from core import botcommon
        payload["reply_markup"] = {"inline_keyboard": [[
            {"text": botcommon.MENU_BUTTON, "url": botcommon.menu_url()}]]}
    try:
        r = httpx.post(_API.format(token=token), json=payload, timeout=10)
        return r.status_code == 200 and r.json().get("ok", False)
    except (httpx.HTTPError, ValueError, TypeError):
        return False
