"""Эмуляция переписки «бот ↔ пользователь».

Пока настоящих Telegram/VK нет, диалог с ботом эмулируется веб-страницей и
хранится в текстовом файле — по одному на пользователя (по его username):

    data/chats/<username>.txt

Каждая запись помечена отправителем (`bot` / `user`), формат — общий, см.
core/msgfile.py. Именно сюда бот «пишет» уведомления игры и ссылку на вход.
"""
import re
from pathlib import Path

from core import msgfile

BASE_DIR = Path(__file__).resolve().parent.parent
CHAT_DIR = BASE_DIR / "data" / "chats"


def normalize(username) -> str:
    """Привести username к канону: нижний регистр, только [a-z0-9_.-]."""
    u = str(username).strip().lstrip("@").lower()
    return re.sub(r"[^a-z0-9_.-]", "", u)


def chat_path(username) -> Path:
    return CHAT_DIR / f"{normalize(username)}.txt"


# Токен входа (?token=... / login?token=...) — это учётные данные. В историю
# переписки на диск он попадать НЕ должен: маскируем перед сохранением. Реальную
# ссылку показываем пользователю один раз (см. app.chat_start), в файл не пишем.
_TOKEN_RE = re.compile(r"(token=)[A-Za-z0-9_\-]+", re.IGNORECASE)


def redact_tokens(text: str) -> str:
    return _TOKEN_RE.sub(r"\1<скрыто>", str(text))


def add_bot_message(username, text: str):
    msgfile.append(chat_path(username), redact_tokens(text), tag="bot")


def add_user_message(username, text: str):
    msgfile.append(chat_path(username), redact_tokens(text), tag="user")


def read(username):
    """Сообщения в хронологическом порядке (старые сверху) — как в мессенджере."""
    return list(reversed(msgfile.read(chat_path(username))))


def clear(username):
    p = chat_path(username)
    if p.exists():
        p.unlink()


def list_chats():
    if not CHAT_DIR.exists():
        return []
    return sorted(p.stem for p in CHAT_DIR.glob("*.txt"))
