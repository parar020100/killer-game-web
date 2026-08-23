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


def _menu_keyboard(user_id, menu_user=None) -> str:
    """Inline-клавиатура под уведомлением. С `menu_user` — состояние-зависимое меню
    действий (принять приглашение, подтвердить поимку и т.п.), собранное общим
    `core.botmenu` — те же кнопки, что в процессе бота; их нажатие (текст) маршрутизирует
    процесс бота. Без него — только кнопка-ссылка «Открыть меню игры».

    У VK inline-клавиатуры лимит 6 кнопок, поэтому при полном меню ссылку «Открыть
    меню» не добавляем (она есть в нижней клавиатуре)."""
    from core import botcommon, botmenu
    buttons = botmenu.vk_button_rows(menu_user) if menu_user is not None else []
    if not buttons:   # нет актуальных действий (или без получателя) — кнопка-ссылка
        buttons = [[{"action": {"type": "open_link",
                                "link": botcommon.menu_url_for("vk", user_id),
                                "label": botcommon.MENU_BUTTON}}]]
    return json.dumps({"inline": True, "buttons": buttons}, ensure_ascii=False)


def send(user_id, text: str, with_menu: bool = True, silent: bool = False,
         menu_user=None) -> bool:
    """Отправить текст пользователю ВК. True при успехе, False при любой ошибке.

    with_menu=True добавляет к сообщению inline-меню действий (те же кнопки, что в
    процессе бота, по состоянию получателя `menu_user`); без `menu_user` — только
    кнопка-ссылка «Открыть меню игры».
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
        params["keyboard"] = _menu_keyboard(user_id, menu_user)
    try:
        r = httpx.get(_API, params=params, timeout=10).json()
        if "response" in r:
            _log_ok(f"уведомление → user {user_id} доставлено")
            return True
        _log_error(f"VK messages.send → user {user_id}: {str(r)[:200]}")
        return False
    except (httpx.HTTPError, ValueError, TypeError) as e:
        _log_error(f"VK messages.send → user {user_id}: {type(e).__name__}: {e}")
        return False


def _log_ok(text: str):
    try:
        from core import bot_log
        bot_log.log(text, "vk")
    except Exception:
        pass


def _log_error(text: str):
    try:
        from core import bot_log
        bot_log.error(text, "vk")
    except Exception:
        pass
