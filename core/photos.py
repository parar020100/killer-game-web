"""Фото-пруфы поимок: единое хранилище для сайта и ботов.

Раньше вся работа с фото жила в app.py, но боты (tg_bot.py / vk_bot.py) — отдельные
процессы и app.py импортировать не могут. Чтобы механика бота не разъезжалась с
сайтом, хранение вынесено сюда: одна папка (`data/photos/`), одна схема имён файлов
(`<username|idN>_<unixtime>.<ext>`), одни и те же функции поиска.
"""
import re
import time

import db

# Расширение по MIME-типу загруженного файла (веб-форма) или скачанного из бота.
EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/heic": ".heic"}


def photos_dir():
    return db.DATA_DIR / "photos"


def prefix(user) -> str:
    """Безопасный префикс имени файла фото-пруфа для игрока-«охотника»."""
    un = user.get_username() or f"id{user.id}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", un)


def save_bytes(user, data: bytes, ext: str = ".jpg") -> bool:
    """Сохранить фото-пруф из произвольного источника (бот). True при успехе."""
    if not data:
        return False
    d = photos_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{prefix(user)}_{int(time.time())}{ext}").write_bytes(data)
    return True


def latest(user):
    """Путь к самому свежему фото-пруфу этого игрока (охотника) или None."""
    if user is None:
        return None
    d = photos_dir()
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"{prefix(user)}_*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def hunters() -> dict:
    """{имя игрока: uid} для тех, у кого есть сохранённый фото-пруф поимки.

    Используется, чтобы в записях журнала о поимке/заявке показать кнопку «Открыть
    фото» (в т.ч. у старых записей). Директорию фото читаем один раз."""
    from core.user import User
    d = photos_dir()
    if not d.is_dir():
        return {}
    names = [p.name for p in d.iterdir() if p.is_file()]
    out = {}
    for u in User.all():
        pref = prefix(u) + "_"
        if any(n.startswith(pref) for n in names):
            out[u.get_name()] = u.id
    return out
