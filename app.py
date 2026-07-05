"""Веб-версия игры «Киллер / Папарацци».

Архитектура входа:
  /                     — эмуляция чата с ботом: поле «id» → открыть чат
  /chat/<username>      — переписка с ботом: история + кнопка /start
  /chat/<username>/start— бот присылает одноразовую ссылку для входа (magic-link)
  /login?token=...      — проверка токена → подписанная cookie-сессия → /app
  /app                  — игровой дашборд (доступен только вошедшему пользователю)

Кнопки — обычные HTML-формы (POST → изменение состояния → редирект), без JavaScript.
Запуск:  ./start.sh   (см. README.md)
"""
import os
import re
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

import db  # noqa: F401 — импорт инициализирует БД
from core import chat, auth, admin_log
from core.game import Game
from core.user import User

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Killer / Paparazzi — web")

# Подписанная cookie-сессия. Секрет — из переменной окружения (в проде обязателен).
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

SUPPORT_CONTACT = "@parar020100"

# Доп. вопрос при регистрации (как EXTRA_INFO в боте). Пусто = шаг отключён.
EXTRA_QUESTION = ""

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*$")


def validate_real_name(raw: str):
    """Вернуть (имя, None) при успехе или (None, текст_ошибки)."""
    name = " ".join((raw or "").split())  # схлопнуть пробелы
    if not (2 <= len(name) <= 50):
        return None, "Имя должно быть от 2 до 50 символов."
    if not _NAME_RE.match(name):
        return None, "Имя может содержать только буквы, пробел и дефис."
    return name, None


# ---------------------------------------------------------------------------
# Текущий пользователь (по сессии)
# ---------------------------------------------------------------------------

def current_user(request: Request):
    uid = request.session.get("uid")
    return User.by_id(uid) if uid else None


_URL_RE = re.compile(r"(https?://[^\s]+)")


def linkify(text: str) -> Markup:
    """Экранировать текст и превратить ссылки в кликабельные."""
    out = _URL_RE.sub(
        lambda m: f'<a href="{escape(m.group(1))}">{escape(m.group(1))}</a>',
        escape(text),
    )
    return Markup(out.replace("\n", "<br>"))


# ---------------------------------------------------------------------------
# Кнопки и экраны игрового дашборда
# ---------------------------------------------------------------------------

def _btn(label, action=None, kind="", full=False, href=None):
    return {"label": label, "action": action, "kind": kind, "full": full, "href": href}


def player_view(user: User):
    game = Game()
    lines = [
        f'<span class="hi">Привет, {escape(user.get_name())}!</span>',
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
        buttons.append(_btn("🟢 Зарегистрироваться", href="/app/join", kind="primary", full=True))

    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return "\n".join(lines), buttons


def admin_view(user: User):
    game = Game()
    message = "\n".join([
        f'<span class="hi">Привет, {escape(user.get_name())}!</span>',
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

    buttons.append(_btn("👥 Список пользователей", href="/app/users", full=True))
    buttons.append(_btn("📋 Журнал (admin log)", href="/admin-log", full=True))
    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return message, buttons


# ---------------------------------------------------------------------------
# Статусы пользователя для списка (эмодзи как в боте, h_list.py)
# ---------------------------------------------------------------------------

def bot_status_emoji(user: User) -> str:
    if user.is_admin():
        return "👑"
    idents = user.identities()
    if idents and all(i.is_muted() for i in idents):
        return "🚫"
    return "👤"


def game_status_emoji(user: User, game: Game) -> str:
    if not game.is_started():
        return "✅" if user.is_player() else "🔴"
    if not user.is_player():
        return "👀"
    if user.is_alive():
        return "💛" if user.get_killed_by() else "💚"
    return "♻️" if user.is_queued_for_revival() else "☠️"


def user_row(user: User, game: Game) -> dict:
    un = user.get_username()
    return {
        "id": user.id,
        "status": bot_status_emoji(user) + game_status_emoji(user, game),
        "order": user.get_game_order() if user.is_player() else None,
        "kills": user.get_score() if user.is_player() else None,
        "nick": f"@{un}" if un else "—",
        "name": user.get_name(),
        "is_admin": user.is_admin(),
    }


# ---------------------------------------------------------------------------
# Действия кнопок дашборда
# ---------------------------------------------------------------------------

def _broadcast(text, players_only=True):
    users = User.all_players() if players_only else User.all()
    for u in users:
        u.notify(text)


def do_join(user: User):
    """Завести игрока в игру (после успешной валидации формы регистрации)."""
    game = Game()
    user.join(alive=not game.is_started())
    admin_log.log(f"➕ {user.get_name()} зарегистрировал(ся/ась) в игре")
    user.notify("✅ Вы зарегистрированы в игре «Папарацци».")


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
    elif action == "leave":
        if user.is_player():
            user.leave()
            admin_log.log(f"➖ {who} вышел(ла) из игры")
            user.notify("🚪 Вы вышли из игры.")
    # "noop" / незнакомое — просто перерисовать


# ---------------------------------------------------------------------------
# Чат с ботом (эмуляция) + вход по ссылке
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def chat_entry(request: Request):
    return templates.TemplateResponse(request, "chat_entry.html",
                                      {"support_contact": SUPPORT_CONTACT})


@app.get("/chat")
def chat_open(id: str = ""):
    username = chat.normalize(id)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/chat/{username}", status_code=303)


@app.get("/chat/{username}", response_class=HTMLResponse)
def chat_view(request: Request, username: str):
    username = chat.normalize(username)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    # Открытие чата создаёт профиль при необходимости (как первый контакт с ботом).
    User.get_or_create_by_tg(username, username=username, name=username)
    messages = [
        {"tag": m["tag"] or "bot", "ts": m["ts"], "html": linkify(m["body"])}
        for m in chat.read(username)
    ]
    return templates.TemplateResponse(
        request, "chat.html",
        {"username": username, "messages": messages},
    )


@app.post("/chat/{username}/start")
def chat_start(request: Request, username: str):
    username = chat.normalize(username)
    user = User.get_or_create_by_tg(username, username=username, name=username)
    # Постоянная ссылка привязана к каналу (tg-идентичности), а не к пользователю:
    # у него может быть отдельная постоянная ссылка и для VK.
    ident = user.identity("tg")
    reissued = auth.has_permanent_token(ident.id)
    chat.add_user_message(username, "/start")
    token = auth.set_permanent_token(ident.id)
    link = f"{request.base_url}login?token={token}"
    note = "Прежняя ссылка больше не работает.\n" if reissued else ""
    chat.add_bot_message(
        username,
        "Ваша постоянная ссылка для входа в игру «Папарацци» "
        "(сохраните её в закладки):\n"
        f"{link}\n"
        f"{note}Она работает всегда и не имеет срока. Никому её не пересылайте — "
        "по ней входят в вашу учётку. Нажмёте /start ещё раз — будет выдана новая, "
        "а старая перестанет работать.",
    )
    return RedirectResponse(url=f"/chat/{username}", status_code=303)


@app.get("/login")
def login(request: Request, token: str = ""):
    uid = auth.resolve_permanent_token(token)
    if uid is None:
        return HTMLResponse(
            "<h3>Ссылка недействительна или устарела.</h3>"
            "<p>Вернитесь в чат и нажмите <b>/start</b> ещё раз.</p>",
            status_code=400,
        )
    request.session["uid"] = uid
    return RedirectResponse(url="/app", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


# ---------------------------------------------------------------------------
# Игровой дашборд (только для вошедших)
# ---------------------------------------------------------------------------

@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request, role: str = "player"):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)

    if role == "admin" and user.is_admin():
        message, buttons = admin_view(user)
    else:
        role = "player"
        message, buttons = player_view(user)

    return templates.TemplateResponse(
        request, "index.html",
        {
            "role": role,
            "is_admin": user.is_admin(),
            "user_name": user.get_name(),
            "message_html": message,
            "buttons": buttons,
            "support_contact": SUPPORT_CONTACT,
        },
    )


def user_menu_buttons(target: User):
    """Все действия админа над пользователем. todo=True → пока не реализовано."""
    buttons = []
    # --- реализованные ---
    if not target.is_admin():
        buttons.append({"label": "👑 Выдать админа", "action": "promote"})
    elif not target.is_default_admin():
        buttons.append({"label": "🧹 Забрать админа", "action": "demote"})
    # --- запланированные (зачёркнуты) ---
    todo = [
        "✍️ Написать игроку", "📊 Инфо о пользователе", "🔪 Устранить", "♻️ Оживить",
        "🎁 Подарить жизнь", "🚫 Отобрать жизнь", "🔀 Сменить порядок в круге",
        "💯 Изменить счёт", "✅ Засчитать поимку", "❌ Отклонить поимку",
        "👋 Удалить из игры", "🗑️ Удалить из системы",
    ]
    buttons.extend({"label": t, "todo": True} for t in todo)
    return buttons


@app.get("/app/users/{uid}", response_class=HTMLResponse)
def user_detail(request: Request, uid: int):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return RedirectResponse(url="/app", status_code=303)
    target = User.by_id(uid)
    if target is None:
        return RedirectResponse(url="/app/users", status_code=303)

    game = Game()
    idents = [{"label": i.label(), "muted": i.is_muted(),
               "log": i.is_admin_log_enabled()} for i in target.identities()]
    info = {
        "id": target.id,
        "name": target.get_name(),
        "username": target.get_username(),
        "real_name": target.get_real_name(),
        "extra_info": target.get_extra_info(),
        "status": bot_status_emoji(target) + game_status_emoji(target, game),
        "is_admin": target.is_admin(),
        "is_default_admin": target.is_default_admin(),
        "is_player": target.is_player(),
        "is_alive": target.is_alive(),
        "score": target.get_score(),
        "order": target.get_game_order(),
        "target": target.get_target(),
        "killed_by": target.get_killed_by(),
        "identities": idents,
    }
    return templates.TemplateResponse(
        request, "user_detail.html",
        {"u": info, "buttons": user_menu_buttons(target)},
    )


@app.post("/app/users/{uid}/act")
def user_action(request: Request, uid: int, action: str = Form(...)):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return RedirectResponse(url="/app", status_code=303)
    target = User.by_id(uid)
    if target is None:
        return RedirectResponse(url="/app/users", status_code=303)

    if action == "promote" and not target.is_admin():
        target.set_admin(True)
        admin_log.log(f"👑 {admin.get_name()} выдал(а) права админа: {target.get_name()}")
        target.notify("👑 Вам выданы права администратора игры.")
    elif action == "demote" and target.is_admin() and not target.is_default_admin():
        target.set_admin(False)
        admin_log.log(f"🧹 {admin.get_name()} снял(а) права админа: {target.get_name()}")
        target.notify("Права администратора сняты.")
    return RedirectResponse(url=f"/app/users/{uid}", status_code=303)


@app.get("/app/users", response_class=HTMLResponse)
def users_list(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return RedirectResponse(url="/app", status_code=303)

    game = Game()
    all_users = User.all()
    players = [u for u in all_users if u.is_player()]
    players.sort(key=lambda u: (u.get_game_order() is None, u.get_game_order() or 0, u.id))
    non_players = [u for u in all_users if not u.is_player()]

    return templates.TemplateResponse(
        request, "users.html",
        {
            "total_users": len(all_users),
            "total_players": len(players),
            "game_started": game.is_started(),
            "players": [user_row(u, game) for u in players],
            "non_players": [user_row(u, game) for u in non_players],
        },
    )


def _render_register(request, user, game, values, errors):
    return templates.TemplateResponse(
        request, "register.html",
        {
            "needs_password": bool(game.get_password()),
            "extra_question": EXTRA_QUESTION,
            "values": values,
            "errors": errors,
        },
    )


@app.get("/app/join", response_class=HTMLResponse)
def join_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    game = Game()
    if user.is_player() or not game.is_registration_open():
        return RedirectResponse(url="/app", status_code=303)
    values = {"real_name": user.get_real_name(), "extra": user.get_extra_info()}
    return _render_register(request, user, game, values, {})


@app.post("/app/join", response_class=HTMLResponse)
def join_submit(request: Request, real_name: str = Form(""),
                password: str = Form(""), extra: str = Form("")):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    game = Game()
    if user.is_player() or not game.is_registration_open():
        return RedirectResponse(url="/app", status_code=303)

    errors = {}
    name, err = validate_real_name(real_name)
    if err:
        errors["real_name"] = err
    if game.get_password() and password != game.get_password():
        errors["password"] = "Неверный пароль игры."
    if EXTRA_QUESTION and not extra.strip():
        errors["extra"] = "Пожалуйста, ответьте на вопрос."

    if errors:
        values = {"real_name": real_name, "extra": extra}
        return _render_register(request, user, game, values, errors)

    user.set_real_name(name)
    if EXTRA_QUESTION:
        user.set_extra_info(extra.strip())
    do_join(user)
    return RedirectResponse(url="/app", status_code=303)


@app.post("/act")
def act(request: Request, action: str = Form(...), role: str = Form("player")):
    """Применить действие текущего пользователя и вернуться (Post/Redirect/Get)."""
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    apply_action(action, user)
    role = "admin" if role == "admin" and user.is_admin() else "player"
    return RedirectResponse(url=f"/app?role={role}", status_code=303)


# ---------------------------------------------------------------------------
# Общий журнал администраторов (один файл на всю игру)
# ---------------------------------------------------------------------------

@app.get("/admin-log", response_class=HTMLResponse)
def admin_log_view(request: Request):
    user = current_user(request)
    if user is None or not user.is_admin():
        return RedirectResponse(url="/app", status_code=303)
    return templates.TemplateResponse(
        request, "admin_log.html", {"messages": admin_log.read_messages()},
    )


@app.post("/admin-log/clear")
def admin_log_clear(request: Request):
    user = current_user(request)
    if user and user.is_admin():
        admin_log.clear()
    return RedirectResponse(url="/admin-log", status_code=303)
