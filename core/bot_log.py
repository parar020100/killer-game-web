"""Технический лог ботов (Telegram/VK) — отдельный файл на всю систему.

Отделён от игрового журнала (core/admin_log.py): сюда пишутся технические события
и ошибки ботов (сбои доставки уведомлений, ошибки API и т.п.). Ошибки при этом
дублируются и в игровой журнал (admin_log), чтобы админ видел их там же, где
игровые события — как просили в TODO п.42.

    data/bot_log.txt

Формат записи — общий, см. core/msgfile.py.
"""
from pathlib import Path

from core import msgfile

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "data" / "bot_log.txt"


def log(text: str):
    """Записать техническое событие бота (только в лог бота)."""
    msgfile.append(LOG_PATH, text)


def error(text: str):
    """Ошибка бота: в лог бота И в игровой журнал (чтобы была видна админу там же)."""
    msgfile.append(LOG_PATH, text)
    try:
        from core import admin_log
        admin_log.log(f"⚠️ [бот] {text}")
    except Exception:
        # Логирование не должно ронять доставку — молча игнорируем сбой записи.
        pass


def read_messages():
    """Записи лога (новые сверху)."""
    return msgfile.read(LOG_PATH)


def read_raw() -> str:
    """Сырой текст файла лога (для просмотра в браузере)."""
    return LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""


def clear():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
