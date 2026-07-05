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

# [время] или [время] тег  — тег опционален (используется чатом: bot / user)
_HEAD_RE = re.compile(r"^\[(.*?)\](?:\s+(\S+))?$")


def append(path: Path, text: str, tag: str = None):
    """Дописать запись с текущей меткой времени (и опц. тегом) в конец файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = f"[{ts}] {tag}" if tag else f"[{ts}]"
    body = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{head}\n{body}\n\n")


def read(path: Path):
    """Список записей, новые сверху: [{'ts':.., 'tag':.., 'body':..}, ...]."""
    if not path.exists():
        return []
    msgs = []
    for chunk in path.read_text(encoding="utf-8").split("\n\n"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        lines = chunk.split("\n")
        m = _HEAD_RE.match(lines[0])
        if m:
            msgs.append({"ts": m.group(1), "tag": m.group(2),
                         "body": "\n".join(lines[1:]).strip()})
        else:
            msgs.append({"ts": "", "tag": None, "body": chunk})
    msgs.reverse()
    return msgs
