"""«Входящие» для тестовых идентичностей (platform='test').

Вместо реального Telegram/VK тестовый канал складывает полученные сообщения в
обычный текстовый файл — по одному файлу на идентичность:

    data/inboxes/test-<uid>.txt

Файл человекочитаем (можно открыть в редакторе), а веб-страница /inbox/<uid>
показывает те же сообщения карточками. Формат одной записи:

    [2026-07-05 14:23:01]
    текст сообщения (может быть в несколько строк)
    <пустая строка-разделитель>
"""
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
INBOX_DIR = BASE_DIR / "data" / "inboxes"

_PREFIX = "test-"
_TS_RE = re.compile(r"^\[(.*?)\]$")


def _safe(uid) -> str:
    """Безопасное имя файла из uid (в dev это обычно число)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(uid)) or "unknown"


def inbox_path(uid) -> Path:
    return INBOX_DIR / f"{_PREFIX}{_safe(uid)}.txt"


def deliver_test(uid, text: str):
    """Дописать сообщение во «входящие» тестовой идентичности."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    with open(inbox_path(uid), "a", encoding="utf-8") as f:
        f.write(f"[{ts}]\n{body}\n\n")


def read_messages(uid):
    """Список сообщений (новые сверху): [{'ts': ..., 'body': ...}, ...]."""
    p = inbox_path(uid)
    if not p.exists():
        return []
    msgs = []
    for chunk in p.read_text(encoding="utf-8").split("\n\n"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        m = _TS_RE.match(lines[0])
        if m:
            msgs.append({"ts": m.group(1), "body": "\n".join(lines[1:]).strip()})
        else:
            msgs.append({"ts": "", "body": chunk})
    msgs.reverse()  # новые сверху
    return msgs


def list_inboxes():
    """uid'ы всех тестовых «входящих» с числом сообщений (для списка)."""
    if not INBOX_DIR.exists():
        return []
    result = []
    for p in sorted(INBOX_DIR.glob(f"{_PREFIX}*.txt")):
        uid = p.stem[len(_PREFIX):]
        result.append({"uid": uid, "count": len(read_messages(uid))})
    return result


def clear_inbox(uid):
    p = inbox_path(uid)
    if p.exists():
        p.unlink()
