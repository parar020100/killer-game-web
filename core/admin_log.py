"""Общий журнал администраторов — один текстовый файл на всю игру.

Аналог чата (core/chat.py), но не привязан к пользователю: сюда пишутся
значимые события игры (открытие/закрытие регистрации, старт/стоп, вход/выход
игроков, поимки и т.п.), и все админы видят один и тот же поток на странице
/admin-log.

    data/admin_log.txt

Формат записи — общий, см. core/msgfile.py. Отдельный per-identity флаг
identity.admin_log_enabled оставлен для будущей пуш-доставки этого журнала в
личные каналы TG/VK; сам файл фиксирует события всегда.
"""
from pathlib import Path

from core import msgfile

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "data" / "admin_log.txt"


def log(text: str):
    """Записать событие в общий журнал администраторов."""
    msgfile.append(LOG_PATH, text)


def read_messages():
    """Записи журнала (новые сверху)."""
    return msgfile.read(LOG_PATH)


def clear():
    if LOG_PATH.exists():
        LOG_PATH.unlink()
