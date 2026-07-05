"""«Входящие» для тестовых идентичностей (platform='test').

Вместо реального Telegram/VK тестовый канал складывает полученные сообщения в
обычный текстовый файл — по одному файлу на идентичность:

    data/inboxes/test-<uid>.txt

Формат записи — общий для всех журналов, см. core/msgfile.py. Веб-страница
/inbox/<uid> показывает те же сообщения карточками.
"""
import re
from pathlib import Path

from core import msgfile

BASE_DIR = Path(__file__).resolve().parent.parent
INBOX_DIR = BASE_DIR / "data" / "inboxes"

_PREFIX = "test-"


def _safe(uid) -> str:
    """Безопасное имя файла из uid (в dev это обычно число)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(uid)) or "unknown"


def inbox_path(uid) -> Path:
    return INBOX_DIR / f"{_PREFIX}{_safe(uid)}.txt"


def deliver_test(uid, text: str):
    """Дописать сообщение во «входящие» тестовой идентичности."""
    msgfile.append(inbox_path(uid), text)


def read_messages(uid):
    """Список сообщений (новые сверху): [{'ts': ..., 'body': ...}, ...]."""
    return msgfile.read(inbox_path(uid))


def list_inboxes():
    """uid'ы всех тестовых «входящих» с числом сообщений (для списка)."""
    if not INBOX_DIR.exists():
        return []
    result = []
    for p in sorted(INBOX_DIR.glob(f"{_PREFIX}*.txt")):
        uid = p.stem[len(_PREFIX):]
        result.append({"uid": uid, "count": len(msgfile.read(p))})
    return result


def clear_inbox(uid):
    p = inbox_path(uid)
    if p.exists():
        p.unlink()
