"""Реальный Telegram-бот для веб-версии игры «Киллер / Папарацци».

Отдельный процесс (как и исходный бот, на python-telegram-bot). Отвечает за
Telegram-сторону:

  • /start — создаёт/привязывает identity (platform='tg', platform_uid = числовой
    Telegram-id), выдаёт постоянную ссылку входа на сайт (magic-link);
  • /link КОД — привязывает этот Telegram-канал к аккаунту с сайта (объединение
    tg+vk под одним пользователем; код берётся в «Настройках профиля»);
  • /register, /status, /target, /kill (=/catch), /accept, /deny, /leave — базовое
    участие в игре прямо из бота, на случай недоступности сайта (core/bot_game.py,
    core/bot_register.py); статус игры добавляется к каждому ответу;
  • любое текстовое сообщение — пересылает администраторам;
  • исходящие игровые уведомления шлёт сам веб-процесс через Bot API
    (см. core/tg_send.py и Identity.deliver) — здесь их обрабатывать не нужно.

Полный интерфейс (админка, история, профиль) — на сайте; бот даёт минимум,
необходимый игроку. Сами игровые действия выполняет общий с сайтом core/gameflow.py,
поэтому механика и условия совпадают.

Запуск (из папки проекта, отдельно от веб-сервера):
    .venv/Scripts/python.exe tg_bot.py
"""
import logging

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

from html import escape

import config
import db  # noqa: F401 — импорт инициализирует БД (таблицы)
from core import botcommon, bot_register, bot_game, photos
from core.user import User


def _kb(user=None) -> ReplyKeyboardMarkup:
    """Постоянная клавиатура под полем ввода — все действия игрока, как на сайте.

    Собирается на каждый ответ: подпись главной кнопки зависит от режима игры
    (Киллер/Папарацци) и от состояния игрока (заявка → «отменить»), как на дашборде.
    """
    return ReplyKeyboardMarkup(
        [[botcommon.LOGIN_BUTTON, botcommon.REGISTER_BUTTON],
         [botcommon.TARGET_BUTTON, botcommon.report_button(user)],
         [botcommon.CONFIRM_BUTTON, botcommon.DENY_BUTTON],
         [botcommon.STATUS_BUTTON, botcommon.LEAVE_BUTTON]],
        resize_keyboard=True, is_persistent=True)

logging.basicConfig(
    format="%(asctime)s [tg_bot] %(levelname)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tg_bot")


def _tg_name(tg_user) -> str:
    full = " ".join(p for p in (tg_user.first_name, tg_user.last_name) if p)
    return full or (tg_user.username or str(tg_user.id))


def _ensure_user(tg_user):
    return User.get_or_create_by_tg(
        str(tg_user.id), username=tg_user.username or None, name=_tg_name(tg_user))


async def _reply(update: Update, user, text: str, markup=None):
    """Ответить игроку, добавив к сообщению блок статуса игры (как на дашборде)."""
    await update.effective_message.reply_text(
        bot_game.with_status(user, text), reply_markup=markup or _kb(user))


def _welcome_markup(tg_id) -> InlineKeyboardMarkup:
    """Inline-кнопки под приветствием: «Открыть меню игры» (сразу в аккаунт) и «Правила»."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(botcommon.MENU_BUTTON, url=botcommon.menu_url_for("tg", tg_id))],
        [InlineKeyboardButton(botcommon.RULES_BUTTON, url=botcommon.rules_url())],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — привязать identity и показать ссылку входа (не сбрасывая её)."""
    tg = update.effective_user
    user = _ensure_user(tg)
    link = botcommon.login_link(user.identity("tg"))
    # Два сообщения: приветствие со ссылками-кнопками и статус с постоянной
    # клавиатурой действий (одно сообщение — одна разметка).
    await update.effective_message.reply_text(
        botcommon.welcome_text(link), reply_markup=_welcome_markup(tg.id))
    await _reply(update, user, "")
    log.info("start: user id=%s tg=%s (@%s)", user.id, tg.id, tg.username)


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link КОД — привязать этот Telegram-канал к аккаунту с сайта."""
    tg = update.effective_user
    user = _ensure_user(tg)
    code = context.args[0] if context.args else ""
    reply = botcommon.do_link(user.identity("tg"), code)
    await _reply(update, user, reply)
    log.info("link: user id=%s tg=%s code=%r", user.id, tg.id, code)


async def register_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/register — начать диалоговую регистрацию в игре прямо из бота."""
    tg = update.effective_user
    user = _ensure_user(tg)
    reply = bot_register.start(user, user.identity("tg"))
    await _reply(update, user, reply)
    log.info("register start: user id=%s tg=%s", user.id, tg.id)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel — прервать текущий диалог (регистрация, фото, причина, выход)."""
    tg = update.effective_user
    user = _ensure_user(tg)
    if bot_register.in_progress(user.id):
        reply = bot_register.handle(user, user.identity("tg"), "отмена")
    elif bot_game.in_progress(user.id):
        reply = bot_game.handle(user, "отмена")
    else:
        reply = "Сейчас нечего отменять."
    await _reply(update, user, reply)


async def target_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/target — показать свою цель при активной игре (имя — под спойлером)."""
    tg = update.effective_user
    user = _ensure_user(tg)
    kind, payload = bot_game.target_status(user)
    if kind == "target":
        # Имя цели прячем под Telegram-спойлер (HTML <tg-spoiler>), экранируя текст.
        body = ("🎯 Ваша цель (нажмите, чтобы раскрыть):\n"
                f"<tg-spoiler>{escape(payload)}</tg-spoiler>")
        # Статус добавляем тем же сообщением — он тоже уходит как HTML, поэтому
        # экранируем его целиком (в именах игроков могут быть < и &).
        await update.effective_message.reply_text(
            body + "\n\n———\n" + escape(bot_game.status_text(user)),
            parse_mode="HTML", reply_markup=_kb(user))
    else:
        await _reply(update, user, payload)
    log.info("target: user id=%s tg=%s kind=%s", user.id, tg.id, kind)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status — статус игры (он же добавляется к каждому сообщению бота)."""
    user = _ensure_user(update.effective_user)
    await _reply(update, user, "")


async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/kill (=/catch) — заявить о поимке цели; при включённом фото-пруфе спросит фото."""
    tg = update.effective_user
    user = _ensure_user(tg)
    await _reply(update, user, bot_game.report(user))
    log.info("report: user id=%s tg=%s", user.id, tg.id)


async def accept_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/accept — подтвердить, что вас поймали."""
    tg = update.effective_user
    user = _ensure_user(tg)
    await _reply(update, user, bot_game.confirm(user))
    log.info("accept: user id=%s tg=%s", user.id, tg.id)


async def deny_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deny — не подтвердить поимку (бот спросит причину)."""
    tg = update.effective_user
    user = _ensure_user(tg)
    await _reply(update, user, bot_game.deny(user))
    log.info("deny: user id=%s tg=%s", user.id, tg.id)


async def leave_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/leave — выйти из игры (с подтверждением, как модальное окно на сайте)."""
    tg = update.effective_user
    user = _ensure_user(tg)
    await _reply(update, user, bot_game.leave(user))
    log.info("leave: user id=%s tg=%s", user.id, tg.id)


async def photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Фотография от игрока — фото-пруф поимки, если бот его сейчас ждёт."""
    tg = update.effective_user
    user = _ensure_user(tg)
    msg = update.effective_message
    if not bot_game.in_progress(user.id):
        await _reply(update, user, botcommon.auto_reply_text())
        return
    if msg.photo:
        tg_file = await msg.photo[-1].get_file()
        ext = ".jpg"
    else:  # фото прислали файлом (без сжатия)
        doc = msg.document
        tg_file = await doc.get_file()
        ext = photos.EXTS.get(doc.mime_type or "", ".jpg")
    data = bytes(await tg_file.download_as_bytearray())
    reply = bot_game.handle_photo(user, data, ext)
    await _reply(update, user, reply or botcommon.auto_reply_text())
    log.info("photo: user id=%s tg=%s bytes=%s", user.id, tg.id, len(data))


async def forward_to_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовые сообщения игрока: шаг регистрации (если идёт) → автомату; «Начать» →
    /start; «Регистрация» → старт регистрации; иначе — авто-ответ (+ пересылка админам)."""
    text = (update.effective_message.text or "").strip()
    tg = update.effective_user
    user = _ensure_user(tg)
    # Идут диалоги (регистрация / причина отказа / подтверждение выхода) — очередной
    # ответ отдаём автомату ПЕРВЫМ, чтобы он не перехватывался кнопками/пересылкой.
    if bot_register.in_progress(user.id):
        reply = bot_register.handle(user, user.identity("tg"), text)
        if reply is not None:
            await _reply(update, user, reply)
        return
    if bot_game.in_progress(user.id):
        reply = bot_game.handle(user, text)
        if reply is not None:
            await _reply(update, user, reply)
            return
    if botcommon.is_login_request(text):
        await start(update, context)
        return
    if botcommon.is_register_request(text):
        await register_cmd(update, context)
        return
    if botcommon.is_target_request(text):
        await target_cmd(update, context)
        return
    if botcommon.is_report_request(text):
        await report_cmd(update, context)
        return
    if botcommon.is_confirm_request(text):
        await accept_cmd(update, context)
        return
    if botcommon.is_deny_request(text):
        await deny_cmd(update, context)
        return
    if botcommon.is_status_request(text):
        await status_cmd(update, context)
        return
    if botcommon.is_leave_request(text):
        await leave_cmd(update, context)
        return
    # Пересылаем админам (best-effort) и всегда отвечаем игроку авто-ответом: сообщения
    # боту могут быть не прочитаны, управление — на сайте, за поддержкой — контакт (TODO 86).
    botcommon.forward_to_admins(user, "Telegram", text)
    await _reply(update, user, botcommon.auto_reply_text())


# Короткое описание на странице профиля бота (лимит Telegram — 120 символов).
_SHORT_DESC = ("🕵️ Игра «Киллер / Папарацци». Нажмите «Начать», чтобы войти на сайт игры.")


async def _announce_connected(application):
    """Вызывается после подключения — печатаем явную индикацию, что бот на связи."""
    me = await application.bot.get_me()
    # Текст на экране пустого чата (до первого /start) — предлагает нажать «Начать».
    try:
        await application.bot.set_my_description(botcommon.intro_text())
        await application.bot.set_my_short_description(_SHORT_DESC)
        await application.bot.set_my_commands([
            BotCommand("start", "получить ссылку для входа"),
            BotCommand("status", "статус игры"),
            BotCommand("register", "зарегистрироваться в игре"),
            BotCommand("target", "узнать свою цель (при активной игре)"),
            BotCommand("kill", "сообщить о поимке / убийстве цели"),
            BotCommand("accept", "подтвердить свою поимку"),
            BotCommand("deny", "не подтвердить поимку (с причиной)"),
            BotCommand("leave", "выйти из игры"),
            BotCommand("cancel", "прервать текущий диалог"),
            BotCommand("link", "привязать этот чат к аккаунту (код из профиля)"),
        ])
    except Exception as exc:  # noqa: BLE001 — приветствие не критично для работы
        log.warning("не удалось задать описание/команды бота: %s", exc)
    log.info("✅ Telegram-бот успешно подключён: @%s (id %s). Ожидаю сообщения…",
             me.username, me.id)


def main():
    if not getattr(config, "ENABLE_TG_BOT", True):
        raise SystemExit("Telegram-бот выключен в config.py (ENABLE_TG_BOT = False).")
    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в config.py — бот не запущен.")
    app = (Application.builder().token(token)
           .post_init(_announce_connected).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("register", register_cmd))
    app.add_handler(CommandHandler("target", target_cmd))
    # kill/catch — одно и то же действие, подпись зависит от режима игры.
    app.add_handler(CommandHandler(["kill", "catch"], report_cmd))
    app.add_handler(CommandHandler("accept", accept_cmd))
    app.add_handler(CommandHandler("deny", deny_cmd))
    app.add_handler(CommandHandler("leave", leave_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admins))
    log.info("Telegram-бот запускается (long polling)… Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
