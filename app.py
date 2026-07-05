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
import re
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

import config
import db  # noqa: F401 — импорт инициализирует БД
from core import chat, auth, admin_log
from core.game import Game
from core.user import User

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Killer / Paparazzi — web")

# Подписанная cookie-сессия (секрет — в config, из переменной окружения).
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)

SUPPORT_CONTACT = config.SUPPORT_CONTACT

# Доп. вопрос при регистрации (как EXTRA_INFO в боте). Пусто = шаг отключён.
EXTRA_QUESTION = config.EXTRA_INFO[1] if config.EXTRA_INFO else ""

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*$")


def validate_real_name(raw: str):
    """Вернуть (имя, None) при успехе или (None, текст_ошибки)."""
    name = " ".join((raw or "").split())  # схлопнуть пробелы
    if not (config.NAME_MIN_LEN <= len(name) <= config.NAME_MAX_LEN):
        return None, f"Имя должно быть от {config.NAME_MIN_LEN} до {config.NAME_MAX_LEN} символов."
    if not _NAME_RE.match(name):
        return None, "Имя может содержать только буквы, пробел и дефис."
    return name, None


# ---------------------------------------------------------------------------
# Текущий пользователь (по сессии или отладочному ?as=<id>)
# ---------------------------------------------------------------------------

def _dev_as(request: Request):
    """id пользователя из ?as= (только если включён ALLOW_DEV_LOGIN), иначе None.

    Это ключ к «разным вкладкам»: identity живёт в URL, а не в общей на браузер
    cookie, поэтому каждая вкладка независима. Значение НЕ пишется в cookie.
    """
    if not config.ALLOW_DEV_LOGIN:
        return None
    raw = request.query_params.get("as")
    return int(raw) if raw and raw.isdigit() else None


def current_user(request: Request):
    # Отладочный ?as= имеет приоритет над cookie и не трогает сессию.
    as_uid = _dev_as(request)
    if as_uid is not None:
        return User.by_id(as_uid)
    uid = request.session.get("uid")
    return User.by_id(uid) if uid else None


def _link_fn(as_uid):
    """Вернуть функцию, дописывающую ?as=<id> к внутренним ссылкам/формам."""
    def link(path: str) -> str:
        if as_uid is None:
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}as={as_uid}"
    return link


def _ctx(request: Request, **extra):
    """Контекст шаблона + прокидывание отладочного ?as= во все ссылки страницы."""
    as_uid = _dev_as(request)
    return {
        "as_uid": as_uid,
        "allow_dev": config.ALLOW_DEV_LOGIN,
        "link": _link_fn(as_uid),
        **extra,
    }


def _redirect(request: Request, url: str, status_code: int = 303):
    """RedirectResponse, сохраняющий отладочный ?as= (чтобы вкладка не «слетала»)."""
    return RedirectResponse(url=_link_fn(_dev_as(request))(url), status_code=status_code)


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


# --- правила игры (HTML-файл из config.RULES_FILENAME) ----------------------

def _rules_path():
    return (BASE_DIR / config.RULES_FILENAME) if config.RULES_FILENAME else None


def has_rules() -> bool:
    p = _rules_path()
    return bool(p and p.exists())


def read_rules_html():
    p = _rules_path()
    if not p:
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def notify_target(user: User):
    """Сообщить игроку его текущую цель (в эмулированный чат)."""
    target = user.get_target_user()
    if target:
        user.notify(f"🎯 Твоя цель: {target.get_name()}\n"
                    "Поймай её (сделай селфи) раньше, чем поймают тебя!")


def player_view(user: User):
    game = Game()
    lines = [
        f'<span class="hi">Привет, {escape(user.get_name())}!</span>',
        "📷 Добро пожаловать в игру Папарацци!",
        "",
        '<span class="divider">══ 📋 Статус игры ══</span>',
        game.status_html(),
    ]
    buttons = []

    if not user.is_player():
        lines.append("❌ <em>Вы пока не участвуете в игре</em>")
        lines.append(f"👥 Игроков: <strong>{game.count_players()}</strong>")
        if game.is_registration_open():
            buttons.append(_btn("🟢 Зарегистрироваться", href="/app/join",
                                kind="primary", full=True))
    elif not game.is_started():
        lines.append("✅ <em>Вы зарегистрированы, ждём старта игры</em>")
        lines.append(f"👥 Игроков: <strong>{game.count_players()}</strong>")
        buttons.append(_btn("🔴 Выйти из игры", "leave", "danger", full=True))
    else:
        # Игра идёт — показываем игровое состояние конкретного игрока.
        lines += _player_game_lines(user)
        buttons += _player_game_buttons(user)
        if game.is_paused():
            buttons.append(_btn("🏁 Итоги игры", href="/app/results", full=True))

    if has_rules():
        buttons.append(_btn("📜 Правила", href="/rules", full=True))
    buttons.append(_btn("🔄 Обновить", "noop", full=True))
    return "\n".join(lines), buttons


def _player_game_lines(user: User):
    """Строки статуса для игрока в идущей игре."""
    game = Game()
    lines = [f"🔪 Ваш счёт поимок: <strong>{user.get_score()}</strong>",
             f"💚 Живых игроков: <strong>{game.count_alive()}</strong>"]
    if not user.is_alive():
        lines.append("")
        lines.append("☠️ <em>Вы выбыли из игры.</em> Спасибо за участие!")
        return lines
    if user.is_being_caught():
        catcher = user.get_murderer()
        who = escape(catcher.get_name()) if catcher else "другой игрок"
        lines.append("")
        lines.append(f"📸 <strong>{who}</strong> заявил(а), что поймал(а) вас!")
        lines.append("Подтвердите или отклоните это ниже.")
        return lines
    target = user.get_target_user()
    tname = escape(target.get_name()) if target else "—"
    lines.append("")
    lines.append(f'<span class="divider">══ 🎯 Ваша цель ══</span>')
    lines.append(f"🎯 <strong>{tname}</strong>")
    if _awaiting_confirmation(user):
        lines.append("⏳ <em>Вы заявили о поимке — ждём подтверждения цели.</em>")
    return lines


def _awaiting_confirmation(user: User) -> bool:
    """Игрок сообщил о поимке своей цели и ждёт её подтверждения."""
    target = user.get_target_user()
    return bool(target and target.is_being_caught()
                and target.get_murderer() and target.get_murderer().id == user.id)


def _player_game_buttons(user: User):
    """Кнопки для игрока в идущей игре — зависят от его состояния в цикле поимок."""
    if not user.is_alive():
        return []
    if user.is_being_caught():
        return [_btn("✅ Подтвердить поимку", "confirm_capture", "primary", full=True),
                _btn("🚫 Это не так", "deny_capture", "danger", full=True)]
    if user.is_awaiting_confirmation():
        return [_btn("✖️ Отменить заявку о поимке", "cancel_capture", full=True)]
    if user.get_target_user():
        return [_btn("📸 Сообщить о поимке цели", "report_capture", "primary", full=True)]
    return []


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
    if not game.is_started():
        buttons.append(_btn("▶️ Запустить игру", "start_game", "primary"))
    else:
        if game.is_paused():
            buttons.append(_btn("▶️ Продолжить", "resume", "primary"))
        else:
            buttons.append(_btn("⏸️ Пауза", "pause"))
        buttons.append(_btn("🏁 Завершить (итоги)", "end_game", "danger"))
    if game.is_registration_open():
        buttons.append(_btn("🚫 Закрыть регистрацию", "close_reg"))
    else:
        buttons.append(_btn("✅ Открыть регистрацию", "open_reg"))

    if game.is_started():
        buttons.append(_btn("📊 Промежуточные итоги", href="/app/results", full=True))
    buttons.append(_btn("👥 Список пользователей", href="/app/users", full=True))
    buttons.append(_btn("📢 Рассылка", href="/broadcast", full=True))
    buttons.append(_btn("🔑 Пароль регистрации", href="/app/password", full=True))
    buttons.append(_btn("📋 Журнал (admin log)", href="/admin-log", full=True))
    if has_rules():
        buttons.append(_btn("📜 Правила", href="/rules", full=True))
    buttons.append(_btn("♻️ Сбросить игру", "reset_game", "danger", full=True))
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


# Аудитории адресной рассылки (админ → выбранная группа).
# ключ → (метка для UI, функция-выборка -> список User)
BROADCAST_AUDIENCES = {
    "all":         ("Все пользователи",       User.all),
    "players":     ("Все игроки",             User.all_players),
    "alive":       ("Живые игроки",           User.alive_players),
    "dead":        ("Выбывшие игроки",        User.dead_players),
    "non_players": ("Не в игре (зрители)",    User.non_players),
    "admins":      ("Администраторы",         User.all_admins),
}


def do_join(user: User):
    """Завести игрока в игру (после успешной валидации формы регистрации)."""
    game = Game()
    user.join(alive=not game.is_started())
    admin_log.log(f"➕ {user.get_name()} зарегистрировал(ся/ась) в игре")
    user.notify("✅ Вы зарегистрированы в игре «Папарацци».")


_MEDALS = ["🥇", "🥈", "🥉"]


def _results_rows(groups):
    """Плоский список призёров из сгруппированного топа: [{medal,name,nick,score}]."""
    rows = []
    for i, grp in enumerate(groups[:3]):
        for p in grp:
            rows.append({"medal": _MEDALS[i], "name": p.get_name(),
                         "nick": p.get_username(), "score": p.get_score()})
    return rows


def results_data():
    game = Game()
    winner = game.get_winner()
    return {
        "alive_count": game.count_alive(),
        "total": game.count_players(),
        "is_over": game.count_players() > 0 and game.count_alive() <= 1,
        "winner_name": winner.get_name() if winner else None,
        "top_alive": _results_rows(game.top_alive_grouped()),
        "top_dead": _results_rows(game.top_dead_grouped()),
    }


def results_text() -> str:
    """Простой текстовый вариант итогов для рассылки в чат."""
    d = results_data()
    lines = []
    if d["winner_name"]:
        lines.append(f"🏆 Победитель: {d['winner_name']}!")
    else:
        lines.append(f"В живых осталось {d['alive_count']} из {d['total']}.")
    if d["top_alive"]:
        lines.append("\nЛучшие игроки:")
        for r in d["top_alive"]:
            nick = f" (@{r['nick']})" if r["nick"] else ""
            lines.append(f"{r['medal']} {r['name']}{nick} — поймал(а) {r['score']}")
    if d["top_dead"]:
        lines.append("\nЛучшие из выбывших:")
        for r in d["top_dead"]:
            nick = f" (@{r['nick']})" if r["nick"] else ""
            lines.append(f"😵 {r['medal']} {r['name']}{nick} — поймал(а) {r['score']}")
    lines.append("\nСпасибо за игру!")
    return "\n".join(lines)


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
        ok, msg = game.start()
        if ok:
            admin_log.log(f"🟢 {who} запустил(а) игру ({game.count_players()} игроков)")
            _broadcast("🟢 Игра «Папарацци» началась! Узнайте свою цель на сайте.")
            for ply in User.alive_players():
                notify_target(ply)
        else:
            admin_log.log(f"⚠️ {who} не смог(ла) запустить игру: {msg}")
    elif action == "stop_game":
        game.stop()
        admin_log.log(f"🔴 {who} остановил(а) игру")
        _broadcast("🔴 Игра остановлена администратором.")
    elif action == "pause":
        if game.is_started() and not game.is_paused():
            game.pause()
            admin_log.log(f"⏸️ {who} поставил(а) игру на паузу")
            _broadcast("⏸️ Игра поставлена на паузу администратором.")
    elif action == "resume":
        if game.is_started() and game.is_paused():
            game.resume()
            admin_log.log(f"▶️ {who} возобновил(а) игру")
            _broadcast("▶️ Игра продолжается!")
    elif action == "reset_game":
        game.reset()
        admin_log.log(f"♻️ {who} сбросил(а) игру")
        _broadcast("♻️ Игра сброшена администратором. Спасибо за участие!",
                   players_only=False)
    elif action == "end_game":
        game.pause()
        admin_log.log(f"🏁 {who} завершил(а) игру, разосланы итоги")
        _broadcast("🏁 Игра завершена! Итоги:\n\n" + results_text(),
                   players_only=False)
    elif action == "leave":
        if user.is_player():
            user.leave()
            admin_log.log(f"➖ {who} вышел(ла) из игры")
            user.notify("🚪 Вы вышли из игры.")
    elif action == "report_capture":
        user.attempt_capture()
    elif action == "cancel_capture":
        user.cancel_capture()
    elif action == "confirm_capture":
        user.confirm_capture()
    elif action == "deny_capture":
        user.deny_capture()
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


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    html_text = read_rules_html()
    if html_text is None:
        return HTMLResponse(
            "<h3>Для этой игры правила пока не заданы.</h3>"
            "<p>Свяжитесь с администратором.</p>",
            status_code=404,
        )
    return templates.TemplateResponse(
        request, "rules.html", _ctx(request, rules_html=Markup(html_text)),
    )


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
        _ctx(
            request,
            role=role,
            is_admin=user.is_admin(),
            user_name=user.get_name(),
            message_html=message,
            buttons=buttons,
            support_contact=SUPPORT_CONTACT,
        ),
    )


@app.get("/app/results", response_class=HTMLResponse)
def results_page(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "results.html", _ctx(request, is_admin=user.is_admin(), **results_data()),
    )


@app.get("/app/password", response_class=HTMLResponse)
def password_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")
    return templates.TemplateResponse(
        request, "password.html",
        _ctx(request, current=Game().get_password(), saved=False),
    )


@app.post("/app/password", response_class=HTMLResponse)
def password_save(request: Request, password: str = Form("")):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")
    value = password.strip()
    Game().set_password(value)
    admin_log.log(f"🔑 {user.get_name()} "
                  + ("задал(а) пароль регистрации" if value else "убрал(а) пароль регистрации"))
    return templates.TemplateResponse(
        request, "password.html", _ctx(request, current=value, saved=True),
    )


def user_menu_buttons(target: User):
    """Действия админа над пользователем, зависят от состояния игрока.

    todo=True → пока намеренно не реализовано (зачёркнуто).
    kind → цвет; иначе обычная кнопка.
    """
    b = []

    def add(label, action, kind=""):
        b.append({"label": label, "action": action, "kind": kind})

    # роль
    if not target.is_admin():
        add("👑 Выдать админа", "promote")
    elif not target.is_default_admin():
        add("🧹 Забрать админа", "demote")

    # модерация текущей заявки о поимке
    if target.is_being_caught():
        add("✅ Засчитать поимку", "force_accept", "primary")
        add("❌ Отклонить поимку", "force_deny", "danger")

    # игровые действия (только для участников)
    if target.is_player():
        if target.is_alive():
            add("🔪 Устранить", "kill", "danger")
        else:
            add("♻️ Оживить", "revive", "primary")
            if target.is_queued_for_revival():
                add("🚫 Отобрать шанс жизни", "take_life")
            else:
                add("🎁 Подарить жизнь", "give_life")
        add("🔀 Сменить порядок в круге", "randomize_order")
        add("👋 Удалить из игры", "kick", "danger")

    # удаление из системы (нельзя себя и дефолт-админа — проверяется в обработчике)
    if not target.is_default_admin():
        add("🗑️ Удалить из системы", "delete", "danger")

    # ещё не реализовано (личные сообщения отложены)
    b.append({"label": "✍️ Написать игроку", "todo": True})
    return b


# Действия над пользователем: имя → (метод User, меняет ли состав живых)
_USER_ACTIONS = {
    "kill":          (User.admin_kill,          True),
    "revive":        (User.admin_revive,        True),
    "kick":          (User.admin_kick,          True),
    "give_life":     (User.give_life,           False),
    "take_life":     (User.take_life,           False),
    "randomize_order": (User.admin_randomize_order, True),
    "force_accept":  (User.admin_force_accept,  True),
    "force_deny":    (User.admin_force_deny,    False),
}


def _snapshot_targets():
    return {u.id: u.get_target_id() for u in User.alive_players()}


def _notify_retargets(before):
    """Сообщить новую цель тем живым игрокам, у кого она изменилась."""
    for u in User.alive_players():
        new = u.get_target_id()
        if new and before.get(u.id) != new:
            notify_target(u)


@app.get("/app/users/{uid}", response_class=HTMLResponse)
def user_detail(request: Request, uid: int):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return _redirect(request, "/app")
    target = User.by_id(uid)
    if target is None:
        return _redirect(request, "/app/users")

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
        _ctx(request, u=info, buttons=user_menu_buttons(target),
             can_set_score=target.is_player()),
    )


@app.post("/app/users/{uid}/act")
def user_action(request: Request, uid: int, action: str = Form(...),
                value: str = Form("")):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return _redirect(request, "/app")
    target = User.by_id(uid)
    if target is None:
        return _redirect(request, "/app/users")

    if action == "promote" and not target.is_admin():
        target.set_admin(True)
        admin_log.log(f"👑 {admin.get_name()} выдал(а) права админа: {target.get_name()}")
        target.notify("👑 Вам выданы права администратора игры.")
    elif action == "demote" and target.is_admin() and not target.is_default_admin():
        target.set_admin(False)
        admin_log.log(f"🧹 {admin.get_name()} снял(а) права админа: {target.get_name()}")
        target.notify("Права администратора сняты.")
    elif action == "set_score" and target.is_player():
        try:
            target.admin_set_score(admin, max(0, int(value)))
        except (ValueError, TypeError):
            pass
    elif action == "delete":
        # нельзя удалить себя или дефолт-админа
        if target.id != admin.id and not target.is_default_admin():
            before = _snapshot_targets()
            target.delete_from_system(admin)
            _notify_retargets(before)
            return _redirect(request, "/app/users")
    elif action in _USER_ACTIONS:
        method, structural = _USER_ACTIONS[action]
        before = _snapshot_targets() if structural else None
        method(target, admin)
        if structural:
            _notify_retargets(before)
    return _redirect(request, f"/app/users/{uid}")


@app.get("/app/users", response_class=HTMLResponse)
def users_list(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")

    game = Game()
    all_users = User.all()
    players = [u for u in all_users if u.is_player()]
    players.sort(key=lambda u: (u.get_game_order() is None, u.get_game_order() or 0, u.id))
    non_players = [u for u in all_users if not u.is_player()]

    return templates.TemplateResponse(
        request, "users.html",
        _ctx(
            request,
            total_users=len(all_users),
            total_players=len(players),
            game_started=game.is_started(),
            players=[user_row(u, game) for u in players],
            non_players=[user_row(u, game) for u in non_players],
        ),
    )


def _render_register(request, user, game, values, errors):
    return templates.TemplateResponse(
        request, "register.html",
        _ctx(
            request,
            needs_password=bool(game.get_password()),
            extra_question=EXTRA_QUESTION,
            values=values,
            errors=errors,
        ),
    )


@app.get("/app/join", response_class=HTMLResponse)
def join_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    game = Game()
    if user.is_player() or not game.is_registration_open():
        return _redirect(request, "/app")
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
        return _redirect(request, "/app")

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
    return _redirect(request, "/app")


@app.post("/act")
def act(request: Request, action: str = Form(...), role: str = Form("player")):
    """Применить действие текущего пользователя и вернуться (Post/Redirect/Get)."""
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    apply_action(action, user)
    role = "admin" if role == "admin" and user.is_admin() else "player"
    return _redirect(request, f"/app?role={role}")


# ---------------------------------------------------------------------------
# Адресная рассылка (админ → выбранная группа людей)
# ---------------------------------------------------------------------------

def _audience_options():
    return [{"key": k, "label": label, "count": len(fn())}
            for k, (label, fn) in BROADCAST_AUDIENCES.items()]


def _render_broadcast(request, values, errors, result=None):
    return templates.TemplateResponse(
        request, "broadcast.html",
        _ctx(request, audiences=_audience_options(),
             values=values, errors=errors, result=result),
    )


@app.get("/broadcast", response_class=HTMLResponse)
def broadcast_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")
    return _render_broadcast(request, {"audience": "players", "text": ""}, {})


@app.post("/broadcast", response_class=HTMLResponse)
def broadcast_send(request: Request, audience: str = Form("players"),
                   text: str = Form("")):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return _redirect(request, "/app")

    values = {"audience": audience, "text": text}
    errors = {}
    body = text.strip()
    if not body:
        errors["text"] = "Введите текст сообщения."
    if audience not in BROADCAST_AUDIENCES:
        errors["audience"] = "Выберите группу получателей."
    if errors:
        return _render_broadcast(request, values, errors)

    label, fetch = BROADCAST_AUDIENCES[audience]
    recipients = fetch()
    message = f"📢 Объявление от организаторов:\n{body}"
    delivered = 0
    for u in recipients:
        u.notify(message)
        delivered += 1

    admin_log.log(f"📢 {admin.get_name()} разослал(а) сообщение группе "
                  f"«{label}» ({delivered} чел.):\n{body}")
    result = {"label": label, "count": delivered}
    # После отправки очищаем текст, аудиторию оставляем.
    return _render_broadcast(request, {"audience": audience, "text": ""}, {}, result)


# ---------------------------------------------------------------------------
# Общий журнал администраторов (один файл на всю игру)
# ---------------------------------------------------------------------------

@app.get("/admin-log", response_class=HTMLResponse)
def admin_log_view(request: Request):
    user = current_user(request)
    if user is None or not user.is_admin():
        return _redirect(request, "/app")
    return templates.TemplateResponse(
        request, "admin_log.html", _ctx(request, messages=admin_log.read_messages()),
    )


@app.post("/admin-log/clear")
def admin_log_clear(request: Request):
    user = current_user(request)
    if user and user.is_admin():
        admin_log.clear()
    return _redirect(request, "/admin-log")
