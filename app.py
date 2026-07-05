"""Веб-версия игры «Киллер / Папарацци» — каркас (FastAPI + Jinja2).

Шаг: состояние игры и регистрация игроков хранятся в **SQLite** (см. db.py, core/).
Входа по аккаунту пока нет — вместо него DEV-режим: текущий пользователь выбирается
параметром `?as=<N>` в адресе (по умолчанию 1). Каждое N — отдельный тестовый игрок,
поэтому в разных вкладках можно открыть разных пользователей (`?as=1`, `?as=2`, ...).

Кнопки — обычные HTML-формы (POST → изменение состояния → редирект), без JavaScript.

Запуск:  ./start.sh   (см. README.md)
"""
from pathlib import Path

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db  # noqa: F401 — импорт инициализирует БД
from core.game import Game
from core.user import User

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Killer / Paparazzi — web")

SUPPORT_CONTACT = "@parar020100"


# ---------------------------------------------------------------------------
# DEV: текущий пользователь по ?as=<N> (заменится входом по ссылке)
# ---------------------------------------------------------------------------

def dev_current_user(as_id: str) -> User:
    """Берёт/создаёт тестового пользователя с tg_id = N (N из ?as=)."""
    try:
        tg = int(as_id)
    except (TypeError, ValueError):
        tg = 1
    return User.get_or_create_by_tg(tg, username=f"test{tg}", name=f"Тест {tg}")


# ---------------------------------------------------------------------------
# Кнопки и экраны
# ---------------------------------------------------------------------------

def _btn(label, action=None, kind="", full=False):
    return {"label": label, "action": action, "kind": kind, "full": full}


def player_view(user: User):
    game = Game()
    lines = [
        f'<span class="hi">Привет, {user.get_name()}!</span>',
        "📷 Добро пожаловать в игру Папарацци!",
        "",
        '<span class="divider">══ 📋 Статус игры ══</span>',
        game.status_html(),
    ]
    if user.is_player():
        lines.append("✅ <em>Вы зарегистрированы в игре</em>")
    else:
        lines.append("❌ <em>Вы пока не участвуете в игре</em>")
    lines.append(f"👥 Игроков: <strong>{game.count_players()}</strong>")

    buttons = []
    if user.is_player():
        buttons.append(_btn("🔴 Выйти из игры", "leave", "danger", full=True))
    elif game.is_registration_open():
        buttons.append(_btn("🟢 Зарегистрироваться", "join", "primary", full=True))

    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return "\n".join(lines), buttons


def admin_view(user: User):
    game = Game()
    message = "\n".join([
        f'<span class="hi">Привет, {user.get_name()}!</span>',
        "📷 Ты — администратор игры.",
        "",
        '<span class="divider">══ 📋 Статус игры ══</span>',
        game.status_html(),
        f"👥 Игроков: <strong>{game.count_players()}</strong> "
        f"(живы: <strong>{game.count_alive()}</strong>)",
    ])

    buttons = []
    if game.is_started():
        buttons.append(_btn("⏹️ Остановить игру", "stop_game", "danger"))
    else:
        buttons.append(_btn("▶️ Запустить игру", "start_game", "primary"))
    if game.is_registration_open():
        buttons.append(_btn("🚫 Закрыть регистрацию", "close_reg"))
    else:
        buttons.append(_btn("✅ Открыть регистрацию", "open_reg"))

    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return message, buttons


# ---------------------------------------------------------------------------
# Действия кнопок
# ---------------------------------------------------------------------------

def apply_action(action: str, user: User):
    game = Game()
    if action == "open_reg":
        game.open_registration()
    elif action == "close_reg":
        game.close_registration()
    elif action == "start_game":
        game.start()
    elif action == "stop_game":
        game.stop()
    elif action == "join":
        if game.is_registration_open() and not user.is_player():
            user.join(alive=not game.is_started())
    elif action == "leave":
        if user.is_player():
            user.leave()
    # "noop" / незнакомое — просто перерисовать


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, role: str = "player", as_id: str = Query("1", alias="as")):
    user = dev_current_user(as_id)
    if role == "admin":
        message, buttons = admin_view(user)
    else:
        role = "player"
        message, buttons = player_view(user)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "role": role,
            "as_id": as_id,
            "user_id": user.id,
            "message_html": message,
            "buttons": buttons,
            "support_contact": SUPPORT_CONTACT,
        },
    )


@app.post("/act")
def act(action: str = Form(...), role: str = Form("player"), as_id: str = Form("1", alias="as")):
    """Применить действие текущего пользователя и вернуться (Post/Redirect/Get)."""
    user = dev_current_user(as_id)
    apply_action(action, user)
    role = "admin" if role == "admin" else "player"
    return RedirectResponse(url=f"/?role={role}&as={as_id}", status_code=303)
