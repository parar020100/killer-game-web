"""Вход по «магической ссылке» — постоянные (переиспользуемые) токены.

Как это работает:
  1. В чате бот по команде /start выдаёт ссылку
     https://<сайт>/login?token=<случайные_256_бит>.
  2. Ссылка работает всегда, не сгорает и не имеет срока. Одна ссылка на КАНАЛ
     (identity) — у пользователя может быть отдельная ссылка для Telegram и VK.
  3. Повторный `/start` НЕ сбрасывает ссылку — показывает ту же (см.
     `botcommon.login_link`). Сменить/отозвать ссылку можно в «Настройках профиля»
     (revoke → следующий `/start` выдаёт новую). Храним и хеш (для проверки), и сырой
     токен `token_plain` (чтобы ссылку можно было показать повторно).
  4. При входе по ссылке id пользователя кладётся в подписанную cookie-сессию
     (см. SessionMiddleware в app.py) — дальше сайт узнаёт игрока по ней.

Токен привязан к конкретному каналу, поэтому вход по ссылке = вход за пользователя,
которому принадлежит этот канал.
"""
import hashlib
import secrets

from db import query_one, execute
from config import LOGIN_TOKEN_BYTES


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- постоянная (переиспользуемая) ссылка, по одной на identity -------------

def set_permanent_token(identity_id: int) -> str:
    """Создать/перевыпустить постоянный токен для канала (старый перестаёт работать).

    Возвращает сырое значение. Кроме хеша (для проверки) храним и сырой токен
    (`token_plain`), чтобы ссылку можно было ПОКАЗАТЬ повторно и переиспользовать —
    напр. кнопкой «Открыть меню игры» без генерации новой ссылки (TODO 76). Хранение
    допустимо для этой игры (локальные мероприятия, без персональных данных); отозвать
    ссылку можно в «Настройках профиля».
    """
    token = secrets.token_urlsafe(LOGIN_TOKEN_BYTES)
    # ON CONFLICT работает и в sqlite (3.24+), и в postgres — перевыпуск токена канала.
    execute(
        "INSERT INTO persistent_login (identity_id, token_hash, token_plain) "
        "VALUES (?, ?, ?) ON CONFLICT(identity_id) DO UPDATE SET "
        "token_hash = excluded.token_hash, token_plain = excluded.token_plain",
        (identity_id, _hash(token), token),
    )
    return token


def get_permanent_token(identity_id: int):
    """Сырой постоянный токен канала (для повторного показа ссылки) или None.

    None — если токена ещё нет ИЛИ он выдан в старой схеме (хранился лишь хеш): тогда
    для стабильной ссылки нужен перевыпуск (см. get_or_create_permanent_token)."""
    row = query_one(
        "SELECT token_plain FROM persistent_login WHERE identity_id = ?", (identity_id,))
    return row["token_plain"] if row else None


def get_or_create_permanent_token(identity_id: int):
    """Вернуть постоянный токен канала, переиспользуя существующий; создать при отсутствии.

    Если строки нет — создаём новый токен. Если строка есть, но `token_plain` пуст
    (легаси-запись только с хешем) — НЕ перевыпускаем молча (чтобы не сломать уже
    выданную ссылку), возвращаем None; стабильная ссылка появится после следующего
    /start. Возвращает сырой токен или None."""
    row = query_one(
        "SELECT token_plain FROM persistent_login WHERE identity_id = ?", (identity_id,))
    if row is None:
        return set_permanent_token(identity_id)
    return row["token_plain"]


def resolve_permanent_token(token: str):
    """Вернуть user_id по постоянному токену канала (без «сжигания») или None."""
    row = resolve_permanent_token_row(token)
    return row["user_id"] if row else None


def resolve_permanent_token_row(token: str):
    """Вернуть строку (user_id, platform, username) по токену или None.

    Платформа нужна, чтобы вход по ссылке из VK/TG «привязывал» вкладку в dev-режиме
    к каналу именно этой платформы (ники на разных платформах могут совпадать).
    """
    if not token:
        return None
    return query_one(
        "SELECT i.user_id AS user_id, i.platform AS platform, i.username AS username "
        "FROM persistent_login p JOIN identity i ON i.id = p.identity_id "
        "WHERE p.token_hash = ?",
        (_hash(token),),
    )


def has_permanent_token(identity_id: int) -> bool:
    return query_one(
        "SELECT 1 FROM persistent_login WHERE identity_id = ?", (identity_id,)
    ) is not None


def revoke_permanent_token(identity_id: int):
    execute("DELETE FROM persistent_login WHERE identity_id = ?", (identity_id,))


# --- гранты входа (пер-браузерный выход, устойчивый к гонке cookie) ---------
# Каждый вход по ссылке создаёт «грант» — случайный id, который кладётся в cookie
# этого браузера рядом с uid. Доступ действителен, только пока грант есть в таблице.
# «Выйти» удаляет грант ЭТОГО браузера → все его вкладки теряют доступ (даже если
# cookie «воскресает» гонкой перезаписи), а гранты других устройств не затрагиваются.

def create_grant(user_id: int) -> str:
    gid = secrets.token_urlsafe(LOGIN_TOKEN_BYTES)
    execute("INSERT INTO auth_grant (grant_id, user_id) VALUES (?, ?)", (gid, user_id))
    return gid


def grant_valid(grant_id: str, user_id: int) -> bool:
    """True, если грант существует и принадлежит этому пользователю."""
    if not grant_id:
        return False
    return query_one(
        "SELECT 1 FROM auth_grant WHERE grant_id = ? AND user_id = ?",
        (grant_id, user_id),
    ) is not None


def revoke_grant(grant_id: str):
    if grant_id:
        execute("DELETE FROM auth_grant WHERE grant_id = ?", (grant_id,))
