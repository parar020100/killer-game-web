"""Общий формат «журнала сообщений» в текстовом файле.

Используется и чатом с ботом (core/chat.py), и общим журналом администраторов
(core/admin_log.py). Одна запись:

    [2026-07-05 14:23:01]
    текст сообщения (может быть в несколько строк)
    <пустая строка-разделитель>

Файл человекочитаем (можно открыть в редакторе), а веб-страница показывает те
же записи карточками, новые сверху.
"""
import re
from datetime import datetime
from pathlib import Path

# Строка-заголовок записи: [YYYY-MM-DD HH:MM:SS] с опциональным тегом (bot / user).
# Формат метки времени строгий — чтобы такая же строка ВНУТРИ сообщения (например в
# рассылке) не была принята за начало новой записи.
_HEAD_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\](?:\s+(\S+))?$")


def append(path: Path, text: str, tag: str = None):
    """Дописать запись с текущей меткой времени (и опц. тегом) в конец файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head = f"[{ts}] {tag}" if tag else f"[{ts}]"
    body = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{head}\n{body}\n\n")


def read(path: Path):
    """Список записей, новые сверху: [{'ts':.., 'tag':.., 'body':..}, ...].

    Записи режутся по строкам-заголовкам `[время]`, а НЕ по пустым строкам — иначе
    сообщение с пустой строкой внутри (например рассылка) разрывалось бы на части.
    """
    if not path.exists():
        return []
    records = []          # (ts, tag, [строки тела])
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = _HEAD_RE.match(line)
        if m:
            records.append([m.group(1), m.group(2), []])
        elif records:
            records[-1][2].append(line)
        # строки до первого заголовка (маловероятно) игнорируем
    msgs = [{"ts": ts, "tag": tag, "body": "\n".join(body).strip("\n")}
            for ts, tag, body in records]
    msgs.reverse()
    return msgs
