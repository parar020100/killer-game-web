"""Технический лог ботов и процессов — отдельный файл на всю систему.

Отделён от игрового журнала (core/admin_log.py). Сюда стекается всё техническое:

  • весь stdout/stderr Telegram/VK-ботов (через супервизор run_all.py);
  • старт/остановка/перезапуск процессов (веб, боты) и команды управления;
  • входящие /start и /link, успешно отправленные уведомления;
  • ошибки доставки и API (они дублируются ещё и в игровой журнал — как п.42).
  • вехи скриптов start.sh / stop.sh.

    data/bot_log.txt

Формат — построчный: ``[ГГГГ-ММ-ДД ЧЧ:ММ:СС] [источник] строка``. Источник —
``web`` / ``tg`` / ``vk`` / ``run`` / ``start.sh`` / ``stop.sh`` / ``ERROR`` и т.п.
Файл человекочитаем; открывается сырым текстом на ``/bot-log/raw``.
"""
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "data" / "bot_log.txt"


def _write(text, source=None):
    """Дописать строку(и) с меткой времени и источником. Ошибки записи глушим —
    логирование не должно ронять доставку/процессы."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prefix = f"[{ts}] [{source}] " if source else f"[{ts}] "
        lines = str(text).rstrip("\n").splitlines() or [""]
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(prefix + ln + "\n")
    except Exception:
        pass


def log(text, source=None):
    """Записать техническое событие/строку вывода бота или процесса (только в лог бота)."""
    _write(text, source)


def error(text, source=None):
    """Ошибка: в лог бота И в игровой журнал (⚠️ [бот]), чтобы была видна админу там же."""
    _write(text, source or "ERROR")
    try:
        from core import admin_log
        admin_log.log(f"⚠️ [бот] {text}")
    except Exception:
        # Логирование не должно ронять доставку — молча игнорируем сбой записи.
        pass


def read_messages():
    """Строки лога, новые сверху (для совместимости; панели используют read_raw)."""
    if not LOG_PATH.exists():
        return []
    return list(reversed(LOG_PATH.read_text(encoding="utf-8").splitlines()))


def read_raw() -> str:
    """Сырой текст файла лога (для просмотра в браузере)."""
    return LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else ""


def clear():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
