"""Модель пользователя — доступ к таблице user.

Аналог user.py из бота, адаптированный под сквозной id + мультиплатформенные
tg_id / vk_id. Пока реализовано то, что нужно для регистрации и статуса;
цели/поимки/воскрешения добавятся на следующих шагах.
"""
from db import query_one, execute


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
    def by_tg(cls, tg_id):
        row = query_one("SELECT id FROM user WHERE tg_id = ?", (tg_id,))
        return cls(row["id"]) if row else None

    @classmethod
    def by_vk(cls, vk_id):
        row = query_one("SELECT id FROM user WHERE vk_id = ?", (vk_id,))
        return cls(row["id"]) if row else None

    @classmethod
    def get_or_create_by_tg(cls, tg_id, username=None, name=None):
        user = cls.by_tg(tg_id)
        if user:
            if username is not None:
                user._set("tg_username", username)
            if name is not None:
                user._set("tg_name", name)
            return user
        new_id = execute(
            "INSERT INTO user (tg_id, tg_username, tg_name) VALUES (?, ?, ?)",
            (tg_id, username, name),
        )
        return cls(new_id)

    @classmethod
    def get_or_create_by_vk(cls, vk_id, username=None, name=None):
        user = cls.by_vk(vk_id)
        if user:
            if username is not None:
                user._set("vk_username", username)
            if name is not None:
                user._set("vk_name", name)
            return user
        new_id = execute(
            "INSERT INTO user (vk_id, vk_username, vk_name) VALUES (?, ?, ?)",
            (vk_id, username, name),
        )
        return cls(new_id)

    # --- служебное чтение/запись ------------------------------------------

    def _get(self, field: str):
        row = query_one(f"SELECT {field} FROM user WHERE id = ?", (self.id,))
        return row[0] if row else None

    def _set(self, field: str, value):
        execute(f"UPDATE user SET {field} = ? WHERE id = ?", (value, self.id))

    # --- профиль / идентичность -------------------------------------------

    def get_tg_id(self):        return self._get("tg_id")
    def get_vk_id(self):        return self._get("vk_id")
    def get_tg_username(self):  return self._get("tg_username")
    def get_vk_username(self):  return self._get("vk_username")
    def get_tg_name(self):      return self._get("tg_name")
    def get_vk_name(self):      return self._get("vk_name")
    def get_real_name(self):    return self._get("real_name")
    def get_extra_info(self):   return self._get("extra_info")

    def set_real_name(self, value):  self._set("real_name", value)
    def set_extra_info(self, value): self._set("extra_info", value)

    def get_name(self) -> str:
        """Лучшее доступное имя для показа: игровое → из TG → из VK."""
        return (self.get_real_name() or self.get_tg_name()
                or self.get_vk_name() or f"Игрок #{self.id}")

    # --- роль / статус ----------------------------------------------------

    def is_admin(self) -> bool:  return bool(self._get("is_admin"))
    def is_player(self) -> bool: return bool(self._get("is_player"))
    def is_alive(self) -> bool:  return bool(self._get("is_alive"))
    def get_score(self) -> int:  return self._get("kill_count") or 0

    def set_admin(self, value: bool):  self._set("is_admin", int(value))
    def set_player(self, value: bool): self._set("is_player", int(value))
    def set_alive(self, value: bool):  self._set("is_alive", int(value))

    # --- уведомления (mute отдельно по платформам) ------------------------

    def is_tg_muted(self) -> bool: return bool(self._get("tg_muted"))
    def is_vk_muted(self) -> bool: return bool(self._get("vk_muted"))

    def set_tg_muted(self, value: bool): self._set("tg_muted", int(value))
    def set_vk_muted(self, value: bool): self._set("vk_muted", int(value))

    def is_muted(self, platform: str) -> bool:
        """platform ∈ {'tg', 'vk'} — заглушены ли игровые уведомления на платформе."""
        return {"tg": self.is_tg_muted, "vk": self.is_vk_muted}[platform]()

    def set_muted(self, platform: str, value: bool):
        {"tg": self.set_tg_muted, "vk": self.set_vk_muted}[platform](value)

    # ADMIN LOG — отдельная категория уведомлений, тоже по платформам
    def is_tg_admin_log_enabled(self) -> bool: return bool(self._get("tg_admin_log_enabled"))
    def is_vk_admin_log_enabled(self) -> bool: return bool(self._get("vk_admin_log_enabled"))

    def set_tg_admin_log_enabled(self, value: bool): self._set("tg_admin_log_enabled", int(value))
    def set_vk_admin_log_enabled(self, value: bool): self._set("vk_admin_log_enabled", int(value))

    def is_admin_log_enabled(self, platform: str) -> bool:
        return {"tg": self.is_tg_admin_log_enabled, "vk": self.is_vk_admin_log_enabled}[platform]()

    def set_admin_log_enabled(self, platform: str, value: bool):
        {"tg": self.set_tg_admin_log_enabled, "vk": self.set_vk_admin_log_enabled}[platform](value)

    # --- к какому каналу реально доставлять (для будущего registry) --------

    def _linked(self, platform: str):
        return self.get_tg_id() if platform == "tg" else self.get_vk_id()

    def wants_notifications(self, platform: str) -> bool:
        """Привязан ли аккаунт на платформе и не заглушены ли игровые уведомления."""
        return self._linked(platform) is not None and not self.is_muted(platform)

    def wants_admin_log(self, platform: str) -> bool:
        """Слать ли ADMIN LOG в этот канал (привязан, админ и лог включён)."""
        return (self._linked(platform) is not None
                and self.is_admin() and self.is_admin_log_enabled(platform))

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
