"""Веб-версия игры «Киллер / Папарацци» — каркас (FastAPI + Jinja2).

Состояние игры, игроки и их каналы связи хранятся в SQLite (db.py, core/).

Входа по аккаунту пока нет — вместо него DEV-режим на **тестовой** платформе:
текущий пользователь выбирается параметром `?as=<N>` в адресе (по умолчанию 1).
Каждое N — отдельная тестовая идентичность (platform='test'), поэтому в разных
вкладках можно открыть разных пользователей (`?as=1`, `?as=2`, ...), не заводя
реальных аккаунтов Telegram/VK. Уведомления такому пользователю падают в его
«входящие» — их видно на странице /inbox/<N>.

Кнопки — обычные HTML-формы (POST → изменение состояния → редирект), без JavaScript.

Запуск:  ./start.sh   (см. README.md)
"""
from pathlib import Path

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import db  # noqa: F401 — импорт инициализирует БД
from core import inbox, admin_log
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
    """Берёт/создаёт тестового пользователя с test-идентичностью uid = N."""
    try:
        uid = str(int(as_id))
    except (TypeError, ValueError):
        uid = "1"
    return User.get_or_create_by_test(uid, username=f"test{uid}", name=f"Тест {uid}")


# ---------------------------------------------------------------------------
# Кнопки и экраны
# ---------------------------------------------------------------------------

def _btn(label, action=None, kind="", full=False, href=None):
    return {"label": label, "action": action, "kind": kind, "full": full, "href": href}


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


def admin_view(user: User, as_id: str):
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

    buttons.append(_btn("📋 Журнал (admin log)",
                        href=f"/admin-log?role=admin&as={as_id}", full=True))
    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return message, buttons


# ---------------------------------------------------------------------------
# Действия кнопок
# ---------------------------------------------------------------------------

def _broadcast(text, players_only=True):
    users = User.all_players() if players_only else User.all()
    for u in users:
        u.notify(text)


def apply_action(action: str, user: User):
    game = Game()
    who = user.get_name()
    if action == "open_reg":
        game.open_registration()
        admin_log.log(f"🟡 {who} открыл(а) регистрацию")
        _broadcast("🟡 Открыта регистрация на игру «Папарацци». "
                   "Зайдите на сайт, чтобы зарегистрироваться!", players_only=False)
    elif action == "close_reg":
        game.close_registration()
        admin_log.log(f"🚫 {who} закрыл(а) регистрацию")
    elif action == "start_game":
        game.start()
        admin_log.log(f"🟢 {who} запустил(а) игру ({game.count_players()} игроков)")
        _broadcast("🟢 Игра началась! Проверьте свою цель на сайте.")
    elif action == "stop_game":
        game.stop()
        admin_log.log(f"🔴 {who} остановил(а) игру")
        _broadcast("🔴 Игра остановлена администратором.")
    elif action == "join":
        if game.is_registration_open() and not user.is_player():
            user.join(alive=not game.is_started())
            admin_log.log(f"➕ {who} зарегистрировал(ся/ась) в игре")
            user.notify("✅ Вы зарегистрированы в игре «Папарацци».")
    elif action == "leave":
        if user.is_player():
            user.leave()
            admin_log.log(f"➖ {who} вышел(ла) из игры")
            user.notify("🚪 Вы вышли из игры.")
    # "noop" / незнакомое — просто перерисовать


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, role: str = "player", as_id: str = Query("1", alias="as")):
    user = dev_current_user(as_id)
    if role == "admin":
        message, buttons = admin_view(user, as_id)
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


# ---------------------------------------------------------------------------
# DEV: «входящие» тестовых идентичностей (симуляция уведомлений бота)
# ---------------------------------------------------------------------------

@app.get("/inbox/{uid}", response_class=HTMLResponse)
def inbox_view(request: Request, uid: str, role: str = Query("player")):
    messages = inbox.read_messages(uid)
    return templates.TemplateResponse(
        request,
        "inbox.html",
        {
            "uid": uid,
            "role": role if role == "admin" else "player",
            "messages": messages,
            "others": inbox.list_inboxes(),
        },
    )


@app.post("/inbox/{uid}/clear")
def inbox_clear(uid: str, role: str = Form("player")):
    inbox.clear_inbox(uid)
    return RedirectResponse(url=f"/inbox/{uid}?role={role}", status_code=303)


# ---------------------------------------------------------------------------
# Общий журнал администраторов (один файл на всю игру)
# ---------------------------------------------------------------------------

@app.get("/admin-log", response_class=HTMLResponse)
def admin_log_view(request: Request, as_id: str = Query("1", alias="as")):
    return templates.TemplateResponse(
        request,
        "admin_log.html",
        {"as_id": as_id, "messages": admin_log.read_messages()},
    )


@app.post("/admin-log/clear")
def admin_log_clear(as_id: str = Form("1", alias="as")):
    admin_log.clear()
    return RedirectResponse(url=f"/admin-log?as={as_id}", status_code=303)
