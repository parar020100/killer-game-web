"""Модель идентичности — привязка профиля (user) к каналу связи.

Одна строка таблицы `identity` = один канал: Telegram или VK.
Идентичность знает, как **доставить** себе сообщение (deliver):
  * tg — пока эмулируется страницей чата (пишет в core/chat.py);
  * vk — заглушка (реальные боты подключим позже).
"""
from db import query_one, query_all, execute

PLATFORMS = ("tg", "vk")

PLATFORM_LABEL = {"tg": "Telegram", "vk": "VK"}
PLATFORM_ICON = {"tg": "✈️", "vk": "🅥"}


class Identity:
    def __init__(self, id: int):
        self.id = id

    # --- поиск / создание -------------------------------------------------

    @classmethod
    def by_id(cls, id):
        if id is None:
            return None
        row = query_one("SELECT id FROM identity WHERE id = ?", (id,))
        return cls(row["id"]) if row else None

    @classmethod
    def by_platform(cls, platform, platform_uid):
        row = query_one(
            "SELECT id FROM identity WHERE platform = ? AND platform_uid = ?",
            (platform, str(platform_uid)),
        )
        return cls(row["id"]) if row else None

    @classmethod
    def for_user(cls, user_id):
        rows = query_all(
            "SELECT id FROM identity WHERE user_id = ? ORDER BY id", (user_id,)
        )
        return [cls(r["id"]) for r in rows]

    @classmethod
    def create(cls, user_id, platform, platform_uid, username=None, name=None):
        new_id = execute(
            "INSERT INTO identity (user_id, platform, platform_uid, username, name) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, platform, str(platform_uid), username, name),
        )
        return cls(new_id)

    # --- служебное чтение/запись ------------------------------------------

    def _get(self, field: str):
        row = query_one(f"SELECT {field} FROM identity WHERE id = ?", (self.id,))
        return row[0] if row else None

    def _set(self, field: str, value):
        execute(f"UPDATE identity SET {field} = ? WHERE id = ?", (value, self.id))

    # --- поля -------------------------------------------------------------

    def get_user_id(self):       return self._get("user_id")
    def get_platform(self):      return self._get("platform")
    def get_platform_uid(self):  return self._get("platform_uid")
    def get_username(self):      return self._get("username")
    def get_name(self):          return self._get("name")

    def set_username(self, value): self._set("username", value)
    def set_name(self, value):     self._set("name", value)

    def is_muted(self) -> bool:            return bool(self._get("muted"))
    def set_muted(self, value: bool):      self._set("muted", int(value))

    def is_admin_log_enabled(self) -> bool:       return bool(self._get("admin_log_enabled"))
    def set_admin_log_enabled(self, value: bool): self._set("admin_log_enabled", int(value))

    def update_profile(self, username=None, name=None):
        """Освежить username/имя из платформы, не затирая на None."""
        if username is not None:
            self.set_username(username)
        if name is not None:
            self.set_name(name)

    # --- представление ----------------------------------------------------

    def label(self) -> str:
        platform = self.get_platform()
        icon = PLATFORM_ICON.get(platform, "•")
        handle = self.get_username() or self.get_name() or self.get_platform_uid()
        return f"{icon} {PLATFORM_LABEL.get(platform, platform)}: {handle}"

    # --- доставка сообщения в канал ---------------------------------------

    def deliver(self, text: str):
        platform = self.get_platform()
        uid = self.get_platform_uid()
        if platform == "tg":
            # Реальный Telegram: если platform_uid — числовой chat_id и задан токен,
            # шлём через Bot API. Иначе (эмуляция: uid = username) — в файл чата.
            if str(uid).isdigit():
                from core import tg_send
                if tg_send.send(uid, text):
                    return
            from core.chat import add_bot_message
            add_bot_message(uid, text)
        else:
            # Реальные боты (vk и т.п.) подключим на шаге уведомлений.
            print(f"[notify:{platform}] -> {uid}: {text}")
