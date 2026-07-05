"""Вход по «магической ссылке» — одноразовые токены.

Как это работает:
  1. В чате бот по команде /start создаёт токен и присылает ссылку
     https://<сайт>/login?token=<случайные_256_бит>.
  2. Ссылка **одноразовая** (после первого успешного входа помечается использованной)
     и **короткоживущая** (по умолчанию 15 минут). Повторный переход по ней или
     переход по протухшей ссылке — отказ; нужно запросить /start заново.
  3. В БД хранится не сам токен, а его SHA-256-хеш: даже при утечке таблицы
     `login_token` действующие ссылки восстановить нельзя.
  4. При успешном входе токен «сгорает», а в подписанную cookie-сессию кладётся id
     пользователя (см. SessionMiddleware в app.py) — дальше сайт узнаёт игрока по ней.

Токен привязан к конкретному user.id, поэтому вход по ссылке = вход именно за того
пользователя, которому её отправил бот.
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from db import query_one, execute

DEFAULT_TTL_MINUTES = 15
_FMT = "%Y-%m-%d %H:%M:%S"  # тот же формат, что у SQLite CURRENT_TIMESTAMP (UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_login_token(user_id: int, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> str:
    """Создать одноразовый токен для user_id и вернуть его сырое значение (для ссылки)."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(minutes=ttl_minutes)).strftime(_FMT)
    execute(
        "INSERT INTO login_token (token, user_id, expires_at) VALUES (?, ?, ?)",
        (_hash(token), user_id, expires_at),
    )
    return token


def consume_token(token: str):
    """Проверить и «сжечь» одноразовый токен. Возвращает user_id или None."""
    if not token:
        return None
    row = query_one(
        "SELECT user_id, expires_at, used_at FROM login_token WHERE token = ?",
        (_hash(token),),
    )
    if row is None or row["used_at"] is not None:
        return None
    now = datetime.utcnow().strftime(_FMT)
    if row["expires_at"] < now:
        return None
    execute("UPDATE login_token SET used_at = ? WHERE token = ?", (now, _hash(token)))
    return row["user_id"]


# --- постоянная (переиспользуемая) ссылка, по одной на identity -------------

def set_permanent_token(identity_id: int) -> str:
    """Создать/перевыпустить постоянный токен для канала (старый перестаёт работать).

    Возвращает сырое значение — показать его нужно один раз, в БД хранится лишь хеш.
    """
    token = secrets.token_urlsafe(32)
    execute(
        "INSERT OR REPLACE INTO persistent_login (identity_id, token_hash) VALUES (?, ?)",
        (identity_id, _hash(token)),
    )
    return token


def resolve_permanent_token(token: str):
    """Вернуть user_id по постоянному токену канала (без «сжигания») или None."""
    if not token:
        return None
    row = query_one(
        "SELECT i.user_id AS user_id FROM persistent_login p "
        "JOIN identity i ON i.id = p.identity_id WHERE p.token_hash = ?",
        (_hash(token),),
    )
    return row["user_id"] if row else None


def has_permanent_token(identity_id: int) -> bool:
    return query_one(
        "SELECT 1 FROM persistent_login WHERE identity_id = ?", (identity_id,)
    ) is not None


def revoke_permanent_token(identity_id: int):
    execute("DELETE FROM persistent_login WHERE identity_id = ?", (identity_id,))
