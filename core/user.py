"""Модель пользователя — профиль и игровое состояние (таблица user).

Всё, что относится к каналу связи (tg/vk, username, имя, mute, ADMIN LOG),
живёт в таблице `identity` и модели Identity. У одного пользователя может быть
несколько идентичностей; уведомления доставляются в каждую неприглушённую.
"""
import random

from db import query_one, query_all, execute
from core.identity import Identity
from config import DEFAULT_ADMINS, CONFIRM_KILLS


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

    @classmethod
    def non_players(cls):
        return [cls(r["id"]) for r in
                query_all("SELECT id FROM user WHERE is_player = 0 ORDER BY id")]

    @classmethod
    def alive_players(cls):
        return [cls(r["id"]) for r in query_all(
            "SELECT id FROM user WHERE is_player = 1 AND is_alive = 1 "
            "ORDER BY game_order IS NULL, game_order, id")]

    @classmethod
    def dead_players(cls):
        return [cls(r["id"]) for r in query_all(
            "SELECT id FROM user WHERE is_player = 1 AND is_alive = 0 ORDER BY id")]

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
    def is_default_admin(self) -> bool:
        """Дефолт-админ (из DEFAULT_ADMINS) — права нельзя снять."""
        return _is_default_admin(self.get_username())
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

    # --- счёт поимок ------------------------------------------------------

    def set_score(self, value: int):   self._set("kill_count", value)
    def increment_score(self):
        execute("UPDATE user SET kill_count = kill_count + 1 WHERE id = ?", (self.id,))
    def decrement_score(self):
        execute("UPDATE user SET kill_count = kill_count - 1 WHERE id = ?", (self.id,))

    # --- круг целей: порядок в круге --------------------------------------
    # Круг задаётся возрастанием game_order (с заворотом). Цель игрока — следующий
    # живой по кругу, охотник — предыдущий живой. Как в боте (user.py).

    def get_game_order_raw(self):
        return self._get("game_order")

    def set_raw_game_order(self, value):
        self._set("game_order", int(value) if value is not None else None)

    @classmethod
    def by_game_order(cls, value):
        row = query_one("SELECT id FROM user WHERE game_order = ?", (value,))
        return cls(row["id"]) if row else None

    @classmethod
    def _next_order_no_loop(cls, current):
        row = query_one(
            "SELECT game_order FROM user WHERE is_player = 1 AND game_order > ? "
            "ORDER BY game_order ASC LIMIT 1", (current,))
        return row["game_order"] if row else None

    def set_game_order(self, value, increase=True):
        """Задать позицию; при конфликте (уже занята) сдвинуть в свободную щель."""
        if value is not None:
            existing = User.by_game_order(value)
            if existing is not None and existing.id != self.id:
                if not increase:
                    return None
                nxt = User._next_order_no_loop(value) or (value + 100000)
                value = (value + nxt) // 2
                existing = User.by_game_order(value)
                while existing is not None and existing.id != self.id:
                    value += 1
                    existing = User.by_game_order(value)
        self.set_raw_game_order(value)
        return value

    def randomize_game_order(self):
        value = None
        while not value:
            value = self.set_game_order(random.randint(1, 1_000_000), increase=False)
        return value

    @classmethod
    def find_next_alive_order(cls, current):
        row = query_one(
            "SELECT game_order, id FROM user WHERE is_player = 1 AND is_alive = 1 "
            "AND game_order > ? ORDER BY game_order ASC LIMIT 1", (current,))
        if not row:
            row = query_one(
                "SELECT game_order, id FROM user WHERE is_player = 1 AND is_alive = 1 "
                "ORDER BY game_order ASC LIMIT 1")
        return (row["game_order"], cls(row["id"])) if row else (None, None)

    @classmethod
    def find_prev_alive_order(cls, current):
        row = query_one(
            "SELECT game_order, id FROM user WHERE is_player = 1 AND is_alive = 1 "
            "AND game_order < ? ORDER BY game_order DESC LIMIT 1", (current,))
        if not row:
            row = query_one(
                "SELECT game_order, id FROM user WHERE is_player = 1 AND is_alive = 1 "
                "ORDER BY game_order DESC LIMIT 1")
        return (row["game_order"], cls(row["id"])) if row else (None, None)

    def find_victim(self):
        order = self.get_game_order_raw()
        if order is None:
            return None
        _, user = User.find_next_alive_order(order)
        return user

    def find_killer(self):
        order = self.get_game_order_raw()
        if order is None:
            return None
        _, user = User.find_prev_alive_order(order)
        return user

    # --- цель -------------------------------------------------------------

    def get_target_id(self):
        return self._get("target")

    def set_target_id(self, value):
        self._set("target", value)

    def get_target_user(self):
        tid = self._get("target")
        return User.by_id(tid) if tid else None

    def set_target_user(self, user):
        self._set("target", user.id if user else None)

    def update_target_quiet(self):
        """Назначить целью следующего живого по кругу (без уведомлений)."""
        self.set_target_user(self.find_victim())

    # --- «убийца»/поимка --------------------------------------------------

    def set_killed_by(self, value):
        self._set("killed_by", value)

    def get_murderer(self):
        k = self._get("killed_by")
        return User.by_id(k) if k else None

    def set_murderer(self, user):
        self._set("killed_by", user.id if user else None)

    def is_being_caught(self) -> bool:
        """Жив, но кто-то уже заявил о его поимке (ждёт подтверждения)."""
        return self.is_alive() and self._get("killed_by") is not None

    def is_awaiting_confirmation(self) -> bool:
        """Игрок заявил о поимке своей цели и ждёт её подтверждения."""
        target = self.get_target_user()
        murderer = target.get_murderer() if target else None
        return bool(target and target.is_being_caught()
                    and murderer and murderer.id == self.id)

    # --- поимки (основной игровой цикл) -----------------------------------

    def _log(self, text):
        from core import admin_log
        admin_log.log(text)

    def attempt_capture(self):
        """Игрок сообщает о поимке своей текущей цели. (ok, сообщение)."""
        if not self.is_alive():
            return False, "Вы выбыли из игры."
        victim = self.get_target_user()
        if victim is None or not victim.is_alive():
            return False, "Сейчас у вас нет активной цели."
        if victim.is_being_caught():
            return False, "По этой цели уже есть заявка на поимку."

        if not CONFIRM_KILLS:
            self.capture(victim)
            return True, "Поимка засчитана."

        victim.set_murderer(self)
        self._log(f"📸 {self.get_name()} заявил(а) о поимке {victim.get_name()} "
                  "(ждёт подтверждения)")
        self.notify(f"📸 Вы заявили о поимке цели ({victim.get_name()}).\n"
                    "⏳ Ожидайте подтверждения от игрока.")
        victim.notify("📸 Другой игрок заявил, что поймал вас.\n"
                      "Пожалуйста, подтвердите или опровергните это на сайте.")
        return True, "Заявка отправлена, ждём подтверждения цели."

    def cancel_capture(self):
        """Отозвать свою заявку о поимке цели (пока цель не ответила)."""
        victim = self.get_target_user()
        if victim and self.is_awaiting_confirmation():
            victim.set_murderer(None)
            self._log(f"✖️ {self.get_name()} отозвал(а) заявку о поимке "
                      f"{victim.get_name()}")
            self.notify("✖️ Вы отозвали заявку о поимке.")
            victim.notify("✅ Заявка о вашей поимке отозвана — тревога отменена.")
            return True, "Заявка отозвана."
        return False, "Нет активной заявки для отмены."

    def confirm_capture(self):
        """Жертва подтверждает, что её поймали → устранение."""
        murderer = self.get_murderer()
        if murderer is None or not self.is_being_caught():
            return False, "Сейчас никто не заявлял о вашей поимке."
        self._log(f"✅ {self.get_name()} подтвердил(а) поимку игроком "
                  f"{murderer.get_name()}")
        murderer.capture(self)
        return True, "Поимка подтверждена."

    def deny_capture(self):
        """Жертва опровергает поимку → остаётся в игре."""
        murderer = self.get_murderer()
        if not self.is_being_caught():
            return False, "Сейчас никто не заявлял о вашей поимке."
        self.set_murderer(None)
        self._log(f"🚫 {self.get_name()} не подтвердил(а) поимку игроком "
                  f"{murderer.get_name() if murderer else '?'}")
        self.notify("💚 Вы отклонили заявку о поимке — остаётесь в игре.")
        if murderer:
            murderer.notify(f"🚫 Игрок {self.get_name()} не подтвердил(а) поимку. "
                            "Попробуйте ещё раз.")
        return True, "Поимка отклонена."

    def capture(self, victim: "User"):
        """Засчитать поимку victim игроком self: устранение, счёт, новая цель."""
        self._log(f"📸 {self.get_name()} поймал(а) {victim.get_name()}")
        self.increment_score()
        victim.set_alive(False)
        victim.set_target_id(None)
        victim.set_murderer(self)

        victim.notify(f"🗿 Увы, вас поймал(а) {self.get_name()}. Вы выбыли из игры.\n"
                      "Спасибо за игру!")

        from core.game import Game
        game = Game()
        finished = game.check_finished()

        if self.is_alive() and not finished:
            self.update_target_quiet()
            new_target = self.get_target_user()
            self.notify(f"📸 Поздравляем! Вы поймали {victim.get_name()}.\n"
                        f"🎯 Ваша новая цель: {new_target.get_name() if new_target else '—'}")
        elif self.is_alive():
            self.notify(f"📸 Поздравляем! Вы поймали {victim.get_name()}.")

        if finished:
            game.announce_winner()

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
        self._set("killed_by", None)
        self._set("target", None)
        # Позицию в круге назначаем сразу (цель раздаётся при старте игры).
        self.randomize_game_order()

    def leave(self):
        self.set_player(False)
        self.set_alive(False)
        self._set("kill_count", 0)
        self._set("game_order", None)
        self._set("target", None)
        self._set("killed_by", None)
