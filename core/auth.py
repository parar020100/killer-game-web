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
    """Проверить и «сжечь» токен. Возвращает user_id или None, если недействителен."""
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
