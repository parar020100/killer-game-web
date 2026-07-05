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
from urllib.parse import quote

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

import json
import os

import config
import db  # noqa: F401 — импорт инициализирует БД
from core import chat, auth, admin_log, settings as app_settings
from core.game import Game
from core.user import User

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Killer / Paparazzi — web")

# Подписанная cookie-сессия (секрет — в config, из переменной окружения).
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY)

# Контакт поддержки, файл правил и доп. вопросы теперь редактируются из UI и
# живут в БД (core/settings.py).


def bootstrap_root():
    """Гарантировать наличие root-пользователя и напечатать ссылку для входа.

    Заменяет прежний список DEFAULT_ADMINS: администраторов больше не задают в
    config.py. При первом запуске создаётся особый пользователь root (его нельзя
    удалить/разжаловать), а в лог печатается постоянная ссылка входа — по ней
    организатор заходит и выдаёт права обычному пользователю.
    """
    rid = app_settings.root_user_id()
    user = User.by_id(rid) if rid else None
    if user is None:
        user = User.get_or_create_by_identity("local", "root", username="root", name="root")
        app_settings.set_root_user_id(user.id)
    if not user.is_admin():
        user.set_admin(True)
    idents = user.identities()
    if not idents:
        return
    token = auth.set_permanent_token(idents[0].id)
    base = (os.getenv("APP_BASE_URL")
            or f"http://{os.getenv('HOST', '127.0.0.1')}:{os.getenv('PORT', '8000')}")
    print("=" * 72)
    print("[root] Вход root-администратора (создайте через него обычного админа):")
    print(f"[root] {base}/login?token={token}")
    print("=" * 72)


# Создание root и печать ссылки — при запуске (можно отключить как и автоинициализацию БД).
if not os.getenv("NO_INITDB"):
    bootstrap_root()

_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*$")


# --- доп. вопросы регистрации: хранение ответов ----------------------------
# Ответы игрока на (возможно несколько) доп. вопросов храним в user.extra_info как
# JSON-словарь {метка: ответ}. Старый одиночный ответ строкой читаем как legacy.

def get_extra_answers(user: User) -> dict:
    raw = (user.get_extra_info() or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    questions = app_settings.extra_questions()
    return {questions[0][0]: raw} if questions else {"Доп. вопрос": raw}


def set_extra_answers(user: User, answers: dict):
    user.set_extra_info(json.dumps(answers, ensure_ascii=False) if answers else "")


def extra_answer_pairs(user: User):
    """[(метка, ответ)] в порядке текущих вопросов (плюс «осиротевшие» ответы)."""
    answers = get_extra_answers(user)
    pairs = []
    used = set()
    for label, _q in app_settings.extra_questions():
        if label in answers:
            pairs.append((label, answers[label]))
            used.add(label)
    for label, ans in answers.items():
        if label not in used:
            pairs.append((label, ans))
    return pairs


def _extra_fields(answers: dict, errors: dict):
    """Описатели полей доп. вопросов для форм регистрации/профиля."""
    out = []
    for i, (label, question) in enumerate(app_settings.extra_questions()):
        out.append({"i": i, "label": label, "question": question,
                    "value": answers.get(label, ""), "error": errors.get(f"extra_{i}", "")})
    return out


def _read_extra(form):
    """(answers {метка: ответ}, errors {extra_<i>: текст}) из данных формы."""
    answers, errors = {}, {}
    for i, (label, _q) in enumerate(app_settings.extra_questions()):
        val = (form.get(f"extra_{i}") or "").strip()
        if not val:
            errors[f"extra_{i}"] = "Пожалуйста, ответьте на вопрос."
        else:
            answers[label] = val
    return answers, errors


def validate_real_name(raw: str):
    """Вернуть (имя, None) при успехе или (None, текст_ошибки)."""
    name = " ".join((raw or "").split())  # схлопнуть пробелы
    if not (config.NAME_MIN_LEN <= len(name) <= config.NAME_MAX_LEN):
        return None, f"Имя должно быть от {config.NAME_MIN_LEN} до {config.NAME_MAX_LEN} символов."
    if not _NAME_RE.match(name):
        return None, "Имя может содержать только буквы, пробел и дефис."
    return name, None


# ---------------------------------------------------------------------------
# Текущий пользователь (по сессии или отладочному ?user=<id|username>)
# ---------------------------------------------------------------------------

def _resolve_user_param(raw):
    """Пользователь по значению ?user= — это может быть внутренний id ИЛИ username."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return User.by_id(int(raw))
    return User.by_username(raw)


def _dev_user_param(request: Request):
    """Сырое значение ?user= (id или username), если dev-режим включён и оно валидно.

    Это ключ к «разным вкладкам»: identity живёт в URL, а не в общей на браузер
    cookie, поэтому каждая вкладка независима. Значение НЕ пишется в cookie.
    Возвращаем именно исходную строку, чтобы ссылки сохраняли удобный username.
    """
    if not config.ALLOW_DEV_LOGIN:
        return None
    raw = (request.query_params.get("user") or "").strip()
    return raw if raw and _resolve_user_param(raw) else None


def current_user(request: Request):
    # Отладочный ?user= имеет приоритет над cookie и не трогает сессию.
    if config.ALLOW_DEV_LOGIN:
        user = _resolve_user_param(request.query_params.get("user"))
        if user is not None:
            return user
    uid = request.session.get("uid")
    return User.by_id(uid) if uid else None


def _link_fn(user_param):
    """Вернуть функцию, дописывающую ?user=<id|username> к внутренним ссылкам/формам."""
    def link(path: str) -> str:
        if not user_param:
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}user={quote(str(user_param))}"
    return link


def _ctx(request: Request, **extra):
    """Контекст шаблона + прокидывание отладочного ?user= во все ссылки страницы."""
    user_param = _dev_user_param(request)
    return {
        "dev_user": user_param,
        "allow_dev": config.ALLOW_DEV_LOGIN,
        "link": _link_fn(user_param),
        **extra,
    }


def _redirect(request: Request, url: str, status_code: int = 303):
    """RedirectResponse, сохраняющий отладочный ?user= (чтобы вкладка не «слетала»)."""
    return RedirectResponse(url=_link_fn(_dev_user_param(request))(url),
                            status_code=status_code)


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

def _btn(label, action=None, kind="", full=False, href=None, todo=False,
         toggle=None, confirm="", disabled=False, note=""):
    """Кнопка дашборда.

    confirm — текст диалога подтверждения перед «опасным» действием (пусто = без);
    для kind="danger" подставляем общий текст автоматически.
    disabled — показать бледно-серой (действие недоступно по состоянию игры),
    note — подсказка (title) почему недоступно.
    """
    if kind == "danger" and action and not confirm:
        confirm = "Вы уверены, что хотите продолжить?"
    return {"label": label, "action": action, "kind": kind, "full": full,
            "href": href, "todo": todo, "toggle": toggle, "confirm": confirm,
            "disabled": disabled, "note": note}


# «Секрет бота» — пасхалка-рикролл (как в боте, h_user.py). Две площадки на выбор.
# Ссылки легко поменять здесь при необходимости.
SECRET_YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SECRET_RUTUBE = "https://rutube.ru/video/c6cc4d620b1d4338901770a44b3e82f4/"


# --- правила игры (HTML-файл, путь из настроек, core/settings.py) ------------

def _rules_path():
    fn = app_settings.rules_filename()
    return (BASE_DIR / fn) if fn else None


def _rules_choices():
    """Список HTML-файлов в папке rules/ (пути от корня проекта) для выпадающего списка."""
    d = BASE_DIR / "rules"
    if not d.is_dir():
        return []
    return sorted(p.relative_to(BASE_DIR).as_posix() for p in d.glob("*.html"))


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


def player_section(user: User):
    """Сообщение-пузырь игрока и его игровые кнопки.

    Без общих кнопок «Правила/Обновить» — их добавляет маршрут в конце последнего
    раздела (у админа это раздел управления, у обычного игрока — этот же).
    """
    game = Game()
    # Приветствие — «шапка» на всю ширину (над двумя колонками).
    top = (f'<span class="hi">Привет, {escape(user.get_name())}!</span>\n'
           "📷 Добро пожаловать в игру Папарацци!")
    # Левая колонка — статус игры; правая — «Ваша цель» (см. index.html).
    status = ['<span class="divider">══ 📋 Статус игры ══</span>', game.status_html()]
    target_html = ""

    if not user.is_player():
        status.append("❌ <em>Вы пока не участвуете в игре</em>")
        status.append(f"👥 Игроков: <strong>{game.count_players()}</strong>")
    elif not game.is_started():
        status.append("✅ <em>Вы зарегистрированы, ждём старта игры</em>")
        status.append(f"👥 Игроков: <strong>{game.count_players()}</strong>")
    else:
        # Игра идёт (в т.ч. на паузе) — показываем игровое состояние игрока.
        # Пауза — это НЕ конец игры, поэтому итоги игроку здесь не показываем
        # (их рассылает админ при завершении). Промежуточные итоги — только у админа.
        status_extra, target_html = _player_game_split(user)
        status += status_extra

    # Единый постоянный набор игровых кнопок (недоступные — серые, с подсказкой),
    # одинаковый для игрока и не-игрока.
    return top, "\n".join(status), target_html, _player_action_buttons(user, game)


def _player_action_buttons(user: User, game: Game):
    """Единый постоянный набор игровых кнопок игрока/не-игрока.

    Всегда одни и те же слоты; недоступные по состоянию — бледно-серые с подсказкой.
    Различается только смысл главного слота: не в игре — «Зарегистрироваться»,
    в игре — «Сообщить о поимке цели» (как в боте b_join / b_gotcha).
    """
    is_player = user.is_player()
    started = game.is_started()
    b = []

    # Главный слот — на всю ширину.
    if not is_player:
        if game.is_registration_open():
            b.append(_btn("🟢 Зарегистрироваться", href="/app/join",
                          kind="primary", full=True))
        else:
            b.append(_btn("🟢 Зарегистрироваться", disabled=True, full=True,
                          note="Регистрация сейчас закрыта — дождитесь, когда "
                               "организаторы её откроют."))
    elif not started:
        b.append(_btn("📸 Сообщить о поимке цели", disabled=True, full=True,
                      note="Сообщить о поимке можно будет после старта игры."))
    elif not user.is_alive():
        b.append(_btn("📸 Сообщить о поимке цели", disabled=True, full=True,
                      note="Вы выбыли из игры — ловить цель больше нельзя."))
    elif user.is_awaiting_confirmation():
        b.append(_btn("✖️ Отменить заявку о поимке", "cancel_capture", full=True))
    elif user.get_target_user():
        b.append(_btn("📸 Сообщить о поимке цели", "report_capture", "ok", full=True))
    else:
        b.append(_btn("📸 Сообщить о поимке цели", disabled=True, full=True,
                      note="Сейчас у вас нет активной цели."))

    # «Выйти из игры» — всегда на месте (как в боте b_leave); для не-игроков серая.
    if is_player:
        b.append(_btn("🚪 Выйти из игры", "leave",
                      confirm="Точно выйти из игры? Вернуться можно будет "
                              "только через новую регистрацию."))
    else:
        b.append(_btn("🚪 Выйти из игры", disabled=True,
                      note="Вы не участвуете в игре — выходить не из чего."))
    return b


def _player_game_split(user: User):
    """(строки-статуса-слева, html-цели-справа) для игрока в идущей игре."""
    game = Game()
    lines = [f"🔪 Вы поймали целей: <strong>{user.get_score()}</strong>",
             f"💚 Живых игроков: <strong>{game.count_alive()}</strong>"]
    if not user.is_alive():
        lines.append("")
        lines.append("☠️ <em>Вы выбыли из игры.</em> Спасибо за участие!")
        return lines, ""
    # Событие «вас поймали» больше НЕ показывается здесь — для него отдельная
    # секция (capture_prompt), чтобы не прятать цель и кнопку «сообщить о поимке».
    target = user.get_target_user()
    tname = escape(target.get_name()) if target else "—"
    # Цель скрыта под спойлером-кнопкой: клик раскрывает её прямо на кнопке
    # (сама кнопка «динамична» — меняет надпись на имя цели). data-keep хранит
    # раскрытое состояние при живом обновлении страницы (см. templates/_live.html).
    right = (
        '<span class="divider">══ 🎯 Ваша цель ══</span>\n'
        '<details class="spoiler" data-keep="target">'
        '<summary>'
        '<span class="reveal-hide">👁️ Показать цель</span>'
        f'<span class="reveal-show">🎯 <strong>{tname}</strong></span>'
        '</summary>'
        '</details>'
    )
    if _awaiting_confirmation(user):
        right += '\n⏳ <em>Вы заявили о поимке — ждём подтверждения цели.</em>'
    return lines, right


def _awaiting_confirmation(user: User) -> bool:
    """Игрок сообщил о поимке своей цели и ждёт её подтверждения."""
    target = user.get_target_user()
    return bool(target and target.is_being_caught()
                and target.get_murderer() and target.get_murderer().id == user.id)


def recent_notifications(user: User, limit: int = 8):
    """Последние игровые уведомления игроку (то, что бот присылал в чат).

    Возвращает список {ts, html} от новых к старым — компактная лента событий
    на дашборде (в боте роль этой ленты играл сам чат).
    """
    un = user.get_username()
    if not un:
        return []
    msgs = [m for m in chat.read(un) if (m.get("tag") or "bot") == "bot"]
    out = []
    for m in reversed(msgs[-limit:]):  # новые сверху
        out.append({"ts": m["ts"], "html": linkify(m["body"])})
    return out


def capture_prompt(user: User):
    """Секция «вас поймали»: сообщение + кнопки подтвердить/это не так.

    Возвращает dict секции или None, если игрока сейчас никто не ловит.
    """
    if not (user.is_player() and user.is_alive() and user.is_being_caught()):
        return None
    # ВАЖНО: имя «охотника» раскрывать нельзя — анонимность преследователя
    # ключевая механика игры (как в боте: «Другой игрок сообщил…»).
    return {
        "hint": "— вас поймали? —",
        "message": "📸 <strong>Другой игрок сообщил, что поймал(а) вас.</strong>",
        "kind": "alert",
        # Половинные кнопки в одну строку (сетка .keyboard — 2 колонки).
        "buttons": [
            _btn("✅ Подтвердить", "confirm_capture", "primary"),
            _btn("🚫 Это не так", "deny_capture", "danger"),
        ],
    }


def admin_management_buttons(user: User):
    """Кнопки раздела «управление игрой» — постоянный набор, по две в ряд.

    Как у игрока: набор кнопок всегда один и тот же, а недоступные по состоянию
    игры показываются бледно-серыми (disabled) с подсказкой. Условия — из бота
    (m_builders.py): регистрацию можно открыть только вне активной игры/на паузе;
    завершить и сбросить игру — только на паузе (сначала пауза, потом остановка);
    промежуточные итоги — когда игра идёт.
    """
    game = Game()
    started = game.is_started()
    paused = game.is_paused()
    reg_open = game.is_registration_open()
    b = []

    # Жизненный цикл: один слот, меняющий смысл (старт / пауза / продолжить).
    if not started:
        b.append(_btn("▶️ Запустить игру", "start_game", "primary"))
    elif paused:
        b.append(_btn("▶️ Продолжить", "resume", "primary"))
    else:
        b.append(_btn("⏸️ Пауза", "pause"))

    # Показать/скрыть встроенный список игроков (рядом с паузой; состояние —
    # в куке/localStorage, поэтому это кнопка-переключатель на клиенте).
    b.append(_btn("👥 Список игроков", toggle="userlist"))

    # Регистрация: закрыть, если открыта; иначе открыть — серая во время игры.
    if reg_open:
        b.append(_btn("🚫 Закрыть регистрацию", "close_reg"))
    else:
        can_open = (not started) or paused
        b.append(_btn("✅ Открыть регистрацию", "open_reg" if can_open else None,
                      disabled=not can_open,
                      note="Открыть регистрацию можно только когда игра не идёт "
                           "или поставлена на паузу."))

    # Завершение / сброс — всегда видны, серые вне паузы (опасные → подтверждение).
    end_note = "Завершить или сбросить игру можно только во время паузы — сначала поставьте паузу."
    b.append(_btn("🏁 Завершить (итоги)", "end_game" if paused else None, "danger",
                  disabled=not paused, note=end_note,
                  confirm="Завершить игру и разослать итоги всем?"))
    b.append(_btn("♻️ Сбросить игру", "reset_game" if paused else None, "danger",
                  disabled=not paused, note=end_note,
                  confirm="Сбросить игру? Все игроки будут сняты с игры."))

    # Инструменты. Промежуточные итоги — только пока игра идёт.
    b.append(_btn("📊 Промежуточные итоги", href="/app/results" if started else None,
                  disabled=not started,
                  note="Промежуточные итоги доступны только во время игры."))
    b.append(_btn("📢 Рассылка", href="/broadcast"))
    b.append(_btn("⚙️ Настройки игры", href="/app/settings"))
    b.append(_btn("📋 Журнал", href="/admin-log"))
    return b


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


def _tg_name(user: User):
    """Имя из привязанного канала (аналог TG-имени в списке бота)."""
    for ident in user.identities():
        nm = ident.get_name()
        if nm:
            return nm
    return None


def user_row(user: User, game: Game) -> dict:
    un = user.get_username()
    murderer = user.get_murderer()
    return {
        "id": user.id,
        "status": bot_status_emoji(user) + game_status_emoji(user, game),
        "order": user.get_game_order() if user.is_player() else None,
        "kills": user.get_score() if user.is_player() else None,
        "nick": f"@{un}" if un else "—",
        "username": un,
        "name": user.get_name(),
        "is_admin": user.is_admin(),
        # «кем пойман» (killed_by): имя + подтверждена ли поимка (жив = ждём).
        "killed_by": murderer.get_name() if murderer else None,
        "killed_by_id": murderer.id if murderer else None,
        "kill_pending": bool(murderer) and user.is_alive(),
        # полный набор полей для раскрытой карточки (как в .txt-списке бота)
        "real_name": user.get_real_name(),
        "extra": extra_answer_pairs(user),  # [(метка, ответ)] доп. вопросов
        "tg_name": _tg_name(user),
        "is_player": user.is_player(),
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
            was_alive = user.is_alive()
            user.leave()
            admin_log.log(f"➖ {who} вышел(ла) из игры")
            user.notify("🚪 Вы вышли из игры.")
            # Если игра шла, а игрок был жив — чинить круг: пересобрать цели,
            # подтянуть очередь возрождения и проверить конец игры.
            if was_alive and game.is_started():
                game.try_revive_one()
                game.reassign_targets()
                if game.check_finished():
                    game.announce_winner()
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
                                      {"support_contact": app_settings.support_contact()})


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
    # В dev-режиме закрепляем вход за КОНКРЕТНОЙ вкладкой через ?user=<username>,
    # чтобы разные ссылки, открытые в разных вкладках одного браузера, не мешали
    # друг другу (cookie одна на браузер). В проде (dev выключен) — обычная cookie.
    if config.ALLOW_DEV_LOGIN:
        u = User.by_id(uid)
        ident = u.get_username() if u else None
        return RedirectResponse(url=f"/app?user={quote(str(ident or uid))}",
                                status_code=303)
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
def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)

    # Один экран без вкладок: статус-пузырь (две колонки), затем секции.
    message, status_col, target_html, player_buttons = player_section(user)
    sections = []

    # Секция «вас поймали» — сразу под статусом, если игрока сейчас ловят.
    prompt = capture_prompt(user)
    if prompt:
        sections.append(prompt)

    # Действия игрока + общие кнопки профиля (правила сверху, у пользователя).
    if has_rules():
        player_buttons.append(_btn("📜 Правила", href="/rules"))
    player_buttons.append(_btn("👤 Настройки профиля", href="/app/profile"))
    player_buttons.append(_btn("🤫 Узнать секрет", href="/app/secret"))
    player_buttons.append(_btn("✍️ Написать организаторам", todo=True))
    sections.append({"hint": "— доступные действия —", "buttons": player_buttons})

    # Админу — второй раздел с управлением игрой (сворачиваемый переключателем).
    user_list = None
    is_admin = user.is_admin()
    if is_admin:
        sections.append({"hint": "— управление игрой —", "toggle_id": "adminmenu",
                         "buttons": admin_management_buttons(user)})
        user_list = _user_list_data()

    return templates.TemplateResponse(
        request, "index.html",
        _ctx(
            request,
            is_admin=is_admin,
            user_name=user.get_name(),
            message_html=message,
            status_col=status_col,
            target_html=target_html,
            notifications=recent_notifications(user),
            sections=sections,
            user_list=user_list,
            # состояние переключателей (кука → корректная подпись кнопки даже при
            # живом обновлении, когда меню перерисовывается)
            show_userlist=is_admin and request.cookies.get("userlist") == "1",
            # меню админа по умолчанию раскрыто; свёрнуто только если явно выбрано
            show_adminmenu=request.cookies.get("adminmenu") != "0",
            support_contact=app_settings.support_contact(),
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


def can_set_score(target: User, game: Game) -> bool:
    """Изменить счёт можно только на паузе (как в боте)."""
    return target.is_player() and game.is_paused()


def can_set_order(target: User, game: Game) -> bool:
    """Сменить позицию в круге — на паузе или пока игра не запущена (как в боте)."""
    return target.is_player() and (game.is_paused() or not game.is_started())


def _admin_log_enabled(user: User) -> bool:
    """Получает ли этот админ уведомления ADMIN LOG (хоть по одному каналу)."""
    return any(i.is_admin_log_enabled() for i in user.identities())


def _settings_ctx(request, user, saved="", **extra):
    return _ctx(
        request,
        current=Game().get_password(),
        support_contact=app_settings.support_contact(),
        rules_filename=app_settings.rules_filename(),
        rules_files=_rules_choices(),
        extra_questions=app_settings.extra_questions(),
        confirm_kills=app_settings.confirm_kills(),
        saved=saved,
        **extra,
    )


@app.get("/app/settings", response_class=HTMLResponse)
def settings_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")
    return templates.TemplateResponse(request, "settings.html",
                                      _settings_ctx(request, user))


@app.post("/app/settings", response_class=HTMLResponse)
async def settings_save(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")

    form = await request.form()
    action = form.get("action", "password")
    saved = ""

    if action == "password":
        value = (form.get("password") or "").strip()
        Game().set_password(value)
        admin_log.log(f"🔑 {user.get_name()} "
                      + ("задал(а) пароль регистрации" if value else
                         "убрал(а) пароль регистрации"))
        saved = "Пароль регистрации сохранён." if value else "Пароль регистрации убран."
    elif action == "support":
        app_settings.set_support_contact(form.get("support_contact") or "")
        admin_log.log(f"📞 {user.get_name()} изменил(а) контакт поддержки")
        saved = "Контакт поддержки сохранён."
    elif action == "rules":
        app_settings.set_rules_filename(form.get("rules_filename") or "")
        admin_log.log(f"📜 {user.get_name()} изменил(а) файл правил")
        saved = "Файл правил сохранён."
    elif action == "confirm_kills":
        new = not app_settings.confirm_kills()
        app_settings.set_confirm_kills(new)
        admin_log.log(f"📸 {user.get_name()} "
                      + ("включил(а) подтверждение поимок жертвой"
                         if new else "отключил(а) подтверждение поимок"))
        saved = ("Теперь поимку подтверждает жертва." if new
                 else "Теперь поимка засчитывается сразу, без подтверждения.")
    elif action == "extra_questions":
        # Собираем пары label_i / q_i (пустые строки = удалённые вопросы).
        old_labels = {p[0] for p in app_settings.extra_questions()}
        pairs, i = [], 0
        while (f"label_{i}" in form) or (f"q_{i}" in form):
            label = (form.get(f"label_{i}") or "").strip()
            question = (form.get(f"q_{i}") or "").strip()
            if label and question:
                pairs.append([label, question])
            i += 1
        app_settings.set_extra_questions(pairs)
        admin_log.log(f"❓ {user.get_name()} обновил(а) доп. вопросы регистрации "
                      f"({len(pairs)} шт.)")
        saved = "Доп. вопросы сохранены."
        # Если добавились новые вопросы — попросить уже зарегистрированных игроков,
        # у кого на них нет ответа, зайти в «Настройки профиля» и заполнить.
        added = {p[0] for p in pairs} - old_labels
        if added:
            notified = 0
            for ply in User.all_players():
                ans = get_extra_answers(ply)
                if any(lbl not in ans for lbl in added):
                    ply.notify("❓ Организаторы добавили новые вопросы для участников. "
                               "Пожалуйста, зайдите в «Настройки профиля» на сайте и "
                               "ответьте на них.")
                    notified += 1
            if notified:
                saved += f" Игроков без ответов уведомлено: {notified}."
    elif action == "restart":
        # Перезапуск приложения: под uvicorn --reload достаточно «тронуть» файл
        # исходника — наблюдатель перезагрузит воркер. Делаем с задержкой, чтобы
        # успеть отдать ответ-редирект до перезапуска.
        admin_log.log(f"🔁 {user.get_name()} перезапустил(а) приложение")
        import threading
        threading.Timer(0.6, lambda: Path(__file__).touch()).start()
        return _redirect(request, "/app")
    elif action == "full_reset":
        # Полный сброс БД — необратимо. Текущая сессия становится недействительной.
        admin_log.log(f"💣 {user.get_name()} выполнил(а) полный сброс базы данных")
        db.drop_db()
        db.init_db()
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(request, "settings.html",
                                      _settings_ctx(request, user, saved=saved))


def _render_profile(request, user, values, errors, saved, answers):
    """Отрисовать страницу профиля. answers — dict {метка: значение} для полей."""
    muted = bool(user.identities()) and all(i.is_muted() for i in user.identities())
    # Доп. вопросы показываем только участникам игры (как в боте, d_edit.py).
    fields = _extra_fields(answers, errors) if user.is_player() else []
    return templates.TemplateResponse(
        request, "profile.html",
        _ctx(request, values=values, errors=errors, saved=saved,
             extra_fields=fields, muted=muted,
             is_admin=user.is_admin(), admin_log_on=_admin_log_enabled(user),
             is_root=user.is_root()),
    )


@app.get("/app/profile", response_class=HTMLResponse)
def profile_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    values = {"real_name": user.get_real_name() or ""}
    return _render_profile(request, user, values, {}, False, get_extra_answers(user))


@app.post("/app/profile", response_class=HTMLResponse)
async def profile_save(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)

    form = await request.form()
    action = form.get("action", "save")
    is_player = user.is_player()

    # «Выйти на всех устройствах» — отозвать постоянные ссылки входа и очистить
    # текущую сессию. Новую ссылку можно получить в чате командой /start.
    if action == "revoke_login":
        user.revoke_login_links()
        admin_log.log(f"🔐 {user.get_name()} отозвал(а) ссылки входа (выход на всех устройствах)")
        request.session.clear()
        return RedirectResponse(url="/", status_code=303)

    # «Забыть меня» — самоудаление учётной записи (root удалить нельзя).
    if action == "forget_me":
        if user.is_root():
            values = {"real_name": user.get_real_name() or ""}
            return _render_profile(request, user, values, {},
                                   "Учётную запись root удалить нельзя.",
                                   get_extra_answers(user))
        user.forget_self()
        request.session.clear()
        return RedirectResponse(url="/", status_code=303)

    # Переключатель уведомлений («Отключить бота» из меню игрока в боте).
    if action in ("mute", "unmute"):
        for ident in user.identities():
            ident.set_muted(action == "mute")
        admin_log.log(f"🔕 {user.get_name()} "
                      + ("отключил(а)" if action == "mute" else "включил(а)")
                      + " уведомления")
        saved = "Уведомления отключены." if action == "mute" else "Уведомления включены."
        values = {"real_name": user.get_real_name() or ""}
        return _render_profile(request, user, values, {}, saved, get_extra_answers(user))

    # ADMIN LOG — личная настройка админа: слать ли ему уведомления обо всех
    # игровых событиях (перенесено из общих настроек игры).
    if action in ("log_on", "log_off") and user.is_admin():
        on = action == "log_on"
        for ident in user.identities():
            ident.set_admin_log_enabled(on)
        saved = ("Уведомления о событиях игры включены." if on
                 else "Уведомления о событиях игры отключены.")
        values = {"real_name": user.get_real_name() or ""}
        return _render_profile(request, user, values, {}, saved, get_extra_answers(user))

    real_name = form.get("real_name") or ""
    errors = {}
    name, err = validate_real_name(real_name)
    if err:
        errors["real_name"] = err
    if is_player:
        answers, extra_errors = _read_extra(form)
        errors.update(extra_errors)
    else:
        answers = get_extra_answers(user)

    if errors:
        values = {"real_name": real_name}
        return _render_profile(request, user, values, errors, False, answers)

    old = user.get_real_name()
    if name != old:
        admin_log.log(f"✏️ {old or '—'} изменил(а) имя на {name}")
    user.set_real_name(name)
    if is_player:
        set_extra_answers(user, answers)
    values = {"real_name": name}
    return _render_profile(request, user, values, {}, "Профиль обновлён.",
                           get_extra_answers(user))


@app.get("/app/secret", response_class=HTMLResponse)
def secret_page(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request, "secret.html",
        _ctx(request, youtube=SECRET_YOUTUBE, rutube=SECRET_RUTUBE),
    )


def user_menu_buttons(target: User):
    """Действия админа над пользователем — все кнопки видны всегда, но те, что
    требуют паузы, показываются бледно-серыми (disabled) с подсказкой.

    Порт условий из бота (menu/m_profile.py): устранить/оживить — на паузе;
    подарить/отобрать жизнь — пока игрок мёртв; удалить из игры — на паузе или
    пока игра не запущена; засчитать/отклонить поимку — когда есть заявка.
    Позиция в круге и счёт живут отдельными «окошками» в шаблоне карточки.
    """
    game = Game()
    paused = game.is_paused()
    started = game.is_started()
    tname = target.get_name()
    b = []

    def add(label, action, kind="", disabled=False, note="", confirm=""):
        # Опасные действия (danger) требуют подтверждения (общий текст по умолчанию).
        if kind == "danger" and not disabled and not confirm:
            confirm = "Вы уверены?"
        b.append({"label": label, "action": action, "kind": kind,
                  "disabled": disabled, "note": note, "confirm": confirm})

    # роль
    if not target.is_admin():
        add("👑 Выдать админа", "promote")
    elif not target.is_default_admin():
        add("🧹 Забрать админа", "demote")

    # модерация текущей заявки о поимке (пока идёт игра)
    if target.is_being_caught():
        add("✅ Засчитать поимку", "force_accept", "primary")
        add("❌ Отклонить поимку", "force_deny",
            confirm=f"Отклонить заявку о поимке игрока {tname}?")

    # игровые действия (только для участников)
    if target.is_player():
        if target.is_alive():
            add("🔪 Устранить", "kill", "danger", disabled=not paused,
                note="Устранять игрока можно только во время паузы.",
                confirm=f"Устранить игрока {tname} из игры?")
        else:
            add("♻️ Оживить", "revive", "primary", disabled=not paused,
                note="Оживлять игрока можно только во время паузы.")
            if target.is_queued_for_revival():
                add("🚫 Отобрать жизнь", "take_life")
            else:
                add("🎁 Подарить жизнь", "give_life")
        add("👋 Удалить из игры", "kick", "danger",
            disabled=not (paused or not started),
            note="Убирать игрока из игры можно во время паузы или до старта игры.",
            confirm=f"Удалить игрока {tname} из игры?")

    # удаление из системы — только для не-игроков (нельзя себя/дефолт-админа)
    if not target.is_player() and not target.is_default_admin():
        add("🗑️ Удалить из системы", "delete", "danger",
            confirm=f"Удалить пользователя {tname} из системы? Это необратимо.")

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
        "extra_info": "; ".join(f"{l}: {a}" for l, a in extra_answer_pairs(target)),
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
             can_set_score=can_set_score(target, game),
             can_set_order=can_set_order(target, game)),
    )


@app.post("/app/users/{uid}/act")
def user_action(request: Request, uid: int, action: str = Form(...),
                value: str = Form(""), next_url: str = Form("", alias="next")):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return _redirect(request, "/app")
    # Куда вернуться после действия: туда, откуда пришли (дашборд/список), а не на
    # отдельную страницу игрока. По умолчанию — на страницу пользователя.
    dest = next_url if next_url.startswith("/app") else f"/app/users/{uid}"
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
    elif action == "set_order" and target.is_player():
        try:
            before = _snapshot_targets()
            target.admin_set_order(admin, int(value))
            _notify_retargets(before)
        except (ValueError, TypeError):
            pass
    elif action == "reassign_kill" and target.is_player():
        # value — позиция в круге (#) нового «охотника», которому засчитать поимку.
        new_m = None
        try:
            new_m = User.by_game_order(int(value))
        except (ValueError, TypeError):
            pass
        if new_m is not None:
            target.admin_reassign_kill(admin, new_m)
    elif action == "delete":
        # нельзя удалить себя или дефолт-админа
        if target.id != admin.id and not target.is_default_admin():
            before = _snapshot_targets()
            target.delete_from_system(admin)
            _notify_retargets(before)
            # удалённого игрока уже нет — возвращаемся к списку, не на его страницу
            back = dest if dest != f"/app/users/{uid}" else "/app/users"
            return _redirect(request, back)
    elif action in _USER_ACTIONS:
        method, structural = _USER_ACTIONS[action]
        before = _snapshot_targets() if structural else None
        method(target, admin)
        if structural:
            _notify_retargets(before)
    return _redirect(request, dest)


def _user_list_data():
    """Данные для списка пользователей (раскрывающиеся карточки с действиями).

    Используется и на отдельной странице `/app/users`, и встроенным блоком на
    дашборде (`_userlist.html`). Кнопки действий зависят от состояния игры.
    """
    game = Game()
    all_users = User.all()
    players = [u for u in all_users if u.is_player()]
    players.sort(key=lambda u: (u.get_game_order() is None, u.get_game_order() or 0, u.id))
    non_players = [u for u in all_users if not u.is_player()]

    def row(u: User) -> dict:
        r = user_row(u, game)
        r["buttons"] = user_menu_buttons(u)
        r["can_set_score"] = can_set_score(u, game)
        r["can_set_order"] = can_set_order(u, game)
        r["can_reassign_kill"] = u.is_player() and not u.is_alive() and bool(u.get_murderer())
        r["score"] = u.get_score()
        return r

    # Неподтверждённые поимки (заявки, ждущие ответа жертвы) — отдельным красным
    # блоком над списком, сразу с кнопками «Засчитать» / «Отклонить».
    pending = []
    for u in players:
        if u.is_being_caught():
            murderer = u.get_murderer()
            pending.append({
                "victim_id": u.id,
                "victim_name": u.get_name(),
                "victim_order": u.get_game_order(),
                "hunter_name": murderer.get_name() if murderer else "?",
            })

    return {
        "total_users": len(all_users),
        "total_players": len(players),
        "game_started": game.is_started(),
        "game_paused": game.is_paused(),
        "players": [row(u) for u in players],
        "non_players": [row(u) for u in non_players],
        "pending": pending,
    }


@app.get("/app/users", response_class=HTMLResponse)
def users_list(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    if not user.is_admin():
        return _redirect(request, "/app")

    return templates.TemplateResponse(
        request, "users.html", _ctx(request, **_user_list_data()),
    )


def _render_register(request, user, game, values, errors, answers):
    return templates.TemplateResponse(
        request, "register.html",
        _ctx(
            request,
            needs_password=bool(game.get_password()),
            extra_fields=_extra_fields(answers, errors),
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
    values = {"real_name": user.get_real_name()}
    return _render_register(request, user, game, values, {}, get_extra_answers(user))


@app.post("/app/join", response_class=HTMLResponse)
async def join_submit(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    game = Game()
    if user.is_player() or not game.is_registration_open():
        return _redirect(request, "/app")

    form = await request.form()
    real_name = form.get("real_name") or ""
    password = form.get("password") or ""

    errors = {}
    name, err = validate_real_name(real_name)
    if err:
        errors["real_name"] = err
    if game.get_password() and password != game.get_password():
        errors["password"] = "Неверный пароль игры."
    answers, extra_errors = _read_extra(form)
    errors.update(extra_errors)

    if errors:
        values = {"real_name": real_name}
        return _render_register(request, user, game, values, errors, answers)

    user.set_real_name(name)
    set_extra_answers(user, answers)
    do_join(user)
    return _redirect(request, "/app")


@app.post("/act")
def act(request: Request, action: str = Form(...)):
    """Применить действие текущего пользователя и вернуться (Post/Redirect/Get)."""
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    apply_action(action, user)
    return _redirect(request, "/app")


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
