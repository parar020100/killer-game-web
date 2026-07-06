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

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

import config
import db  # noqa: F401 — импорт инициализирует БД (таблицы)
from core import botcommon
from core.user import User

logging.basicConfig(
    format="%(asctime)s [tg_bot] %(levelname)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tg_bot")


def _tg_name(tg_user) -> str:
    full = " ".join(p for p in (tg_user.first_name, tg_user.last_name) if p)
    return full or (tg_user.username or str(tg_user.id))


def _ensure_user(tg_user):
    return User.get_or_create_by_tg(
        str(tg_user.id), username=tg_user.username or None, name=_tg_name(tg_user))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — привязать identity и выдать постоянную ссылку входа."""
    tg = update.effective_user
    user = _ensure_user(tg)
    link, reissued = botcommon.issue_login_link(user.identity("tg"))
    await update.effective_message.reply_text(botcommon.welcome_text(link, reissued))
    log.info("start: user id=%s tg=%s (@%s)", user.id, tg.id, tg.username)


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/link КОД — привязать этот Telegram-канал к аккаунту с сайта."""
    tg = update.effective_user
    user = _ensure_user(tg)
    code = context.args[0] if context.args else ""
    reply = botcommon.do_link(user.identity("tg"), code)
    await update.effective_message.reply_text(reply)


async def forward_to_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое сообщение игрока пересылаем администраторам (как в исходном боте)."""
    tg = update.effective_user
    user = User.by_tg(str(tg.id))
    text = update.effective_message.text or ""
    has_admins = botcommon.forward_to_admins(user, "Telegram", text)
    if not has_admins:
        await update.effective_message.reply_text(
            "Сообщение получено, но пока некому его переслать "
            "(нет администраторов).")


def main():
    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в config.py — бот не запущен.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("link", link_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admins))
    log.info("Telegram-бот запущен (long-polling). Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
