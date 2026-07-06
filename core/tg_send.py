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
    return (bool(getattr(config, "ENABLE_TG_BOT", True))
            and bool((getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()))


def send(chat_id, text: str, with_menu: bool = True, silent: bool = False) -> bool:
    """Отправить текст в чат Telegram. True при успехе, False при любой ошибке.

    with_menu=True добавляет к сообщению inline-кнопку «Открыть меню игры».
    silent=True — тихое уведомление без звука (Telegram disable_notification).
    """
    if not enabled():
        return False
    token = (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    payload = {"chat_id": int(chat_id), "text": text,
               "disable_web_page_preview": True}
    if silent:
        payload["disable_notification"] = True
    if with_menu:
        from core import botcommon
        payload["reply_markup"] = {"inline_keyboard": [[
            {"text": botcommon.MENU_BUTTON, "url": botcommon.menu_url()}]]}
    try:
        r = httpx.post(_API.format(token=token), json=payload, timeout=10)
        if r.status_code == 200 and r.json().get("ok", False):
            return True
        _log_error(f"Telegram sendMessage → chat {chat_id}: HTTP {r.status_code} {r.text[:200]}")
        return False
    except (httpx.HTTPError, ValueError, TypeError) as e:
        _log_error(f"Telegram sendMessage → chat {chat_id}: {type(e).__name__}: {e}")
        return False


def _log_error(text: str):
    try:
        from core import bot_log
        bot_log.error(text)
    except Exception:
        pass
