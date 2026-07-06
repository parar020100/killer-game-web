"""Вход по «магической ссылке» — постоянные (переиспользуемые) токены.

Как это работает:
  1. В чате бот по команде /start выдаёт ссылку
     https://<сайт>/login?token=<случайные_256_бит>.
  2. Ссылка **постоянная**: работает всегда, не сгорает и не имеет срока. Одна ссылка
     на КАНАЛ (identity) — у пользователя может быть отдельная ссылка для Telegram и VK.
  3. В БД хранится не сам токен, а его SHA-256-хеш: показать ссылку можно один раз,
     при утечке таблицы `persistent_login` действующие ссылки восстановить нельзя.
     Повторный /start перевыпускает токен (старая ссылка перестаёт работать).
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

    Возвращает сырое значение — показать его нужно один раз, в БД хранится лишь хеш.
    """
    token = secrets.token_urlsafe(LOGIN_TOKEN_BYTES)
    # ON CONFLICT работает и в sqlite (3.24+), и в postgres — перевыпуск токена канала.
    execute(
        "INSERT INTO persistent_login (identity_id, token_hash) VALUES (?, ?) "
        "ON CONFLICT(identity_id) DO UPDATE SET token_hash = excluded.token_hash",
        (identity_id, _hash(token)),
    )
    return token


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
