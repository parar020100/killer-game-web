"""Общий формат «журнала сообщений» в текстовом файле.

Используется и «входящими» тестовых идентичностей (core/inbox.py), и общим
журналом администраторов (core/admin_log.py). Одна запись:

    [2026-07-05 14:23:01]
    текст сообщения (может быть в несколько строк)
    <пустая строка-разделитель>

Файл человекочитаем (можно открыть в редакторе), а веб-страница показывает те
же записи карточками, новые сверху.
"""
import re
from datetime import datetime
from pathlib import Path

_TS_RE = re.compile(r"^\[(.*?)\]$")


def append(path: Path, text: str):
    """Дописать запись с текущей меткой времени в конец файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{ts}]\n{body}\n\n")


def read(path: Path):
    """Список записей, новые сверху: [{'ts': ..., 'body': ...}, ...]."""
    if not path.exists():
        return []
    msgs = []
    for chunk in path.read_text(encoding="utf-8").split("\n\n"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        m = _TS_RE.match(lines[0])
        if m:
            msgs.append({"ts": m.group(1), "body": "\n".join(lines[1:]).strip()})
        else:
            msgs.append({"ts": "", "body": chunk})
    msgs.reverse()
    return msgs
