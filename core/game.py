"""Модель игры — доступ к единственной строке таблицы game (id=1).

Аналог game.py из бота: состояние, счётчики, раздача целей по кругу, определение
победителя и подведение итогов. Поимки живут в модели User (attempt/accept/deny).
"""
from db import query_one, query_all, execute
from core.user import User


class Game:
    # --- служебное чтение/запись поля -------------------------------------

    def _get(self, field: str):
        row = query_one(f"SELECT {field} FROM game WHERE id = 1")
        return row[0] if row else None

    def _set(self, field: str, value):
        execute(f"UPDATE game SET {field} = ? WHERE id = 1", (value,))

    # --- состояние --------------------------------------------------------

    def is_registration_open(self) -> bool:
        return bool(self._get("registration_open"))

    def is_started(self) -> bool:
        return bool(self._get("is_started"))

    def is_paused(self) -> bool:
        return bool(self._get("is_paused"))

    def set_registration_open(self, value: bool):
        self._set("registration_open", int(value))

    def set_started(self, value: bool):
        self._set("is_started", int(value))

    def set_paused(self, value: bool):
        self._set("is_paused", int(value))

    def get_password(self) -> str:
        return self._get("password") or ""

    def set_password(self, value: str):
        self._set("password", value)

    # --- переходы (упрощённые, полноценная логика — на шаге старта игры) ---

    def open_registration(self):
        self.set_registration_open(True)

    def close_registration(self):
        self.set_registration_open(False)

    def start(self):
        """Запустить игру: раздать цели по кругу. Возвращает (ok, сообщение)."""
        if self.is_started() and not self.is_paused():
            return False, "Игра уже запущена."
        if self.count_players() <= 1 or self.count_alive() == 0:
            return False, "Недостаточно игроков для запуска игры."
        if self.count_alive() == 1:
            return False, "В игре остался 1 игрок. Продолжение невозможно."

        self.set_registration_open(False)
        # Каждому живому игроку — цель = следующий живой по кругу.
        for ply in User.all_players():
            ply.update_target_quiet()
        self.set_paused(False)
        self.set_started(True)
        return True, "Игра началась."

    def stop(self):
        self.set_started(False)
        self.set_paused(False)

    def pause(self):
        self.set_paused(True)

    def resume(self):
        if self.is_started():
            self.set_paused(False)

    # --- счётчики ---------------------------------------------------------

    def count_users(self) -> int:
        return query_one("SELECT COUNT(*) FROM user")[0]

    def count_players(self) -> int:
        return query_one("SELECT COUNT(*) FROM user WHERE is_player = 1")[0]

    def count_alive(self) -> int:
        return query_one("SELECT COUNT(*) FROM user WHERE is_player = 1 AND is_alive = 1")[0]

    # --- игроки / победитель ----------------------------------------------

    def alive_players(self):
        return User.alive_players()

    def get_winner(self):
        """Единственный живой игрок или None."""
        alive = User.alive_players()
        return alive[0] if len(alive) == 1 else None

    def check_finished(self) -> bool:
        """Если живых ≤ 1 — поставить игру на паузу (ожидание итогов). True, если конец."""
        if self.count_alive() <= 1:
            if self.is_started() and not self.is_paused():
                self.set_paused(True)
            return True
        return False

    def _top_grouped(self, alive: bool, limit: int = 3):
        """Списки игроков по убыванию счёта: [[лидеры], [вторые], [третьи]]."""
        rows = query_all(
            "SELECT DISTINCT kill_count FROM user "
            "WHERE is_player = 1 AND is_alive = ? ORDER BY kill_count DESC LIMIT ?",
            (int(alive), limit))
        result = []
        for r in rows:
            players = query_all(
                "SELECT id FROM user WHERE is_player = 1 AND is_alive = ? "
                "AND kill_count = ? ORDER BY game_order IS NULL, game_order, id",
                (int(alive), r["kill_count"]))
            result.append([User(p["id"]) for p in players])
        return result

    def top_alive_grouped(self, limit: int = 3):
        return self._top_grouped(True, limit)

    def top_dead_grouped(self, limit: int = 3):
        return self._top_grouped(False, limit)

    # --- текст статуса (как в status.py бота) ------------------------------

    def status_html(self) -> str:
        if self.is_started():
            if self.is_paused():
                base = "⏸️ Игра <strong><em>на паузе</em></strong>"
            else:
                base = "🟢 Игра <strong><em>запущена</em></strong>"
        elif self.is_registration_open():
            base = "🟡 <strong><em>Открыта регистрация</em></strong> на игру"
        elif self.count_players() > 0:
            base = "🚫 <strong><em>Регистрация закрыта</em></strong>"
        else:
            base = "🔴 <strong><em>Нет активной игры</em></strong>"
        return base
