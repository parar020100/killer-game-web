"""Реальный VK-бот сообщества для веб-версии игры «Киллер / Папарацци».

Отдельный процесс (аналог tg_bot.py, но для ВКонтакте). Работает через Bots Long
Poll API сообщества, без внешних библиотек (только httpx). Отвечает за VK-сторону:

  • «Начать» / /start — создаёт/привязывает identity (platform='vk',
    platform_uid = числовой VK-id), выдаёт постоянную ссылку входа на сайт;
  • /link КОД — привязывает этот VK-канал к аккаунту с сайта (объединение tg+vk);
  • кнопки игровых действий (регистрация, статус, цель, заявка о поимке с фото,
    подтвердить/отклонить, выйти) — базовое участие без сайта; действия выполняет
    общий с сайтом core/gameflow.py, статус игры идёт под каждым ответом;
  • любое сообщение — пересылает администраторам;
  • исходящие уведомления шлёт сам веб-процесс через messages.send
    (см. core/vk_send.py и Identity.deliver).

Настройка сообщества (Long Poll, права, токен) — в info/SETUP.md.

Запуск:  .venv/Scripts/python.exe vk_bot.py
"""
import json
import logging
import os
import random
import time

import httpx

import config
import db  # noqa: F401 — инициализация БД
from core import botcommon, bot_register, bot_game
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


def _action_rows(user_id):
    """Единый набор действий бота: все игровые кнопки (как на дашборде сайта) плюс
    ссылки «Открыть меню игры» и «Правила». Используется и нижней клавиатурой, и
    inline-«меню» — чтобы все действия были доступны в обоих местах."""
    def txt(label):
        return {"action": {"type": "text", "label": label}}
    # Подпись главной кнопки зависит от режима игры и состояния игрока (заявка →
    # «отменить») — как главный слот на дашборде.
    user = User.by_vk(str(user_id))
    return [
        [txt(botcommon.LOGIN_BUTTON), txt(botcommon.REGISTER_BUTTON)],
        [txt(botcommon.TARGET_BUTTON), txt(botcommon.report_button(user))],
        [txt(botcommon.CONFIRM_BUTTON), txt(botcommon.DENY_BUTTON)],
        [txt(botcommon.STATUS_BUTTON), txt(botcommon.LEAVE_BUTTON)],
        [{"action": {"type": "open_link", "link": botcommon.menu_url_for("vk", user_id),
                     "label": botcommon.MENU_BUTTON}},
         {"action": {"type": "open_link", "link": botcommon.rules_url(),
                     "label": botcommon.RULES_BUTTON}}],
    ]


def _main_kb(user_id) -> str:
    """Нижняя постоянная клавиатура (держится, пока не пришлём новую; видна после ответа
    бота). Раньше приветствие слало только inline — нижняя клавиатура не появлялась."""
    return json.dumps({"one_time": False, "buttons": _action_rows(user_id)},
                      ensure_ascii=False)


def _inline_kb(user_id) -> str:
    """Inline-«меню» под сообщением — те же действия, что и в нижней клавиатуре."""
    return json.dumps({"inline": True, "buttons": _action_rows(user_id)},
                      ensure_ascii=False)


def _send(user_id, text: str, keyboard: str = None):
    if keyboard is None:
        keyboard = _main_kb(user_id)
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


def _largest_photo_url(attachments) -> str:
    """Ссылка на самый крупный размер первой фотографии вложения (или '')."""
    for att in attachments or []:
        if att.get("type") != "photo":
            continue
        sizes = (att.get("photo") or {}).get("sizes") or []
        if sizes:
            best = max(sizes, key=lambda s: (s.get("width", 0), s.get("height", 0)))
            return best.get("url", "")
    return ""


def _handle_photo(user, from_id, attachments) -> bool:
    """Фото-пруф поимки, если бот его сейчас ждёт. True — сообщение обработано."""
    if not bot_game.in_progress(user.id):
        return False
    url = _largest_photo_url(attachments)
    if not url:
        return False
    try:
        data = httpx.get(url, timeout=30).content
    except httpx.HTTPError as exc:
        log.warning("не удалось скачать фото пользователя %s: %s", from_id, exc)
        _send(from_id, bot_game.with_status(
            user, "⚠️ Не удалось загрузить фото. Попробуйте отправить ещё раз."))
        return True
    reply = bot_game.handle_photo(user, data, ".jpg")
    if reply is None:
        return False
    _send(from_id, bot_game.with_status(user, reply))
    log.info("photo: user id=%s vk=%s bytes=%s", user.id, from_id, len(data))
    return True


def handle_message(from_id, text: str, attachments=None):
    text = (text or "").strip()
    log.info("← сообщение от %s: %r", from_id, text)
    low = text.lower()
    user = _ensure_user(from_id)
    ident = user.identity("vk")

    def reply(body, keyboard=None):
        """Ответ игроку — со статусом игры под каждым сообщением."""
        _send(from_id, bot_game.with_status(user, body), keyboard=keyboard)

    # Ждём фото-пруф — вложение важнее текста.
    if _handle_photo(user, from_id, attachments):
        return

    # Идут диалоги (регистрация / причина отказа / подтверждение выхода) — очередной
    # ответ отдаём автомату ПЕРВЫМ, чтобы он не перехватывался кнопками/пересылкой.
    if bot_register.in_progress(user.id):
        answer = bot_register.handle(user, ident, text)
        if answer is not None:
            reply(answer)
        log.info("register step: user id=%s vk=%s", user.id, from_id)
        return
    if bot_game.in_progress(user.id):
        answer = bot_game.handle(user, text)
        if answer is not None:
            reply(answer)
            log.info("game dialog step: user id=%s vk=%s", user.id, from_id)
            return
    if botcommon.is_register_request(text):
        reply(bot_register.start(user, ident))
        log.info("register start: user id=%s vk=%s", user.id, from_id)
        return
    if botcommon.is_target_request(text):
        kind, payload = bot_game.target_status(user)
        # В VK спойлеров нет — имя цели показываем как есть.
        reply(f"🎯 Ваша цель: {payload}" if kind == "target" else payload)
        log.info("target: user id=%s vk=%s kind=%s", user.id, from_id, kind)
        return
    if botcommon.is_report_request(text):
        reply(bot_game.report(user))
        log.info("report: user id=%s vk=%s", user.id, from_id)
        return
    if botcommon.is_confirm_request(text):
        reply(bot_game.confirm(user))
        log.info("accept: user id=%s vk=%s", user.id, from_id)
        return
    if botcommon.is_deny_request(text):
        reply(bot_game.deny(user))
        log.info("deny: user id=%s vk=%s", user.id, from_id)
        return
    if botcommon.is_leave_request(text):
        reply(bot_game.leave(user))
        log.info("leave: user id=%s vk=%s", user.id, from_id)
        return
    if botcommon.is_status_request(text):
        reply("")
        return

    if low in _START_WORDS or botcommon.is_login_request(text):
        link = botcommon.login_link(ident)
        # 1) приветствие с нижней клавиатурой (persist), 2) inline-«меню» под сообщением —
        # так все действия видны и в нижней клавиатуре, и в меню.
        _send(from_id, botcommon.welcome_text(link))
        reply("📋 Меню — быстрые действия:", keyboard=_inline_kb(from_id))
        log.info("start: user id=%s vk=%s", user.id, from_id)
        return
    if low.startswith("/link"):
        code = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
        reply(botcommon.do_link(ident, code))
        log.info("link: user id=%s vk=%s code=%r", user.id, from_id, code)
        return

    # прочее: пересылаем админам (best-effort) и всегда отвечаем игроку авто-ответом —
    # сообщения боту могут быть не прочитаны, управление на сайте, поддержка (TODO 86).
    botcommon.forward_to_admins(user, "VK", text)
    reply(botcommon.auto_reply_text())


def _write_intro_file():
    """Сохранить текст приветствия в intro.txt (рядом с ботом). VK не даёт задать
    «Приветствие» сообщества через API — этот файл готов к копированию в настройки
    сообщества (Управление → Сообщения → Настройки для бота → «Приветствие»)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intro.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(botcommon.intro_text())
        log.info("приветствие сохранено в %s (вставьте его в «Приветствие» сообщества)", path)
    except OSError as exc:
        log.warning("не удалось записать intro.txt: %s", exc)


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
    _write_intro_file()
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
                handle_message(from_id, msg.get("text", "") or msg.get("body", ""),
                               msg.get("attachments"))
            except Exception as exc:  # noqa: BLE001 — не роняем бота из-за одного апдейта
                log.exception("handle_message failed: %s", exc)


if __name__ == "__main__":
    main()
