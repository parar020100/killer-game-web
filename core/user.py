"""Модель пользователя — профиль и игровое состояние (таблица user).

Всё, что относится к каналу связи (tg/vk, username, имя, mute, ADMIN LOG),
живёт в таблице `identity` и модели Identity. У одного пользователя может быть
несколько идентичностей; уведомления доставляются в каждую неприглушённую.
"""
from db import query_one, query_all, execute
from core.identity import Identity

# Пользователи с этими username автоматически становятся админами при создании
# (аналог DEFAULT_ADMINS из бота).
DEFAULT_ADMINS = {"parar020100"}


def _is_default_admin(username) -> bool:
    return bool(username) and str(username).strip().lstrip("@").lower() in DEFAULT_ADMINS


class User:
    def __init__(self, id: int):
        self.id = id

    # --- поиск / создание -------------------------------------------------

    @classmethod
    def by_id(cls, id):
        if id is None:
            return None
        row = query_one("SELECT id FROM user WHERE id = ?", (id,))
        return cls(row["id"]) if row else None

    @classmethod
    def by_identity(cls, platform, platform_uid):
        ident = Identity.by_platform(platform, platform_uid)
        return cls(ident.get_user_id()) if ident else None

    @classmethod
    def get_or_create_by_identity(cls, platform, platform_uid, username=None, name=None):
        """Найти пользователя по идентичности или создать новый профиль с ней."""
        ident = Identity.by_platform(platform, platform_uid)
        if ident:
            ident.update_profile(username, name)
            user = cls(ident.get_user_id())
        else:
            new_id = execute("INSERT INTO user DEFAULT VALUES")
            Identity.create(new_id, platform, platform_uid, username, name)
            user = cls(new_id)
        # Дефолт-админ: назначаем права по username (идемпотентно).
        if _is_default_admin(username) and not user.is_admin():
            user.set_admin(True)
        return user

    # тонкие обёртки для конкретных платформ
    @classmethod
    def by_tg(cls, tg_id):
        return cls.by_identity("tg", tg_id)

    @classmethod
    def by_vk(cls, vk_id):
        return cls.by_identity("vk", vk_id)

    @classmethod
    def get_or_create_by_tg(cls, tg_id, username=None, name=None):
        return cls.get_or_create_by_identity("tg", tg_id, username, name)

    @classmethod
    def get_or_create_by_vk(cls, vk_id, username=None, name=None):
        return cls.get_or_create_by_identity("vk", vk_id, username, name)

    # --- выборки ----------------------------------------------------------

    @classmethod
    def all(cls):
        return [cls(r["id"]) for r in query_all("SELECT id FROM user ORDER BY id")]

    @classmethod
    def all_players(cls):
        return [cls(r["id"]) for r in
                query_all("SELECT id FROM user WHERE is_player = 1 ORDER BY id")]

    @classmethod
    def all_admins(cls):
        return [cls(r["id"]) for r in
                query_all("SELECT id FROM user WHERE is_admin = 1 ORDER BY id")]

    # --- служебное чтение/запись ------------------------------------------

    def _get(self, field: str):
        row = query_one(f"SELECT {field} FROM user WHERE id = ?", (self.id,))
        return row[0] if row else None

    def _set(self, field: str, value):
        execute(f"UPDATE user SET {field} = ? WHERE id = ?", (value, self.id))

    # --- идентичности / каналы --------------------------------------------

    def identities(self):
        return Identity.for_user(self.id)

    def identity(self, platform):
        for ident in self.identities():
            if ident.get_platform() == platform:
                return ident
        return None

    # --- профиль ----------------------------------------------------------

    def get_real_name(self):    return self._get("real_name")
    def get_extra_info(self):   return self._get("extra_info")

    def set_real_name(self, value):  self._set("real_name", value)
    def set_extra_info(self, value): self._set("extra_info", value)

    def get_name(self) -> str:
        """Лучшее доступное имя: игровое → из любой привязанной платформы."""
        rn = self.get_real_name()
        if rn:
            return rn
        for ident in self.identities():
            nm = ident.get_name()
            if nm:
                return nm
        return f"Игрок #{self.id}"

    def get_username(self):
        """Первый доступный username из привязанных каналов (@nick)."""
        for ident in self.identities():
            un = ident.get_username()
            if un:
                return un
        return None

    # --- роль / статус ----------------------------------------------------

    def is_admin(self) -> bool:  return bool(self._get("is_admin"))
    def is_player(self) -> bool: return bool(self._get("is_player"))
    def is_alive(self) -> bool:  return bool(self._get("is_alive"))
    def get_score(self) -> int:  return self._get("kill_count") or 0

    def get_game_order(self):    return self._get("game_order")
    def get_target(self):        return self._get("target")
    def get_killed_by(self):     return self._get("killed_by")

    def is_queued_for_revival(self) -> bool:
        return query_one(
            "SELECT 1 FROM revive_queue WHERE user_id = ? LIMIT 1", (self.id,)
        ) is not None

    def set_admin(self, value: bool):  self._set("is_admin", int(value))
    def set_player(self, value: bool): self._set("is_player", int(value))
    def set_alive(self, value: bool):  self._set("is_alive", int(value))

    # --- уведомления ------------------------------------------------------

    def notify(self, text: str):
        """Доставить сообщение во все неприглушённые каналы пользователя."""
        for ident in self.identities():
            if not ident.is_muted():
                ident.deliver(text)

    # --- регистрация в игре (упрощённо) -----------------------------------

    def join(self, alive: bool = True):
        self.set_player(True)
        self.set_alive(alive)
        self._set("kill_count", 0)

    def leave(self):
        self.set_player(False)
        self.set_alive(False)
        self._set("kill_count", 0)
        self._set("game_order", None)
        self._set("target", None)
        self._set("killed_by", None)
