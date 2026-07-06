"""Реальный Telegram-бот для веб-версии игры «Киллер / Папарацци».

Отдельный процесс (как и исходный бот, на python-telegram-bot). Отвечает за
Telegram-сторону:

  • /start — создаёт/привязывает identity (platform='tg', platform_uid = числовой
    Telegram-id), выдаёт постоянную ссылку входа на сайт (magic-link);
  • любое текстовое сообщение — пересылает администраторам (как в исходном боте);
  • исходящие игровые уведомления шлёт сам веб-процесс напрямую через Bot API
    (см. core/tg_send.py и Identity.deliver) — здесь их обрабатывать не нужно.

Весь игровой интерфейс — на сайте, поэтому бот намеренно минимальный.

Запуск (из папки проекта, отдельно от веб-сервера):
    .venv/Scripts/python.exe tg_bot.py
Токен — config.TELEGRAM_BOT_TOKEN; базовый адрес сайта — config.APP_BASE_URL.
"""
import logging

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
)

import config
import db  # noqa: F401 — импорт инициализирует БД (таблицы)
from core import auth, admin_log
from core.user import User

logging.basicConfig(
    format="%(asctime)s [tg_bot] %(levelname)s: %(message)s", level=logging.INFO)
log = logging.getLogger("tg_bot")


def _tg_name(tg_user) -> str:
    full = " ".join(p for p in (tg_user.first_name, tg_user.last_name) if p)
    return full or (tg_user.username or str(tg_user.id))


def _login_link(token: str) -> str:
    base = (config.APP_BASE_URL or "http://127.0.0.1:8000").rstrip("/")
    return f"{base}/login?token={token}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — привязать identity и выдать постоянную ссылку входа."""
    tg = update.effective_user
    user = User.get_or_create_by_tg(
        str(tg.id), username=tg.username or None, name=_tg_name(tg))
    ident = user.identity("tg")
    reissued = auth.has_permanent_token(ident.id)
    token = auth.set_permanent_token(ident.id)
    link = _login_link(token)
    note = "Прежняя ссылка больше не работает.\n" if reissued else ""
    await update.effective_message.reply_text(
        "🕵️ Игра «Киллер / Папарацци».\n\n"
        "Ваша постоянная ссылка для входа на сайт (сохраните в закладки):\n"
        f"{link}\n"
        f"{note}Она работает всегда и не имеет срока. Никому её не пересылайте — "
        "по ней входят в вашу учётку. Наберёте /start ещё раз — будет выдана новая, "
        "а старая перестанет работать.\n\n"
        "Всё управление игрой — на сайте по ссылке. Сюда можно писать сообщения "
        "организаторам."
    )
    log.info("start: user id=%s tg=%s (@%s)", user.id, tg.id, tg.username)


def _admin_tg_chat_ids():
    """chat_id администраторов с привязанным Telegram (числовой uid)."""
    out = []
    for admin in User.all_admins():
        for ident in admin.identities():
            uid = ident.get_platform_uid()
            if ident.get_platform() == "tg" and str(uid).isdigit() and not ident.is_muted():
                out.append(int(uid))
    return out


async def forward_to_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое сообщение игрока пересылаем администраторам (как в исходном боте)."""
    tg = update.effective_user
    user = User.by_tg(str(tg.id))
    who = user.get_name() if user else (tg.username or str(tg.id))
    text = update.effective_message.text or ""
    admins = _admin_tg_chat_ids()
    for chat_id in admins:
        try:
            await context.bot.send_message(
                chat_id=chat_id, text=f"✉️ Сообщение от игрока {who}:\n{text}")
        except Exception as exc:  # noqa: BLE001 — не роняем бота из-за одного адресата
            log.warning("не удалось переслать админу %s: %s", chat_id, exc)
    admin_log.log(f"✉️ Игрок {who} написал боту: {text}")
    if not admins:
        await update.effective_message.reply_text(
            "Сообщение получено, но пока некому его переслать "
            "(нет администраторов в Telegram).")


def main():
    token = (config.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в config.py — бот не запущен.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, forward_to_admins))
    log.info("Telegram-бот запущен (long-polling). Ctrl+C для остановки.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
