"""Фото-пруфы поимок: единое хранилище для сайта и ботов.

Раньше вся работа с фото жила в app.py, но боты (tg_bot.py / vk_bot.py) — отдельные
процессы и app.py импортировать не могут. Чтобы механика бота не разъезжалась с
сайтом, хранение вынесено сюда: одна папка (`data/photos/`) и общие функции поиска.

Имя файла фото-пруфа:
  • ``<hunter>__v<victimid>__<unixtime><ext>`` — новая схема, привязана к КОНКРЕТНОЙ
    жертве, чтобы у каждой поимки было своё фото (охотник ловит несколько целей —
    у каждой записи в логе свой снимок);
  • ``<hunter>_<unixtime><ext>`` — старая схема (жертва в имени не записана); такие
    фото сопоставляются с записью журнала по времени (см. `for_capture`).

``<hunter>`` — из username охотника (или ``id<N>``), очищен до ``[A-Za-z0-9_.-]``.
Новые имена тоже начинаются с ``<hunter>_``, поэтому старые выборки по префиксу их
по-прежнему находят.
"""
import re
import time

import db

# Расширение по MIME-типу загруженного файла (веб-форма) или скачанного из бота.
EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "image/gif": ".gif", "image/heic": ".heic"}

# Метка жертвы в имени файла новой схемы: __v<id>__<unixtime>.<ext>
_VID_RE = re.compile(r"__v(\d+)__\d+\.[^.]+$")


def photos_dir():
    return db.DATA_DIR / "photos"


def prefix(user) -> str:
    """Безопасный префикс имени файла фото-пруфа для игрока-«охотника»."""
    un = user.get_username() or f"id{user.id}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", un)


def _victim_id(name: str):
    """id жертвы из имени файла новой схемы или None (старая схема без метки)."""
    m = _VID_RE.search(name)
    return int(m.group(1)) if m else None


def _hunter_files(user):
    """Все фото охотника, по возрастанию времени (старые → новые). [] если нет папки."""
    if user is None:
        return []
    d = photos_dir()
    if not d.is_dir():
        return []
    return sorted(d.glob(f"{prefix(user)}_*"), key=lambda p: p.stat().st_mtime)


def save_bytes(user, data: bytes, ext: str = ".jpg", victim=None) -> bool:
    """Сохранить фото-пруф. `victim` (жертва) записывается в имя файла, чтобы позже
    отдать именно этот снимок для этой поимки, а не «последнее фото охотника»."""
    if not data:
        return False
    d = photos_dir()
    d.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    if victim is not None:
        name = f"{prefix(user)}__v{victim.id}__{stamp}{ext}"
    else:
        name = f"{prefix(user)}_{stamp}{ext}"
    (d / name).write_bytes(data)
    return True


def latest(user):
    """Путь к самому свежему фото-пруфу этого игрока (охотника) или None.

    Используется как признак «у охотника вообще есть фото» и как последний
    запасной вариант. Для конкретной поимки берите `for_capture`.
    """
    files = _hunter_files(user)
    return files[-1] if files else None


def for_capture(hunter, victim=None, ts=None):
    """Фото КОНКРЕТНОЙ поимки, а не «последнее фото охотника».

    Порядок выбора:
      1. новое фото с меткой этой жертвы в имени — новейшее из них;
      2. иначе (старые фото без метки) — ближайшее по времени к записи журнала
         (`ts` — момент записи в epoch-секундах); так каждая старая запись в
         активной игре получает свой снимок, а не общий «последний»;
      3. иначе — последнее доступное.
    """
    files = _hunter_files(hunter)
    if not files:
        return None
    if victim is not None:
        tagged = [p for p in files if _victim_id(p.name) == victim.id]
        if tagged:
            return tagged[-1]
    if ts is not None:
        untagged = [p for p in files if _victim_id(p.name) is None]
        pool = untagged or files
        return min(pool, key=lambda p: abs(p.stat().st_mtime - ts))
    return files[-1]


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
