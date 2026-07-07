"""Реальный Telegram-бот для веб-версии игры «Киллер / Папарацци».

Отдельный процесс (как и исходный бот, на python-telegram-bot). Отвечает за
Telegram-сторону:

  • /start — создаёт/привязывает identity (platform='tg', platform_uid = числовой
    Telegram-id), выдаёт постоянную ссылку входа на сайт (magic-link);
  • /link КОД — привязывает этот Telegram-канал к аккаунту с сайта (объединение
    tg+vk под одним пользователем; код берётся в «Настройках профиля»);
  • любое текстовое сообщение — пересылает администраторам;
  • исходящие игровые уведомления шлёт сам веб-процесс через Bot API
    (см. core/tg_send.py и Identity.deliver) — здесь их обрабатывать не нужно.

Весь игровой интерфейс — на сайте, поэтому бот намеренно минимальный.

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

import config
import db  # noqa: F401 — импорт инициализирует БД (таблицы)
from core import botcommon
from core.user import User

# Постоянная кнопка под полем ввода — её нажатие равносильно команде /start.
_KB = ReplyKeyboardMarkup([[botcommon.LOGIN_BUTTON]],
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
    await update.effective_message.reply_text(
        botcommon.welcome_text(link), reply_markup=_welcome_markup(tg.id))
    log.info("start: user id=%s tg=%s (@%s)", user.id, tg.id, tg.username)


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link КОД — привязать этот Telegram-канал к аккаунту с сайта."""
    tg = update.effective_user
    user = _ensure_user(tg)
    code = context.args[0] if context.args else ""
    reply = botcommon.do_link(user.identity("tg"), code)
    await update.effective_message.reply_text(reply, reply_markup=_KB)
    log.info("link: user id=%s tg=%s code=%r", user.id, tg.id, code)


async def forward_to_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовые сообщения игрока: нажатие кнопки-«/start» → /start, иначе — админам."""
    text = (update.effective_message.text or "").strip()
    if botcommon.is_login_request(text):
        await start(update, context)
        return
    tg = update.effective_user
    user = User.by_tg(str(tg.id))
    has_admins = botcommon.forward_to_admins(user, "Telegram", text)
    if not has_admins:
        await update.effective_message.reply_text(
            "Сообщение получено, но пока некому его переслать "
            "(нет администраторов).", reply_markup=_KB)


# Приветствие на пустом экране чата (кнопка «Начать» в Telegram запускает /start).
_GREETING = ("🕵️ Игра «Киллер / Папарацци».\n"
             "Нажмите «Начать», чтобы получить ссылку для входа на сайт.")


async def _announce_connected(application):
    """Вызывается после подключения — печатаем явную индикацию, что бот на связи."""
    me = await application.bot.get_me()
    # Текст на экране пустого чата (до первого /start) — предлагает нажать «Начать».
    try:
        await application.bot.set_my_description(_GREETING)
        await application.bot.set_my_short_description(_GREETING)
        await application.bot.set_my_commands([
            BotCommand("start", "получить ссылку для входа"),
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
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admins))
    log.info("Telegram-бот запускается (long polling)… Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
