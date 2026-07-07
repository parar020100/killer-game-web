"""Настройки игры, редактируемые из UI и хранящиеся в базе данных.

Раньше эти параметры жили в config.py (SUPPORT_CONTACT, RULES_FILENAME, EXTRA_INFO,
CONFIRM_KILLS). Теперь они — в таблице `setting` (ключ-значение), чтобы организатор
менял их прямо на странице «⚙️ Настройки игры», а не правил код. Если ключа в БД нет,
берётся значение по умолчанию из этого модуля.

Формат доп. вопросов — список пар [короткая_метка, текст_вопроса].
"""
import json

from db import query_one, execute

# --- значения по умолчанию (первый запуск / после полного сброса) ----------
DEFAULT_SUPPORT_CONTACT = "@parar020100"
# По умолчанию правила НЕ заданы — админ обязан выбрать файл (TODO 89). Существующим
# играм это не меняет поведение: для БД, созданных раньше, старое значение
# добивается в таблицу setting миграцией db._migrate_default_rules (см. db.py).
DEFAULT_RULES_FILENAME = ""
LEGACY_RULES_FILENAME = "rules/un2026_1.html"  # прежний дефолт (для миграции)
DEFAULT_CONFIRM_KILLS = True
DEFAULT_PHOTO_PROOF = True  # требовать фото-пруф поимки по умолчанию (TODO 88)
DEFAULT_GAME_MODE = "paparazzi"  # 'killer' | 'paparazzi' (оформление игры)


def _raw(key):
    row = query_one("SELECT value FROM setting WHERE key = ?", (key,))
    return row[0] if row else None


def _set_raw(key, value):
    execute("INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


# --- контакт поддержки ------------------------------------------------------

def support_contact() -> str:
    v = _raw("support_contact")
    return v if v is not None else DEFAULT_SUPPORT_CONTACT


def set_support_contact(value: str):
    _set_raw("support_contact", (value or "").strip())


# --- приветствие бота (пустой экран диалога) --------------------------------

def bot_intro():
    """Кастомный текст приветствия пустого экрана диалога или None, если не задан.

    Если None (или пусто) — используется сгенерированный `botcommon.intro_text()`
    (домен + ссылка на правила). Задаётся на странице «⚙️ Настройки игры» и
    применяется к описанию Telegram-бота; в VK его вставляют вручную."""
    v = _raw("bot_intro")
    return v if v else None


def set_bot_intro(value: str):
    _set_raw("bot_intro", (value or "").strip())


# --- файл правил (HTML) -----------------------------------------------------

def rules_filename() -> str:
    v = _raw("rules_filename")
    return v if v is not None else DEFAULT_RULES_FILENAME


def set_rules_filename(value: str):
    _set_raw("rules_filename", (value or "").strip())


# --- подтверждение поимок жертвой -------------------------------------------

def confirm_kills() -> bool:
    v = _raw("confirm_kills")
    return DEFAULT_CONFIRM_KILLS if v is None else v == "1"


def set_confirm_kills(on: bool):
    _set_raw("confirm_kills", "1" if on else "0")


# --- фото-пруф поимки -------------------------------------------------------

def photo_proof() -> bool:
    v = _raw("photo_proof")
    return DEFAULT_PHOTO_PROOF if v is None else v == "1"


def set_photo_proof(on: bool):
    _set_raw("photo_proof", "1" if on else "0")


# --- доп. вопросы при регистрации -------------------------------------------

def _normalize_pairs(raw) -> list:
    """Привести значение к списку пар [метка, вопрос]."""
    if not raw:
        return []
    if len(raw) == 2 and isinstance(raw[0], str) and isinstance(raw[1], str):
        return [[raw[0], raw[1]]]
    pairs = []
    for item in raw:
        if item and len(item) >= 2 and str(item[0]).strip() and str(item[1]).strip():
            pairs.append([str(item[0]).strip(), str(item[1]).strip()])
    return pairs


def extra_questions() -> list:
    v = _raw("extra_questions")
    if v is None:
        return []
    try:
        return _normalize_pairs(json.loads(v))
    except ValueError:
        return []


def set_extra_questions(pairs):
    _set_raw("extra_questions", json.dumps(_normalize_pairs(pairs), ensure_ascii=False))


# --- оформление игры («Киллер» / «Папарацци») -------------------------------

def game_mode() -> str:
    v = _raw("game_mode")
    return v if v in ("killer", "paparazzi") else DEFAULT_GAME_MODE


def set_game_mode(value: str):
    _set_raw("game_mode", value if value in ("killer", "paparazzi") else DEFAULT_GAME_MODE)


# --- root-пользователь (замена DEFAULT_ADMINS) ------------------------------

def root_user_id():
    v = _raw("root_user_id")
    return int(v) if v is not None and str(v).isdigit() else None


def set_root_user_id(uid: int):
    _set_raw("root_user_id", int(uid))
