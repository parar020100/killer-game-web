"""Модель пользователя — профиль и игровое состояние (таблица user).

Всё, что относится к каналу связи (tg/vk, username, имя, mute, ADMIN LOG),
живёт в таблице `identity` и модели Identity. У одного пользователя может быть
несколько идентичностей; уведомления доставляются в каждую неприглушённую.
"""
import random

from db import query_one, query_all, execute
from core.identity import Identity


# --- причина отклонения поимки (TODO 79) ------------------------------------
# Когда поимку/убийство не принимают (жертва или админ), можно указать причину.
# Она добавляется в лог для админов и в уведомление «охотнику» единообразно.

def _reason_log_suffix(reason: str) -> str:
    reason = (reason or "").strip()
    return f" (причина: {reason})" if reason else ""


def _reason_notify_suffix(reason: str) -> str:
    reason = (reason or "").strip()
    return f"\nПричина: {reason}" if reason else ""


# --- админы по умолчанию (config.py, отдельно для Telegram и VK) -------------
# Списки DEFAULT_ADMINS_TG / DEFAULT_ADMINS_VK в config.py содержат id или
# username/screen_name каналов. Кто входит через такой канал — получает права
# администратора автоматически. Списки РАЗДЕЛЬНЫЕ: один человек может быть админом
# при входе через VK и не быть при входе через TG (пока это разные аккаунты — до
# объединения каналов кодом привязки). Только выдаёт права, никогда не снимает.

def _default_admin_handles(platform: str) -> set:
    try:
        import config
    except ImportError:
        return set()
    key = {"tg": "DEFAULT_ADMINS_TG", "vk": "DEFAULT_ADMINS_VK"}.get(platform)
    if not key:
        return set()
    raw = getattr(config, key, None) or []
    return {str(x).strip().lstrip("@").lower() for x in raw if str(x).strip()}


def is_default_admin_identity(platform, platform_uid, username=None) -> bool:
    """Входит ли этот канал (по id ИЛИ username) в список админов своей платформы."""
    handles = _default_admin_handles(platform)
    if not handles:
        return False
    cand = {str(platform_uid).strip().lstrip("@").lower()}
    if username:
        cand.add(str(username).strip().lstrip("@").lower())
    return bool(cand & handles)


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
    def by_platform_username(cls, platform, value):
        """Пользователь по (платформа, username|platform_uid) — точное сопоставление.

        Нужно, чтобы отличать одинаковые ники на разных платформах (tg/vk/local):
        например ?source=vk&user=nick найдёт именно VK-канал, а не первый попавшийся.
        """
        if not value:
            return None
        v = str(value).strip().lstrip("@")
        row = query_one(
            "SELECT user_id FROM identity WHERE platform = ? "
            "AND (lower(username) = lower(?) OR lower(platform_uid) = lower(?)) "
            "ORDER BY id LIMIT 1", (platform, v, v))
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
        # Админ по умолчанию (config.py, отдельно для tg/vk): если этот канал в
        # списке — выдаём права (идемпотентно, при каждом входе; не снимает).
        if not user.is_admin() and is_default_admin_identity(platform, platform_uid, username):
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

    # 'local' — самостоятельный веб-чат (эмуляция): свой никнейм, не связан с tg/vk.
    @classmethod
    def by_local(cls, name_id):
        return cls.by_identity("local", name_id)

    @classmethod
    def get_or_create_by_local(cls, name_id, username=None, name=None):
        return cls.get_or_create_by_identity("local", name_id, username, name)

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

    # --- версия сессии (авторитетный выход, независимо от cookie) -----------
    # Сессия-cookie хранит версию, с которой аккаунт вошёл. «Выйти» инкрементит
    # серверную версию — и все cookie со старой версией (в т.ч. в других вкладках,
    # и «воскрешённые» гонкой перезаписи cookie) перестают давать доступ к аккаунту.

    def get_session_version(self) -> int:
        v = self._get("session_version")
        return int(v) if v is not None else 0

    def bump_session_version(self) -> int:
        execute("UPDATE user SET session_version = COALESCE(session_version, 0) + 1 "
                "WHERE id = ?", (self.id,))
        return self.get_session_version()

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
        """Игрок сообщает о поимке/устранении своей текущей цели. (ok, сообщение)."""
        from core import settings, mode
        if not self.is_alive():
            return False, "Вы выбыли из игры."
        victim = self.get_target_user()
        if victim is None:
            return False, "Сейчас у вас нет активной цели."
        if not victim.is_alive():
            # Цель уже выбыла (напр. устранена админом до пересборки круга): как в
            # боте — сообщаем и тут же освежаем свою цель на следующего живого.
            self.update_target_quiet()
            return False, mode.t("target_already_dead")
        if victim.is_being_caught():
            return False, mode.t("err_already_pending")

        if not settings.confirm_kills():
            self.capture(victim)
            return True, mode.t("attempt_instant_ok")

        victim.set_murderer(self)
        self._log(mode.t("attempt_log", name=self.get_name(), victim=victim.get_name()))
        self.notify(mode.t("attempt_self", victim=victim.get_name()))
        victim.notify(mode.t("attempt_victim"))
        return True, mode.t("attempt_sent_ok")

    def cancel_capture(self):
        """Отозвать свою заявку о поимке цели (пока цель не ответила)."""
        from core import mode
        victim = self.get_target_user()
        if victim and self.is_awaiting_confirmation():
            victim.set_murderer(None)
            self._log(mode.t("cancel_log", name=self.get_name(), victim=victim.get_name()))
            self.notify(mode.t("cancel_self"))
            victim.notify(mode.t("cancel_victim"))
            return True, "Заявка отозвана."
        return False, "Нет активной заявки для отмены."

    def confirm_capture(self):
        """Жертва подтверждает, что её поймали → выбывание."""
        from core import mode
        murderer = self.get_murderer()
        if murderer is None or not self.is_being_caught():
            return False, mode.t("confirm_none")
        self._log(mode.t("confirm_log", name=self.get_name(), murderer=murderer.get_name()))
        murderer.capture(self)
        return True, mode.t("confirm_ok")

    def deny_capture(self, reason: str = ""):
        """Жертва опровергает поимку → остаётся в игре.

        reason — необязательная причина отказа: показывается админам в логе и уходит
        в уведомление «охотнику» (киллеру/папарацци), чтобы он понял, почему поимку
        не засчитали (TODO 79)."""
        from core import mode
        murderer = self.get_murderer()
        if not self.is_being_caught():
            return False, mode.t("confirm_none")
        self.set_murderer(None)
        self._log(mode.t("deny_log", name=self.get_name(),
                         murderer=murderer.get_name() if murderer else "?")
                  + _reason_log_suffix(reason))
        self.notify(mode.t("deny_self"))
        if murderer:
            murderer.notify(mode.t("deny_murderer", name=self.get_name())
                            + _reason_notify_suffix(reason))
        return True, mode.t("deny_ok")

    def capture(self, victim: "User"):
        """Засчитать поимку/устранение victim игроком self: выбывание, счёт, новая цель."""
        from core import mode
        self._log(mode.t("capture_log", name=self.get_name(), victim=victim.get_name()))
        self.increment_score()
        victim.set_alive(False)
        victim.set_target_id(None)
        victim.set_murderer(self)

        victim.notify(mode.t("capture_victim", name=self.get_name()))

        from core.game import Game
        game = Game()
        # Освободилось место убитого — вернём ВСЮ очередь возрождения в его слот
        # (в случайном порядке, друг за другом), чтобы киллер (self) получил целью
        # первого воскрешённого — для него поимка неотличима от обычной (как в боте).
        game.try_revive_all(at_order=victim.get_game_order_raw())
        game.reassign_targets()
        finished = game.check_finished()

        if self.is_alive() and not finished:
            new_target = self.get_target_user()
            self.notify(mode.t("capture_self_new", victim=victim.get_name(),
                               target=new_target.get_name() if new_target else "—"))
        elif self.is_alive():
            self.notify(mode.t("capture_self_done", victim=victim.get_name()))

        if finished:
            game.announce_winner()

    # --- очередь на возрождение -------------------------------------------

    def revive_queue_push(self):
        if not self.is_queued_for_revival():
            execute("INSERT INTO revive_queue (user_id) VALUES (?)", (self.id,))

    def revive_queue_remove(self):
        execute("DELETE FROM revive_queue WHERE user_id = ?", (self.id,))

    # --- действия администратора над игроком ------------------------------

    def _finish_structural_change(self, was_alive: bool, at_order=None, revive=False):
        """Общий хвост админ-действий, меняющих состав живых: возрождение/круг/итог.

        `revive=True` (только устранение админом — как в боте) вернёт ВСЮ очередь в
        освободившийся слот `at_order`. При выходе/кике очередь НЕ трогаем (в боте
        leave/kick не оживляют) — только пересобираем круг и проверяем конец.
        """
        from core.game import Game
        game = Game()
        if was_alive and game.is_started():
            if revive:
                game.try_revive_all(at_order=at_order)   # вернём очередь в слот
            game.reassign_targets()
            if game.check_finished():
                game.announce_winner()

    def admin_kill(self, admin: "User") -> str:
        """Устранить игрока (остаётся выбывшим игроком — можно оживить, учтётся в итогах)."""
        if not self.is_player():
            return "Пользователь не участвует в игре."
        from core import mode
        was_alive = self.is_alive()
        freed_order = self.get_game_order_raw()   # слот убитого — для воскрешения в него
        self._log(mode.t("admin_kill_log", admin=admin.get_name(), name=self.get_name()))
        self.notify(mode.t("admin_kill_notify"))
        self.set_alive(False)
        self.set_target_id(None)
        self.set_murderer(None)
        self._finish_structural_change(was_alive, at_order=freed_order, revive=True)
        return f"Игрок {self.get_name()} устранён из игры."

    def admin_revive(self, admin: "User") -> str:
        from core import mode
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
        self.notify(mode.t("admin_revive_notify"))
        return f"Игрок {self.get_name()} снова в игре."

    def admin_kick(self, admin: "User") -> str:
        """Полностью убрать из игры (перестаёт быть игроком)."""
        if not self.is_player():
            return "Пользователь и так не в игре."
        was_alive = self.is_alive()
        freed_order = self.get_game_order_raw()   # слот до сброса в leave()
        self._log(f"👋 {admin.get_name()} удалил(а) игрока {self.get_name()} из игры")
        self.notify("👋 Администратор удалил вас из игры.")
        self.leave()
        self._finish_structural_change(was_alive, at_order=freed_order)
        return f"Игрок {self.get_name()} удалён из игры."

    def admin_set_score(self, admin: "User", value: int) -> str:
        from core import mode
        self.set_score(value)
        self._log(mode.t("admin_setscore_log", admin=admin.get_name(),
                         value=value, name=self.get_name()))
        self.notify(mode.t("admin_setscore_notify", value=value))
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
        from core import mode
        murderer = self.get_murderer()
        if not self.is_being_caught() or murderer is None:
            return "По этому игроку нет заявки на поимку."
        self._log(f"✅ {admin.get_name()} подтвердил(а) поимку {self.get_name()} "
                  f"игроком {murderer.get_name()}")
        self.notify(mode.t("admin_force_accept_notify", admin=admin.get_name()))
        murderer.capture(self)
        return f"Поимка игрока {self.get_name()} засчитана."

    def admin_force_deny(self, admin: "User", reason: str = "") -> str:
        """Админ отклоняет заявку о поимке. reason — необязательная причина: в лог
        админам и в уведомление «охотнику» (TODO 79)."""
        from core import mode
        murderer = self.get_murderer()
        if not self.is_being_caught():
            return "По этому игроку нет заявки на поимку."
        self.set_murderer(None)
        self._log(f"❌ {admin.get_name()} отклонил(а) поимку игрока {self.get_name()}"
                  + _reason_log_suffix(reason))
        self.notify(mode.t("admin_force_deny_victim"))
        if murderer:
            murderer.notify(mode.t("admin_force_deny_murderer", name=self.get_name())
                            + _reason_notify_suffix(reason))
        return f"Поимка игрока {self.get_name()} отклонена."

    def admin_reassign_kill(self, admin: "User", new_murderer: "User") -> str:
        """Переназначить, кому засчитывается поимка выбывшего игрока (self).

        Пример: цель поймал не тот, кому она была назначена. Списываем очко у
        прежнего «охотника» (если был) и начисляем новому, обновляя killed_by.
        """
        if not self.is_player():
            return "Пользователь не участвует в игре."
        if self.is_alive():
            return "Переназначить поимку можно только у выбывшего игрока."
        if new_murderer is None or new_murderer.id == self.id:
            return "Некорректный игрок для зачёта поимки."
        old = self.get_murderer()
        if old and old.id == new_murderer.id:
            return "Поимка уже засчитана этому игроку."
        from core import mode
        if old:
            old.decrement_score()
            old.notify(mode.t("reassign_old", name=self.get_name()))
        new_murderer.increment_score()
        self.set_murderer(new_murderer)
        self._log(mode.t("reassign_log", admin=admin.get_name(),
                         name=self.get_name(), new=new_murderer.get_name()))
        new_murderer.notify(mode.t("reassign_new", name=self.get_name()))
        return mode.t("reassign_ok", name=self.get_name(), new=new_murderer.get_name())

    def give_life(self, admin: "User") -> str:
        if self.is_alive():
            return "Игрок и так в игре."
        from core import mode
        self.revive_queue_push()
        self._log(f"🎁 {admin.get_name()} подарил(а) жизнь игроку {self.get_name()}")
        self.notify(mode.t("give_life_notify"))
        return f"Игрок {self.get_name()} добавлен в очередь на оживление."

    def take_life(self, admin: "User") -> str:
        # Как give_life_cancel в боте: проверить, что игрок реально ждёт возрождения,
        # и уведомить его об отмене.
        if self.is_alive():
            return "Игрок в игре — он не в очереди на возрождение."
        if not self.is_queued_for_revival():
            return f"Игрок {self.get_name()} не в очереди на оживление."
        from core import mode
        self.revive_queue_remove()
        self._log(f"🚫 {admin.get_name()} отменил(а) шанс возрождения игроку {self.get_name()}")
        self.notify(mode.t("take_life_notify", admin=admin.get_name()))
        return f"Игрок {self.get_name()} убран из очереди на оживление."

    def delete_from_system(self, admin: "User") -> str:
        """Удалить пользователя из БД (identity/логин каскадно, ссылки — обнулить)."""
        name = self.get_name()
        was_alive = self.is_alive()
        freed_order = self.get_game_order_raw()
        self._log(f"🗑️ {admin.get_name()} удалил(а) пользователя {name} из системы")
        execute("UPDATE user SET target = NULL WHERE target = ?", (self.id,))
        execute("UPDATE user SET killed_by = NULL WHERE killed_by = ?", (self.id,))
        execute("DELETE FROM user WHERE id = ?", (self.id,))
        self._finish_structural_change(was_alive, at_order=freed_order)
        return f"Пользователь {name} удалён из системы."

    # --- самообслуживание учётки («забыть меня») --------------------------

    def revoke_login_links(self):
        """Отозвать постоянные ссылки входа всех каналов («выйти на всех устройствах»).

        Старые magic-ссылки перестают работать; новую можно получить в чате (/start).
        """
        from core import auth
        for ident in self.identities():
            auth.revoke_permanent_token(ident.id)

    def forget_self(self) -> bool:
        """Самоудаление учётной записи («забыть меня»). root удалить нельзя.

        Каскадно удаляет идентичности и ссылки входа; игровые ссылки на этого
        пользователя обнуляются. Возвращает False, если это root (удаление запрещено).
        """
        if self.is_root():
            return False
        name = self.get_name()
        was_alive = self.is_alive()
        self._log(f"🗑️ {name} удалил(а) свою учётную запись («забыть меня»)")
        execute("UPDATE user SET target = NULL WHERE target = ?", (self.id,))
        execute("UPDATE user SET killed_by = NULL WHERE killed_by = ?", (self.id,))
        execute("DELETE FROM user WHERE id = ?", (self.id,))
        self._finish_structural_change(was_alive)
        return True

    # --- уведомления ------------------------------------------------------

    def notify(self, text: str, silent: bool = False):
        """Доставить сообщение во все неприглушённые каналы пользователя.

        silent=True — тихое уведомление без звука (для платформ, что это умеют).
        """
        for ident in self.identities():
            if not ident.is_muted():
                ident.deliver(text, silent=silent)

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
