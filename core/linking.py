"""Привязка второго канала (Telegram ↔ VK) к одному пользователю — и обратное.

Идея: у одного человека может быть несколько identity (tg и vk). Их можно
**объединить** под одним пользователем или держать **раздельно**.

Объединение — через одноразовый код:
  1. Пользователь на сайте (уже вошедший как T) получает код привязки.
  2. Отправляет боту другой платформы `/link <code>`.
  3. Бот переносит свой identity (канал отправителя) на пользователя T
     (`link_identity`). Если исходный «пустой» аккаунт (без игры и прав) остаётся
     без каналов — он удаляется.

Разъединение (`unlink_identity`) — отвязать канал в отдельного пользователя
(создаётся новый пустой аккаунт, к нему переезжает канал вместе со своей ссылкой
входа).
"""
import secrets
import string

from db import query_one, execute
from core.identity import Identity
from core.user import User

# Код без похожих символов (0/O, 1/I/L) — легче продиктовать.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 6


def create_code(user_id: int) -> str:
    """Выдать одноразовый код привязки для пользователя (старый — заменяется)."""
    execute("DELETE FROM link_code WHERE user_id = ?", (user_id,))
    while True:
        code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
        if query_one("SELECT 1 FROM link_code WHERE code = ?", (code,)) is None:
            break
    execute("INSERT INTO link_code (code, user_id) VALUES (?, ?)", (code, user_id))
    return code


def _resolve_code(code: str):
    code = (code or "").strip().upper()
    if not code:
        return None
    row = query_one("SELECT user_id FROM link_code WHERE code = ?", (code,))
    return row["user_id"] if row else None


def link_by_code(code: str, identity: Identity):
    """Привязать канал `identity` к пользователю, которому принадлежит код.

    Возвращает (ok, сообщение). Код одноразовый — при успехе удаляется.
    """
    target_id = _resolve_code(code)
    if target_id is None:
        return False, "Код привязки не найден или уже использован."
    target = User.by_id(target_id)
    if target is None:
        execute("DELETE FROM link_code WHERE code = ?", ((code or "").strip().upper(),))
        return False, "Аккаунт для привязки не найден."
    ok, msg = link_identity(identity, target_id)
    if ok:
        execute("DELETE FROM link_code WHERE code = ?", ((code or "").strip().upper(),))
    return ok, msg


def link_identity(identity: Identity, target_user_id: int):
    """Перенести identity на target-пользователя (объединение). (ok, сообщение).

    Чтобы не потерять игровые данные, отказываемся объединять, если исходный
    аккаунт канала сам участвует в игре или является админом.
    """
    source_id = identity.get_user_id()
    if source_id == target_user_id:
        return False, "Этот канал уже привязан к вашему аккаунту."
    source = User.by_id(source_id)
    if source is not None and (source.is_player() or source.is_admin()):
        return (False,
                "У аккаунта этого канала уже есть игровая история или права — "
                "привязка отменена, чтобы не потерять данные. Сначала выйдите из "
                "игры этим каналом или используйте его отдельно.")
    identity.set_user_id(target_user_id)
    # Если исходный аккаунт остался без каналов — убрать «пустышку».
    if source is not None and not Identity.for_user(source_id):
        execute("DELETE FROM user WHERE id = ?", (source_id,))
    target = User.by_id(target_user_id)
    label = identity.label()
    return True, f"✅ Канал привязан к аккаунту «{target.get_name()}»: {label}"


def unlink_identity(identity: Identity) -> int:
    """Отвязать канал в отдельного нового пользователя. Возвращает id нового user."""
    new_id = execute("INSERT INTO user DEFAULT VALUES")
    identity.set_user_id(new_id)
    return new_id
