"""Модель пользователя — профиль и игровое состояние (таблица user).

Всё, что относится к каналу связи (tg/vk, username, имя, mute, ADMIN LOG),
живёт в таблице `identity` и модели Identity. У одного пользователя может быть
несколько идентичностей; уведомления доставляются в каждую неприглушённую.
"""
import random

from db import query_one, query_all, execute
from core.identity import Identity


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
    def by_username(cls, username):
        """Найти пользователя по username любого канала (или по platform_uid).

        В эмуляции чата platform_uid = username, поэтому оба варианта совпадают.
        Регистронезависимо, ведущий @ игнорируется.
        """
        if not username:
            return None
        u = str(username).strip().lstrip("@")
        if not u:
            return None
        row = query_one(
            "SELECT user_id FROM identity WHERE lower(username) = lower(?) "
            "ORDER BY id LIMIT 1", (u,))
        if not row:
            row = query_one(
                "SELECT user_id FROM identity WHERE lower(platform_uid) = lower(?) "
                "ORDER BY id LIMIT 1", (u,))
        return cls(row["user_id"]) if row else None

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
    def is_root(self) -> bool:
        """root-пользователь (создаётся при первом запуске) — особый админ."""
        from core import settings
        return settings.root_user_id() == self.id
    def is_default_admin(self) -> bool:
        """Совместимость: «неприкосновенный» админ = root (нельзя снять/удалить)."""
        return self.is_root()
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

        from core import settings
        if not settings.confirm_kills():
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
        game.try_revive_one()      # освободилось место — вернём одного из очереди
        game.reassign_targets()
        finished = game.check_finished()

        if self.is_alive() and not finished:
            new_target = self.get_target_user()
            self.notify(f"📸 Поздравляем! Вы поймали {victim.get_name()}.\n"
                        f"🎯 Ваша новая цель: {new_target.get_name() if new_target else '—'}")
        elif self.is_alive():
            self.notify(f"📸 Поздравляем! Вы поймали {victim.get_name()}.")

        if finished:
            game.announce_winner()

    # --- очередь на возрождение -------------------------------------------

    def revive_queue_push(self):
        if not self.is_queued_for_revival():
            execute("INSERT INTO revive_queue (user_id) VALUES (?)", (self.id,))

    def revive_queue_remove(self):
        execute("DELETE FROM revive_queue WHERE user_id = ?", (self.id,))

    # --- действия администратора над игроком ------------------------------

    def _finish_structural_change(self, was_alive: bool):
        """Общий хвост админ-действий, меняющих состав живых: возрождение/круг/итог."""
        from core.game import Game
        game = Game()
        if was_alive and game.is_started():
            game.try_revive_one()      # освободилось место — вернём одного из очереди
            game.reassign_targets()
            if game.check_finished():
                game.announce_winner()

    def admin_kill(self, admin: "User") -> str:
        """Устранить игрока (остаётся выбывшим игроком — можно оживить, учтётся в итогах)."""
        if not self.is_player():
            return "Пользователь не участвует в игре."
        was_alive = self.is_alive()
        self._log(f"💀 {admin.get_name()} устранил(а) игрока {self.get_name()}")
        self.notify("💀 Администратор устранил вас из игры.")
        self.set_alive(False)
        self.set_target_id(None)
        self.set_murderer(None)
        self._finish_structural_change(was_alive)
        return f"Игрок {self.get_name()} устранён из игры."

    def admin_revive(self, admin: "User") -> str:
        if not self.is_player():
            return "Пользователь не участвует в игре."
        if self.is_alive():
            return "Игрок уже в игре."
        self._log(f"🧟 {admin.get_name()} оживил(а) игрока {self.get_name()}")
        self.set_alive(True)
        self.set_murderer(None)
        self.revive_queue_remove()
        if self.get_game_order_raw() is None:
            self.randomize_game_order()
        from core.game import Game
        game = Game()
        if game.is_started():
            game.reassign_targets()
        target = self.get_target_user()
        self.notify("🧟 Администратор вернул вас в игру! Ваша цель: "
                    f"{target.get_name() if target else '—'}")
        return f"Игрок {self.get_name()} снова в игре."

    def admin_kick(self, admin: "User") -> str:
        """Полностью убрать из игры (перестаёт быть игроком)."""
        if not self.is_player():
            return "Пользователь и так не в игре."
        was_alive = self.is_alive()
        self._log(f"👋 {admin.get_name()} удалил(а) игрока {self.get_name()} из игры")
        self.notify("👋 Администратор удалил вас из игры.")
        self.leave()
        self._finish_structural_change(was_alive)
        return f"Игрок {self.get_name()} удалён из игры."

    def admin_set_score(self, admin: "User", value: int) -> str:
        self.set_score(value)
        self._log(f"💯 {admin.get_name()} задал(а) счёт {value} игроку {self.get_name()}")
        self.notify(f"💯 Администратор изменил ваш счёт поимок: {value}.")
        return f"Счёт игрока {self.get_name()} = {value}."

    def admin_randomize_order(self, admin: "User") -> str:
        self.randomize_game_order()
        self._log(f"🔀 {admin.get_name()} сменил(а) позицию в круге игроку {self.get_name()}")
        from core.game import Game
        game = Game()
        if game.is_started() and self.is_alive():
            game.reassign_targets()
        return f"Позиция игрока {self.get_name()} в круге изменена."

    def admin_set_order(self, admin: "User", position: int) -> str:
        """Поставить игрока на конкретную позицию в круге (при конфликте — сдвиг)."""
        if not self.is_player():
            return "Пользователь не участвует в игре."
        placed = self.set_game_order(position, increase=True)
        self._log(f"🔢 {admin.get_name()} задал(а) позицию {placed} в круге "
                  f"игроку {self.get_name()}")
        from core.game import Game
        game = Game()
        if game.is_started() and self.is_alive():
            game.reassign_targets()
        return f"Позиция игрока {self.get_name()} в круге: {placed}."

    def admin_force_accept(self, admin: "User") -> str:
        murderer = self.get_murderer()
        if not self.is_being_caught() or murderer is None:
            return "По этому игроку нет заявки на поимку."
        self._log(f"✅ {admin.get_name()} подтвердил(а) поимку {self.get_name()} "
                  f"игроком {murderer.get_name()}")
        murderer.capture(self)
        return f"Поимка игрока {self.get_name()} засчитана."

    def admin_force_deny(self, admin: "User") -> str:
        murderer = self.get_murderer()
        if not self.is_being_caught():
            return "По этому игроку нет заявки на поимку."
        self.set_murderer(None)
        self._log(f"❌ {admin.get_name()} отклонил(а) поимку игрока {self.get_name()}")
        self.notify("💚 Администратор отклонил заявку о вашей поимке — вы в игре.")
        if murderer:
            murderer.notify(f"🚫 Администратор отклонил вашу поимку {self.get_name()}.")
        return f"Поимка игрока {self.get_name()} отклонена."

    def give_life(self, admin: "User") -> str:
        if self.is_alive():
            return "Игрок и так в игре."
        self.revive_queue_push()
        self._log(f"🎁 {admin.get_name()} подарил(а) жизнь игроку {self.get_name()}")
        self.notify("🎁 Администратор дал вам ещё один шанс! "
                    "Возрождение произойдёт при первой возможности.")
        return f"Игрок {self.get_name()} добавлен в очередь на оживление."

    def take_life(self, admin: "User") -> str:
        self.revive_queue_remove()
        self._log(f"🚫 {admin.get_name()} отменил(а) шанс возрождения игроку {self.get_name()}")
        return f"Игрок {self.get_name()} убран из очереди на оживление."

    def delete_from_system(self, admin: "User") -> str:
        """Удалить пользователя из БД (identity/логин каскадно, ссылки — обнулить)."""
        name = self.get_name()
        was_alive = self.is_alive()
        self._log(f"🗑️ {admin.get_name()} удалил(а) пользователя {name} из системы")
        execute("UPDATE user SET target = NULL WHERE target = ?", (self.id,))
        execute("UPDATE user SET killed_by = NULL WHERE killed_by = ?", (self.id,))
        execute("DELETE FROM user WHERE id = ?", (self.id,))
        self._finish_structural_change(was_alive)
        return f"Пользователь {name} удалён из системы."

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
