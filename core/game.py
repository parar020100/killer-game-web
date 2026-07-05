"""Модель игры — доступ к единственной строке таблицы game (id=1).

Аналог game.py из бота, но пока только состояние и счётчики. Логика целей,
поимок и воскрешений подключится на следующих шагах.
"""
from db import query_one, execute


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
        # старт закрывает набор — как в боте
        self.set_registration_open(False)
        self.set_paused(False)
        self.set_started(True)

    def stop(self):
        self.set_started(False)
        self.set_paused(False)

    # --- счётчики ---------------------------------------------------------

    def count_users(self) -> int:
        return query_one("SELECT COUNT(*) FROM user")[0]

    def count_players(self) -> int:
        return query_one("SELECT COUNT(*) FROM user WHERE is_player = 1")[0]

    def count_alive(self) -> int:
        return query_one("SELECT COUNT(*) FROM user WHERE is_player = 1 AND is_alive = 1")[0]

    # --- текст статуса (как в status.py бота) ------------------------------

    def status_html(self) -> str:
        if self.is_started():
            base = "🟢 Игра <strong><em>запущена</em></strong>"
        elif self.is_registration_open():
            base = "🟡 <strong><em>Открыта регистрация</em></strong> на игру"
        elif self.count_players() > 0:
            base = "🚫 <strong><em>Регистрация закрыта</em></strong>"
        else:
            base = "🔴 <strong><em>Нет активной игры</em></strong>"
        return base
