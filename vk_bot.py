"""Реальный VK-бот сообщества для веб-версии игры «Киллер / Папарацци».

Отдельный процесс (аналог tg_bot.py, но для ВКонтакте). Работает через Bots Long
Poll API сообщества, без внешних библиотек (только httpx). Отвечает за VK-сторону:

  • «Начать» / /start — создаёт/привязывает identity (platform='vk',
    platform_uid = числовой VK-id), выдаёт постоянную ссылку входа на сайт;
  • /link КОД — привязывает этот VK-канал к аккаунту с сайта (объединение tg+vk);
  • любое сообщение — пересылает администраторам;
  • исходящие уведомления шлёт сам веб-процесс через messages.send
    (см. core/vk_send.py и Identity.deliver).

Настройка сообщества (Long Poll, права, токен) — в info/SETUP.md.

Запуск:  .venv/Scripts/python.exe vk_bot.py
"""
import json
import logging
import random
import time

import httpx

import config
import db  # noqa: F401 — инициализация БД
from core import botcommon
from core.user import User

logging.basicConfig(
    format="%(asctime)s [vk_bot] %(levelname)s: %(message)s", level=logging.INFO)
log = logging.getLogger("vk_bot")

_START_WORDS = {"/start", "start", "начать", "старт", "привет", "begin"}


def _api(method: str, **params):
    params.setdefault("access_token", config.VK_GROUP_TOKEN)
    params.setdefault("v", getattr(config, "VK_API_VERSION", "5.199"))
    r = httpx.get(f"https://api.vk.com/method/{method}", params=params, timeout=30).json()
    if "error" in r:
        raise RuntimeError(f"VK API {method}: {r['error'].get('error_msg')}")
    return r["response"]


# Постоянная клавиатура сообщества с кнопкой-«/start» (не одноразовая).
_PERSISTENT_KB = json.dumps({"one_time": False, "buttons": [[
    {"action": {"type": "text", "label": botcommon.LOGIN_BUTTON}}]]},
    ensure_ascii=False)


def _welcome_kb(user_id) -> str:
    """Inline-кнопки под приветствием: «Открыть меню игры» (сразу в аккаунт) и «Правила»."""
    return json.dumps({"inline": True, "buttons": [
        [{"action": {"type": "open_link", "link": botcommon.menu_url_for("vk", user_id),
                     "label": botcommon.MENU_BUTTON}}],
        [{"action": {"type": "open_link", "link": botcommon.rules_url(),
                     "label": botcommon.RULES_BUTTON}}],
    ]}, ensure_ascii=False)


def _send(user_id, text: str, keyboard: str = _PERSISTENT_KB):
    try:
        params = dict(user_id=user_id, message=text,
                      random_id=random.randint(1, 2_000_000_000))
        if keyboard:
            params["keyboard"] = keyboard
        _api("messages.send", **params)
        log.info("→ ответ отправлен пользователю %s", user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("НЕ удалось отправить пользователю %s: %s", user_id, exc)


def _vk_name(user_id):
    """Имя и screen_name пользователя (для профиля). Возвращает (name, screen_name)."""
    try:
        info = _api("users.get", user_ids=user_id, fields="screen_name")[0]
        name = " ".join(p for p in (info.get("first_name"), info.get("last_name")) if p)
        return name or None, info.get("screen_name")
    except Exception:  # noqa: BLE001
        return None, None


def _ensure_user(from_id):
    name, screen = _vk_name(from_id)
    return User.get_or_create_by_vk(str(from_id), username=screen, name=name or str(from_id))


def handle_message(from_id, text: str):
    text = (text or "").strip()
    log.info("← сообщение от %s: %r", from_id, text)
    low = text.lower()
    user = _ensure_user(from_id)
    ident = user.identity("vk")

    if low in _START_WORDS or botcommon.is_login_request(text):
        link = botcommon.login_link(ident)
        _send(from_id, botcommon.welcome_text(link), keyboard=_welcome_kb(from_id))
        log.info("start: user id=%s vk=%s", user.id, from_id)
        return
    if low.startswith("/link"):
        code = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
        _send(from_id, botcommon.do_link(ident, code))
        log.info("link: user id=%s vk=%s code=%r", user.id, from_id, code)
        return

    # прочее — пересылаем администраторам
    has_admins = botcommon.forward_to_admins(user, "VK", text)
    if not has_admins:
        _send(from_id, "Сообщение получено, но пока некому его переслать "
                       "(нет администраторов).")


def _get_long_poll_server(group_id):
    r = _api("groups.getLongPollServer", group_id=group_id)
    return r["server"], r["key"], r["ts"]


def _resolve_group_id(configured: str) -> str:
    """Числовой id сообщества. Если задано короткое имя (screen_name) или пусто —
    берём id из самого токена сообщества (groups.getById)."""
    configured = (configured or "").strip()
    if configured.isdigit():
        return configured
    grp = _api("groups.getById")["groups"][0]
    gid = str(grp["id"])
    log.info("VK_GROUP_ID=%r → числовой id сообщества %s (%s)",
             configured, gid, grp.get("screen_name"))
    return gid


def main():
    if not getattr(config, "ENABLE_VK_BOT", True):
        raise SystemExit("VK-бот выключен в config.py (ENABLE_VK_BOT = False).")
    token = (config.VK_GROUP_TOKEN or "").strip()
    if not token:
        raise SystemExit("VK_GROUP_TOKEN не задан в config.py — VK-бот не запущен.")
    group_id = _resolve_group_id(str(config.VK_GROUP_ID))

    try:
        server, key, ts = _get_long_poll_server(group_id)
    except RuntimeError as exc:
        if "longpoll" in str(exc).lower():
            raise SystemExit(
                "❌ В сообществе не включён Bots Long Poll API.\n"
                "Включите его: Управление сообществом → Работа с API → Long Poll API →\n"
                "  • включить Long Poll API, версия " + config.VK_API_VERSION + ";\n"
                "  • вкладка «Типы событий» → включить «Входящее сообщение» (message_new).\n"
                "Также: Управление → Сообщения → включить «Сообщения сообщества».\n"
                "Подробно — в info/SETUP.md, раздел 5.") from exc
        raise

    try:
        grp = _api("groups.getById")["groups"][0]
        who = f"{grp.get('name')} (id {group_id})"
    except Exception:  # noqa: BLE001
        who = f"id {group_id}"
    log.info("✅ VK-бот успешно подключён: %s. Ожидаю сообщения…", who)
    while True:
        try:
            resp = httpx.get(server, params={"act": "a_check", "key": key,
                                             "ts": ts, "wait": 25}, timeout=30).json()
        except httpx.HTTPError as exc:
            log.warning("long poll network error: %s", exc)
            time.sleep(3)
            continue
        # Ошибки long poll: 1 — устарел ts; 2/3 — ключ/сервер невалидны → переполучить.
        if "failed" in resp:
            failed = resp["failed"]
            if failed == 1:
                ts = resp["ts"]
            else:
                server, key, ts = _get_long_poll_server(group_id)
            continue
        ts = resp["ts"]
        updates = resp.get("updates", [])
        if updates:
            log.info("получено обновлений: %d (%s)", len(updates),
                     ", ".join(u.get("type", "?") for u in updates))
        for upd in updates:
            if upd.get("type") != "message_new":
                continue
            obj = upd.get("object", {})
            # API 5.x: событие в object.message; на старых версиях — сам object.
            msg = obj.get("message", obj)
            from_id = msg.get("from_id") or msg.get("user_id")
            if not from_id or from_id < 0:   # сообщения от сообществ игнорируем
                continue
            try:
                handle_message(from_id, msg.get("text", "") or msg.get("body", ""))
            except Exception as exc:  # noqa: BLE001 — не роняем бота из-за одного апдейта
                log.exception("handle_message failed: %s", exc)


if __name__ == "__main__":
    main()
