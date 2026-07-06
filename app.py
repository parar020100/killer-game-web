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
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

import json
import os

import config
import db  # noqa: F401 — импорт инициализирует БД
from core import chat, auth, admin_log, bot_log, mode, settings as app_settings, version
from core.game import Game
from core.user import User

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Подпись внизу страниц (см. templates/_footer.html) — доступна во всех шаблонах.
templates.env.globals["developed_by"] = version.DEVELOPED_BY
templates.env.globals["build_sha"] = version.BUILD_SHA

app = FastAPI(title="Killer / Paparazzi — web")

# Подписанная cookie-сессия. В проде (адрес https) помечаем cookie Secure, чтобы
# она не утекала по http; SameSite=Lax — базовая защита от CSRF на переходах.
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    same_site="lax",
    https_only=str(getattr(config, "APP_BASE_URL", "")).startswith("https"),
)

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
    # root всегда носит имя «Админище» (item 50): задаём при старте, если оно ещё
    # не такое — так организатора видно в списках под узнаваемым именем.
    if user.get_real_name() != "Админище":
        user.set_real_name("Админище")
    idents = user.identities()
    if not idents:
        return
    token = auth.set_permanent_token(idents[0].id)
    # Тот же базовый адрес, что и у ботов (config.APP_BASE_URL) — по умолчанию http.
    base = (getattr(config, "APP_BASE_URL", "") or "http://127.0.0.1:8000").rstrip("/")
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
# Текущий пользователь — по сессии; переключение вкладки — ?user=<uid> из набора
# ---------------------------------------------------------------------------
# Сессия хранит НАБОР uid, для которых этот браузер реально прошёл вход по ссылке
# (/login?token=). Активный аккаунт вкладки — ?user=<uid>, но ТОЛЬКО если uid входит
# в набор — иначе доступа нет. Так разные ссылки из бота (или админская кнопка
# «Открыть меню», которая генерирует токен) открывают разных игроков в разных
# вкладках, но войти можно лишь в аккаунты, чью ссылку-токен браузер предъявил.
# Отдельного «dev-входа как кто угодно» больше нет — вход всегда только по токену.

def _authed_uids(request: Request):
    """Список uid, подтверждённых входом по ссылке в этой сессии (в порядке добавления)."""
    uids = request.session.get("uids")
    if not uids:
        legacy = request.session.get("uid")   # совместимость со старой одиночной сессией
        uids = [legacy] if legacy else []
    seen = []
    for u in uids:
        try:
            u = int(u)
        except (TypeError, ValueError):
            continue
        if u not in seen:
            seen.append(u)
    return seen


def _active_uid(request: Request, authed=None):
    """Активный uid для этой вкладки: ?user=<uid> из набора, иначе первый в наборе."""
    if authed is None:
        authed = _authed_uids(request)
    if not authed:
        return None
    raw = (request.query_params.get("user") or "").strip()
    if raw.isdigit() and int(raw) in authed:
        return int(raw)
    return authed[0]


def current_user(request: Request):
    # Активный аккаунт — из набора сессии. ?user=<uid> переключает вкладку, но только
    # на аккаунт из набора; явный, но не входящий в набор uid — отказ (None), чтобы
    # нельзя было «подсмотреть» чужой аккаунт подбором id.
    authed = _authed_uids(request)
    raw = request.query_params.get("user")
    if raw is not None and raw.strip() != "":
        raw = raw.strip()
        if raw.isdigit() and int(raw) in authed:
            return User.by_id(int(raw))
        return None
    return User.by_id(authed[0]) if authed else None


def _tab_user_param(request: Request):
    """uid активного аккаунта для переноса по ссылкам вкладки — только если он из набора."""
    authed = _authed_uids(request)
    raw = (request.query_params.get("user") or "").strip()
    if raw.isdigit() and int(raw) in authed:
        return raw
    return None


def _accounts_ctx(request: Request):
    """Аккаунты набора сессии для селектора «выбрать активного» (слева сверху)."""
    authed = _authed_uids(request)
    if not authed:
        return []
    active = _active_uid(request, authed)
    out = []
    for uid in authed:
        u = User.by_id(uid)
        if u is None:
            continue
        out.append({
            "id": uid,
            "name": u.get_name(),
            "is_admin": u.is_admin(),
            "active": uid == active,
            "url": f"/app?user={uid}",
        })
    return out


def _link_fn(user_param):
    """Функция, дописывающая ?user=<uid> к ссылкам/формам (закрепляет вкладку за аккаунтом)."""
    def link(path: str) -> str:
        if not user_param:
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}user={quote(str(user_param))}"
    return link


def _ctx(request: Request, **extra):
    """Контекст шаблона + прокидывание ?user=<uid> во все ссылки + мульти-аккаунт."""
    return {
        "link": _link_fn(_tab_user_param(request)),
        "accounts": _accounts_ctx(request),
        **extra,
    }


def _redirect(request: Request, url: str, status_code: int = 303):
    """RedirectResponse, сохраняющий ?user=<uid> вкладки (вкладка не «слетает»)."""
    return RedirectResponse(
        url=_link_fn(_tab_user_param(request))(url),
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
         toggle=None, confirm="", disabled=False, note="", popover=""):
    """Кнопка дашборда.

    confirm — текст диалога подтверждения перед «опасным» действием (пусто = без);
    для kind="danger" подставляем общий текст автоматически.
    disabled — показать бледно-серой (действие недоступно по состоянию игры),
    note — подсказка (title) почему недоступно.
    popover — id встроенного «окошка» (напр. "testusers"), которое раскрывает эта
    кнопка вместо отправки формы (разметка — в шаблоне index.html).
    """
    if kind == "danger" and action and not confirm:
        confirm = "Вы уверены, что хотите продолжить?"
    return {"label": label, "action": action, "kind": kind, "full": full,
            "href": href, "todo": todo, "toggle": toggle, "confirm": confirm,
            "disabled": disabled, "note": note, "popover": popover}


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


def notify_target(user: User, changed: bool = False):
    """Уведомить игрока, что цель назначена/изменилась (БЕЗ раскрытия имени).

    Имя цели показывается только в интерфейсе игры (под спойлером на дашборде),
    поэтому в уведомление оно не попадает — лишь факт и приглашение открыть сайт.
    ``changed=True`` — цель сменилась (переназначение), иначе — первично назначена.
    """
    target = user.get_target_user()
    if target:
        user.notify(mode.t("target_changed" if changed else "target_assigned"))


def player_section(user: User):
    """Сообщение-пузырь игрока и его игровые кнопки.

    Без общих кнопок «Правила/Обновить» — их добавляет маршрут в конце последнего
    раздела (у админа это раздел управления, у обычного игрока — этот же).
    """
    game = Game()
    # Приветствие — «шапка» на всю ширину (над двумя колонками).
    top = (f'<span class="hi">Привет, {escape(user.get_name())}!</span>\n'
           + mode.t("welcome"))
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
        b.append(_btn(mode.t("report_btn"), disabled=True, full=True,
                      note="Сообщить о поимке можно будет после старта игры."))
    elif game.is_paused():
        # На паузе игровые действия недоступны (как в боте) — заявку не подать.
        b.append(_btn(mode.t("report_btn"), disabled=True, full=True,
                      note="Игра на паузе — сообщить о поимке сейчас нельзя."))
    elif not user.is_alive():
        b.append(_btn(mode.t("report_btn"), disabled=True, full=True,
                      note="Вы выбыли из игры — ловить цель больше нельзя."))
    elif user.is_awaiting_confirmation():
        b.append(_btn("✖️ Отменить заявку", "cancel_capture", full=True))
    elif user.get_target_user():
        # Если включён фото-пруф — ведём на страницу с загрузкой фото, иначе
        # обычная кнопка-действие (мгновенная заявка).
        if app_settings.photo_proof():
            b.append(_btn(mode.t("report_btn"), href="/app/capture", kind="ok", full=True))
        else:
            b.append(_btn(mode.t("report_btn"), "report_capture", "ok", full=True))
    else:
        b.append(_btn(mode.t("report_btn"), disabled=True, full=True,
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
    lines = [mode.t("score_line", score=user.get_score()),
             f"💚 Живых игроков: <strong>{game.count_alive()}</strong>"]
    if not user.is_alive():
        lines.append("")
        lines.append("☠️ <em>Вы выбыли из игры.</em> Спасибо за участие!")
        return lines, ""
    # На паузе цель не показывается (как в боте): игровые действия заморожены.
    if game.is_paused():
        right = ('<span class="divider">══ 🎯 Ваша цель ══</span>\n'
                 '⏸️ <em>Игра на паузе — цель временно скрыта. '
                 'Дождитесь возобновления.</em>')
        return lines, right
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


def game_log_entries(limit: int = 40):
    """Последние записи игрового журнала (admin_log) для панели «Лог игры».

    Возвращает список {ts, html} от новых к старым (read_messages уже новые сверху).
    """
    out = []
    for m in admin_log.read_messages()[:limit]:
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
    # На паузе подтверждать/опровергать нельзя (как в боте) — кнопки серые.
    if Game().is_paused():
        buttons = [
            _btn("✅ Подтвердить", disabled=True,
                 note="Игра на паузе — подтвердить можно после возобновления."),
            _btn("🚫 Это не так", disabled=True,
                 note="Игра на паузе — ответить можно после возобновления."),
        ]
    else:
        buttons = [
            _btn("✅ Подтвердить", "confirm_capture", "primary"),
            _btn("🚫 Это не так", "deny_capture", "danger"),
        ]
    return {
        "hint": mode.t("caught_hint"),
        "message": mode.t("caught_msg"),
        "kind": "alert",
        # Половинные кнопки в одну строку (сетка .keyboard — 2 колонки).
        "buttons": buttons,
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
        b.append(_btn("▶️ Запустить игру", "start_game"))
    elif paused:
        b.append(_btn("▶️ Продолжить", "resume", "primary"))
    else:
        b.append(_btn("⏸️ Пауза", "pause"))

    # Показать/скрыть встроенный список игроков (рядом с паузой; состояние —
    # в куке/localStorage, поэтому это кнопка-переключатель на клиенте).
    b.append(_btn("👥 Список игроков", toggle="userlist"))

    # Показать/скрыть панель «Лог игры» (правый 3-й столбец на широком экране,
    # иначе стопкой снизу) — тем же механизмом переключателя, что и список.
    b.append(_btn("📋 Лог игры", toggle="gamelog"))

    # Регистрация: закрыть, если открыта; иначе открыть — серая во время игры.
    if reg_open:
        b.append(_btn("🚫 Закрыть регистрацию", "close_reg"))
    else:
        can_open = (not started) or paused
        b.append(_btn("✅ Открыть регистрацию", "open_reg" if can_open else None,
                      disabled=not can_open,
                      note="Открыть регистрацию можно только когда игра не идёт "
                           "или поставлена на паузу."))

    # Промежуточные итоги — только пока игра идёт (перед «Завершить»).
    b.append(_btn("📊 Промежуточные итоги", href="/app/results" if started else None,
                  disabled=not started,
                  note="Промежуточные итоги доступны только во время игры."))

    # Завершение / сброс — всегда видны, серые вне паузы (опасные → подтверждение).
    end_note = "Завершить или сбросить игру можно только во время паузы — сначала поставьте паузу."
    b.append(_btn("🏁 Завершить игру", "end_game" if paused else None, "danger",
                  disabled=not paused, note=end_note,
                  confirm="Завершить игру и разослать итоги всем?"))
    b.append(_btn("♻️ Сбросить игру", "reset_game" if paused else None, "danger",
                  disabled=not paused, note=end_note,
                  confirm="Сбросить игру? Все игроки будут сняты с игры."))

    # Инструменты.
    b.append(_btn("📢 Рассылка", href="/broadcast"))
    b.append(_btn("⚙️ Настройки игры", href="/app/settings"))
    b.append(_btn("📋 Журнал", href="/admin-log"))
    b.append(_btn("🧪 Тестовые пользователи", popover="testusers"))
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


# Порядок платформ в списке привязанных профилей: сначала Telegram, потом VK.
_PLATFORM_ORDER = {"tg": 0, "vk": 1}


def _account_list(user: User):
    """Привязанные профили пользователя (tg/vk) для карточки — tg первым, vk потом."""
    from core.identity import PLATFORM_ICON, PLATFORM_LABEL
    out = []
    idents = sorted(user.identities(),
                    key=lambda i: _PLATFORM_ORDER.get(i.get_platform(), 9))
    for i in idents:
        pl = i.get_platform()
        out.append({
            "icon": PLATFORM_ICON.get(pl, "•"),
            "platform": PLATFORM_LABEL.get(pl, pl),
            "username": i.get_username(),
            "name": i.get_name(),
        })
    return out


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
        "accounts": _account_list(user),  # привязанные профили (tg/vk)
        "is_player": user.is_player(),
        # есть ли веб-чат ('local') — тогда доступен дебаг-чат (эмуляция) для админа
        "is_local": any(i.get_platform() == "local" for i in user.identities()),
    }


# ---------------------------------------------------------------------------
# Действия кнопок дашборда
# ---------------------------------------------------------------------------

def _broadcast(text, players_only=True):
    users = User.all_players() if players_only else User.all()
    for u in users:
        u.notify(text)


# Тестовые пользователи: создаются админом (канал 'local' = веб-чат). Их ники —
# строго вида ``test<цифры>`` (напр. test001), чтобы массовое удаление затрагивало
# ТОЛЬКО их (root/обычные пользователи с другими никами не пострадают).
_TEST_FIRST = ["Тест", "Гость", "Демо", "Проба", "Игрок", "Бот", "Робот", "Аноним"]
_TEST_LAST = ["Тестов", "Пробин", "Демидов", "Гостев", "Ботов", "Мокин", "Фейков"]
_TEST_USER_RE = re.compile(r"^test\d+$")


def _is_test_user(u: User) -> bool:
    un = (u.get_username() or "")
    return bool(_TEST_USER_RE.match(un)) and not u.is_root()


def _create_test_users(n: int = 5, join: bool = True) -> int:
    """Создать n тестовых пользователей (веб-чат). join=True — сразу в игру. → число."""
    import random
    n = max(1, min(int(n), 100))
    existing = {u.get_username() for u in User.all()}
    created, i = 0, 1
    while created < n and i < 10000:
        un = f"test{i:03d}"
        i += 1
        if un in existing:
            continue
        name = f"{random.choice(_TEST_FIRST)} {random.choice(_TEST_LAST)}"
        u = User.get_or_create_by_local(un, username=un, name=name)
        u.set_real_name(name)
        if join and not u.is_player():
            u.join(alive=not Game().is_started())
        created += 1
    return created


def _delete_test_from_game() -> int:
    """Снять всех тестовых пользователей (test<цифры>) с игры. → сколько снято."""
    n = 0
    for u in User.all():
        if _is_test_user(u) and u.is_player():
            u.leave()
            n += 1
    return n


def _delete_test_users(admin: User) -> int:
    """Удалить из системы всех тестовых пользователей (test<цифры>). → сколько.

    Строго по маске ``test<цифры>`` и c пропуском root — случайно снести обычные
    учётки или организатора нельзя.
    """
    n = 0
    for u in list(User.all()):
        if _is_test_user(u) and u.id != admin.id and not u.is_default_admin():
            u.delete_from_system(admin)
            n += 1
    return n


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
    user.notify(mode.t("joined"))


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


def apply_action(action: str, user: User, count: int = 5) -> str:
    """Применить действие кнопки и вернуть текст-фидбек для показа игроку (flash).

    Каждая кнопка обязана давать результат: успех — подтверждение, неуспех —
    внятную причину («почему не сработало»). Пустая строка = молча (незнакомое
    действие); всё остальное показывается в плашке .flash на дашборде.
    """
    game = Game()
    who = user.get_name()

    # --- админские действия управления игрой ---
    if action in ("open_reg", "close_reg", "start_game", "stop_game", "pause",
                  "resume", "reset_game", "end_game", "add_test_users",
                  "add_test_players", "del_test_players", "del_test_users"):
        if not user.is_admin():
            return "⛔ Это действие доступно только организаторам."

    if action == "open_reg":
        if game.is_started() and not game.is_paused():
            return "⚠️ Открыть регистрацию нельзя, пока идёт игра. Поставьте паузу."
        game.open_registration()
        admin_log.log(f"🟡 {who} открыл(а) регистрацию")
        _broadcast(mode.t("bcast_reg_open"), players_only=False)
        return "✅ Регистрация открыта."
    elif action == "close_reg":
        if not game.is_registration_open():
            return "ℹ️ Регистрация и так закрыта."
        game.close_registration()
        admin_log.log(f"🚫 {who} закрыл(а) регистрацию")
        _broadcast("🚫 Регистрация на игру закрыта.", players_only=False)
        return "🚫 Регистрация закрыта, игроки уведомлены."
    elif action == "start_game":
        ok, msg = game.start()
        if ok:
            admin_log.log(f"🟢 {who} запустил(а) игру ({game.count_players()} игроков)")
            _broadcast(mode.t("bcast_started"))
            for ply in User.alive_players():
                notify_target(ply)
            return f"🟢 {msg} Игроков: {game.count_players()}. Цели разосланы."
        admin_log.log(f"⚠️ {who} не смог(ла) запустить игру: {msg}")
        return f"⚠️ Не удалось запустить игру: {msg}"
    elif action == "stop_game":
        if not game.is_started():
            return "ℹ️ Игра сейчас не запущена — останавливать нечего."
        game.stop()
        admin_log.log(f"🔴 {who} остановил(а) игру")
        _broadcast("🔴 Игра остановлена администратором.")
        return "🔴 Игра остановлена."
    elif action == "pause":
        if not game.is_started():
            return "⚠️ Игра сейчас не идёт — ставить на паузу нечего."
        if game.is_paused():
            return "ℹ️ Игра уже на паузе."
        game.pause()
        admin_log.log(f"⏸️ {who} поставил(а) игру на паузу")
        _broadcast("⏸️ Игра поставлена на паузу администратором.")
        return "⏸️ Игра на паузе."
    elif action == "resume":
        if not game.is_started():
            return "⚠️ Игра сейчас не запущена — продолжать нечего."
        if not game.is_paused():
            return "ℹ️ Игра уже идёт."
        game.resume()
        admin_log.log(f"▶️ {who} возобновил(а) игру")
        _broadcast("▶️ Игра продолжается!")
        return "▶️ Игра продолжается."
    elif action == "reset_game":
        if not game.is_paused():
            return "⚠️ Сбросить игру можно только во время паузы — сначала поставьте паузу."
        game.reset()
        admin_log.log(f"♻️ {who} сбросил(а) игру")
        _broadcast("♻️ Игра сброшена администратором. Спасибо за участие!",
                   players_only=False)
        return "♻️ Игра сброшена, все игроки сняты с игры."
    elif action == "end_game":
        if not game.is_paused():
            return "⚠️ Завершить игру можно только во время паузы — сначала поставьте паузу."
        game.pause()
        admin_log.log(f"🏁 {who} завершил(а) игру, разосланы итоги")
        _broadcast("🏁 Игра завершена! Итоги:\n\n" + results_text(),
                   players_only=False)
        return "🏁 Игра завершена, итоги разосланы всем."
    elif action == "leave":
        if not user.is_player():
            return "ℹ️ Вы и так не участвуете в игре."
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
        return "🚪 Вы вышли из игры."
    elif action in ("add_test_users", "add_test_players"):
        n = _create_test_users(count, join=(action == "add_test_players"))
        kind_word = "игроков" if action == "add_test_players" else "пользователей"
        admin_log.log(f"🧪 {who} создал(а) {n} тестовых {kind_word} (веб-чат)")
        return f"🧪 Создано тестовых {kind_word}: {n}."
    elif action == "del_test_players":
        n = _delete_test_from_game()
        admin_log.log(f"🧪 {who} снял(а) с игры {n} тестовых игроков")
        return f"🧪 Снято с игры тестовых игроков: {n}."
    elif action == "del_test_users":
        n = _delete_test_users(user)
        admin_log.log(f"🧪 {who} удалил(а) {n} тестовых пользователей из системы")
        return f"🧪 Удалено тестовых пользователей: {n}."
    elif action in ("report_capture", "cancel_capture", "confirm_capture",
                    "deny_capture"):
        # Игровые действия игрока заблокированы на паузе (как в боте).
        if game.is_paused():
            return "⏸️ Игра на паузе — действие сейчас недоступно."
        if action == "report_capture":
            ok, msg = user.attempt_capture()
        elif action == "cancel_capture":
            ok, msg = user.cancel_capture()
        elif action == "confirm_capture":
            ok, msg = user.confirm_capture()
        else:
            ok, msg = user.deny_capture()
        return ("✅ " if ok else "⚠️ ") + msg
    # "noop" / незнакомое — просто перерисовать без плашки
    return ""


# ---------------------------------------------------------------------------
# Чат с ботом (эмуляция) + вход по ссылке
# ---------------------------------------------------------------------------

def tg_bot_url() -> str:
    """Ссылка, открывающая диалог с Telegram-ботом (с кнопкой Start → /start)."""
    u = (getattr(config, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
    return f"https://t.me/{u}?start=login" if u else ""


def vk_bot_url() -> str:
    """Ссылка, открывающая диалог с VK-сообществом (с кнопкой «Начать»)."""
    url = (getattr(config, "VK_BOT_URL", "") or "").strip()
    if not url:
        return ""
    screen = url.rstrip("/").split("/")[-1]
    return f"https://vk.me/{screen}" if screen else url


@app.get("/", response_class=HTMLResponse)
def chat_entry(request: Request):
    return templates.TemplateResponse(
        request, "chat_entry.html",
        _ctx(request, support_contact=app_settings.support_contact(),
             tg_url=tg_bot_url(), vk_url=vk_bot_url()),
    )


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
    # Эмуляция — самостоятельный канал 'local' со своим никнеймом, НЕ связанный с
    # Telegram/VK (у тех — числовые id и отдельные аккаунты).
    User.get_or_create_by_local(username, username=username, name=username)
    messages = [
        {"tag": m["tag"] or "bot", "ts": m["ts"], "html": linkify(m["body"])}
        for m in chat.read(username)
    ]
    # Разовый показ настоящей ссылки входа (в историю на диск токен не пишется —
    # там он замаскирован). Ссылка живёт в сессии одну загрузку после /start.
    fresh = request.session.pop("fresh_link", None)
    if fresh and fresh.get("u") == username and fresh.get("link"):
        messages.append({
            "tag": "bot", "ts": "",
            "html": Markup(
                "🔗 <strong>Ваша ссылка для входа</strong> "
                "(показывается один раз, в истории не сохраняется):<br>"
                + str(linkify(fresh["link"]))),
        })
    return templates.TemplateResponse(
        request, "chat.html",
        {"username": username, "messages": messages},
    )


@app.post("/chat/{username}/start")
def chat_start(request: Request, username: str):
    username = chat.normalize(username)
    user = User.get_or_create_by_local(username, username=username, name=username)
    # Постоянная ссылка привязана к каналу (веб-чат 'local'), а не к пользователю:
    # у него могут быть отдельные ссылки и для Telegram/VK — это разные аккаунты,
    # пока их явно не объединят кодом привязки («Настройки профиля»).
    ident = user.identity("local")
    reissued = auth.has_permanent_token(ident.id)
    chat.add_user_message(username, "/start")
    token = auth.set_permanent_token(ident.id)
    link = f"{request.base_url}login?token={token}"
    note = "Прежняя ссылка больше не работает.\n" if reissued else ""
    # В историю (файл) пишем текст без самой ссылки — токен туда попадать не должен
    # (add_bot_message замаскирует любой token=..., а мы и вовсе не даём ссылку).
    # Настоящую ссылку показываем один раз через сессию (см. chat_view).
    chat.add_bot_message(
        username,
        "Ваша постоянная ссылка для входа в игру «Папарацци» отправлена ниже.\n"
        f"{note}Она работает всегда и не имеет срока, но в истории переписки "
        "не сохраняется (в целях безопасности). Никому её не пересылайте — по ней "
        "входят в вашу учётку. Нажмёте /start ещё раз — будет выдана новая, "
        "а старая перестанет работать.",
    )
    request.session["fresh_link"] = {"u": username, "link": link}
    return RedirectResponse(url=f"/chat/{username}", status_code=303)


@app.get("/login")
def login(request: Request, token: str = ""):
    row = auth.resolve_permanent_token_row(token)
    if row is None:
        return HTMLResponse(
            "<h3>Ссылка недействительна или устарела.</h3>"
            "<p>Вернитесь в чат и нажмите <b>/start</b> ещё раз.</p>",
            status_code=400,
        )
    uid = row["user_id"]
    # Добавляем uid в НАБОР подтверждённых аккаунтов этой сессии (мульти-аккаунт).
    uids = _authed_uids(request)
    if uid not in uids:
        uids.append(uid)
    request.session["uids"] = uids
    request.session.pop("uid", None)   # уходим со старой одиночной схемы
    # Вкладка привязывается к этому аккаунту через ?user=<uid> (uid уже в наборе) —
    # так разные ссылки в разных вкладках одного браузера не мешают друг другу.
    return RedirectResponse(url=f"/app?user={uid}", status_code=303)


@app.get("/logout")
def logout(request: Request):
    # Мульти-аккаунт: выходим из АКТИВНОГО аккаунта (убираем его из набора). Если он
    # был последним — очищаем сессию полностью. ?all=1 — выйти из всех сразу.
    if request.query_params.get("all") == "1":
        request.session.clear()
        return RedirectResponse(url="/", status_code=303)
    authed = _authed_uids(request)
    active = _active_uid(request, authed)
    if active in authed:
        authed.remove(active)
    if authed:
        request.session["uids"] = authed
        request.session.pop("uid", None)
        return RedirectResponse(url=f"/app?user={authed[0]}", status_code=303)
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
    # «Написать организаторам» временно убрана (вернуть — см. TODO).
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
            # лог игры — правая панель (3-й столбец), включается кнопкой (как список)
            show_gamelog=is_admin and request.cookies.get("gamelog") == "1",
            game_log=game_log_entries() if is_admin else None,
            support_contact=app_settings.support_contact(),
            game_name=mode.term("name"),
            game_tagline=mode.term("tagline"),
            game_icon=mode.term("icon"),
            # одноразовое уведомление о результате действия (напр. переназначение поимки)
            flash=request.session.pop("flash", ""),
        ),
    )


@app.get("/app/users/{uid}/open")
def open_as_user(request: Request, uid: int):
    """Админ: войти под дебаг-пользователем (веб-чат) в этой вкладке.

    Генерирует пользователю токен (как дебаг-чат) и проходит обычный вход по нему:
    аккаунт добавляется в набор сессии, вкладка привязывается к нему (?user=<uid>).
    Открывать в новой вкладке (target=_blank). Только для админа и только для
    пользователей с веб-каналом ('local') — реальные tg/vk-ссылки не трогаем.
    """
    actor = current_user(request)
    if actor is None or not actor.is_admin():
        return RedirectResponse(url="/app", status_code=303)
    target = User.by_id(uid)
    ident = target.identity("local") if target else None
    if ident is None:
        return RedirectResponse(url="/app", status_code=303)
    token = auth.set_permanent_token(ident.id)
    return RedirectResponse(url=f"/login?token={token}", status_code=303)


@app.get("/app/users/{uid}/photo")
def capture_photo(request: Request, uid: int):
    """Показать фото-пруф поимки, снятое этим игроком (охотником). Только админу.

    Отдаёт самый свежий файл из data/photos/ для указанного пользователя. Кнопка
    ведёт сюда из строки-заявки и из карточки устранённого игрока (по его «убийце»).
    """
    user = current_user(request)
    if user is None or not user.is_admin():
        return RedirectResponse(url="/app", status_code=303)
    target = User.by_id(uid)
    path = _latest_capture_photo(target) if target else None
    if path is None or not path.exists():
        return HTMLResponse("<h3>Фото поимки не найдено.</h3>", status_code=404)
    from starlette.responses import FileResponse
    return FileResponse(str(path))


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
        photo_proof=app_settings.photo_proof(),
        game_mode=mode.current(),
        db_files=db.list_db_files(),
        active_db=db.active_db_name(),
        start_mode=getattr(config, "START_MODE", "manual"),
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
    elif action == "game_mode":
        new_mode = form.get("game_mode") or ""
        mode.set_current(new_mode)
        admin_log.log(f"🎭 {user.get_name()} выбрал(а) оформление игры: "
                      f"{mode.term('name')}")
        saved = f"Оформление игры: «{mode.term('name')}»."
    elif action == "confirm_kills":
        new = not app_settings.confirm_kills()
        app_settings.set_confirm_kills(new)
        admin_log.log(f"📸 {user.get_name()} "
                      + ("включил(а) подтверждение поимок жертвой"
                         if new else "отключил(а) подтверждение поимок"))
        saved = ("Теперь поимку подтверждает жертва." if new
                 else "Теперь поимка засчитывается сразу, без подтверждения.")
    elif action == "photo_proof":
        new = not app_settings.photo_proof()
        app_settings.set_photo_proof(new)
        admin_log.log(f"🖼️ {user.get_name()} "
                      + ("включил(а) фото-пруф поимок" if new else "отключил(а) фото-пруф поимок"))
        saved = ("Теперь при поимке нужно приложить фото."
                 if new else "Фото-пруф поимок отключён.")
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
    elif action == "active_db":
        # Выбор активной «игры» = файл БД. Имя нового файла (если задано) имеет
        # приоритет над выбором из списка. Применяется после перезапуска.
        name = (form.get("new_db") or form.get("db_select") or "").strip()
        if name:
            applied = db.set_active_db(name)
            admin_log.log(f"🗄️ {user.get_name()} выбрал(а) активную игру (БД): {applied}")
            # перезапуск, чтобы приложение переоткрыло выбранный файл БД
            from core import control
            control.request("restart")
            saved = f"Активная игра: {applied}. Приложение перезапускается…"
        else:
            saved = "Имя файла БД не задано."
    elif action in ("restart", "stop", "update"):
        # Управление процессом приложения: команду исполнит супервизор run_all.py
        # (с учётом START_MODE — вручную или через systemctl). См. core/control.py.
        from core import control
        labels = {"restart": "перезагрузку", "stop": "выключение",
                  "update": "обновление (git pull) и перезапуск"}
        if control.request(action):
            admin_log.log(f"🔁 {user.get_name()} инициировал(а) {labels[action]} приложения")
            saved = (f"Команда на {labels[action]} отправлена. "
                     "Применится в течение пары секунд.")
        else:
            saved = "Неизвестная команда управления."
    elif action == "full_reset":
        # Полный сброс БД — необратимо. Текущая сессия становится недействительной.
        admin_log.log(f"💣 {user.get_name()} выполнил(а) полный сброс базы данных")
        db.drop_db()
        db.init_db()
        return RedirectResponse(url="/", status_code=303)

    return templates.TemplateResponse(request, "settings.html",
                                      _settings_ctx(request, user, saved=saved))


def _profile_accounts(user: User):
    """Привязанные каналы пользователя для страницы профиля (id, платформа, mute)."""
    from core.identity import PLATFORM_ICON, PLATFORM_LABEL
    out = []
    for i in user.identities():
        pl = i.get_platform()
        handle = i.get_username() or i.get_name() or i.get_platform_uid()
        out.append({"id": i.id, "key": pl, "icon": PLATFORM_ICON.get(pl, "•"),
                    "platform": PLATFORM_LABEL.get(pl, pl), "handle": handle,
                    "muted": i.is_muted()})
    return out


def _render_profile(request, user, values, errors, saved, answers,
                    link_code="", link_platform=""):
    """Отрисовать страницу профиля. answers — dict {метка: значение} для полей."""
    # Доп. вопросы показываем только участникам игры (как в боте, d_edit.py).
    fields = _extra_fields(answers, errors) if user.is_player() else []
    accounts = _profile_accounts(user)
    present = {a["key"] for a in accounts}
    return templates.TemplateResponse(
        request, "profile.html",
        _ctx(request, values=values, errors=errors, saved=saved,
             extra_fields=fields,
             is_admin=user.is_admin(), admin_log_on=_admin_log_enabled(user),
             is_root=user.is_root(),
             accounts=accounts, can_unlink=len(accounts) >= 2,
             has_tg="tg" in present, has_vk="vk" in present,
             tg_url=tg_bot_url(), vk_url=vk_bot_url(),
             link_code=link_code, link_platform=link_platform),
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

    # Привязка ещё одного канала: выдать одноразовый код для команды /link боту.
    # platform (tg/vk) — чтобы показать ссылку именно на нужного бота.
    if action == "link_code":
        from core import linking
        code = linking.create_code(user.id)
        platform = (form.get("platform") or "").strip()
        values = {"real_name": user.get_real_name() or ""}
        return _render_profile(request, user, values, {},
                               "Код привязки создан — отправьте его боту.",
                               get_extra_answers(user), link_code=code,
                               link_platform=platform)

    # Отвязать канал в отдельный аккаунт (разъединение). Только если каналов ≥2
    # (хотя бы один канал всегда должен остаться привязан).
    if action == "unlink":
        from core import linking
        from core.identity import Identity
        try:
            iid = int(form.get("identity_id") or 0)
        except (ValueError, TypeError):
            iid = 0
        ident = Identity.by_id(iid)
        saved = "Не удалось отвязать канал."
        if ident is None or ident.get_user_id() != user.id:
            saved = "Такого канала у вас нет."
        elif len(user.identities()) < 2:
            saved = "Нельзя отвязать единственный канал — хотя бы один должен остаться."
        else:
            linking.unlink_identity(ident)
            admin_log.log(f"🔗 {user.get_name()} отвязал(а) канал в отдельный аккаунт")
            saved = "Канал отвязан в отдельный аккаунт."
        values = {"real_name": user.get_real_name() or ""}
        return _render_profile(request, user, values, {}, saved, get_extra_answers(user))

    # Уведомления по КОНКРЕТНОМУ каналу (вкл/выкл mute у одной identity).
    if action in ("mute_ident", "unmute_ident"):
        from core.identity import Identity
        try:
            iid = int(form.get("identity_id") or 0)
        except (ValueError, TypeError):
            iid = 0
        ident = Identity.by_id(iid)
        saved = "Канал не найден."
        if ident and ident.get_user_id() == user.id:
            ident.set_muted(action == "mute_ident")
            saved = ("Уведомления для канала отключены." if action == "mute_ident"
                     else "Уведомления для канала включены.")
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


_PHOTO_EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "image/gif": ".gif", "image/heic": ".heic"}


def _photo_prefix(user: User) -> str:
    """Безопасный префикс имени файла фото-пруфа для пользователя (охотника)."""
    un = user.get_username() or f"id{user.id}"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", un)


def _photos_dir():
    return db.DATA_DIR / "photos"


def _latest_capture_photo(user: User):
    """Путь к самому свежему фото-пруфу этого игрока (охотника) или None."""
    if user is None:
        return None
    d = _photos_dir()
    if not d.is_dir():
        return None
    files = sorted(d.glob(f"{_photo_prefix(user)}_*"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _save_capture_photo(user: User, upload) -> bool:
    """Сохранить фото-пруф поимки в data/photos/ (как файлы бота). True при успехе."""
    if upload is None or not getattr(upload, "filename", ""):
        return False
    ct = getattr(upload, "content_type", "") or ""
    ext = _PHOTO_EXTS.get(ct) or (Path(upload.filename).suffix.lower() or ".jpg")
    photos_dir = _photos_dir()
    photos_dir.mkdir(parents=True, exist_ok=True)
    import time
    dest = photos_dir / f"{_photo_prefix(user)}_{int(time.time())}{ext}"
    data = upload.file.read()
    if not data:
        return False
    dest.write_bytes(data)
    return True


@app.get("/app/capture", response_class=HTMLResponse)
def capture_form(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    # Форма фото-пруфа доступна только когда пруф включён и есть активная цель.
    game = Game()
    target = user.get_target_user()
    if not (app_settings.photo_proof() and user.is_player() and user.is_alive()
            and game.is_started() and not game.is_paused()
            and target and not user.is_awaiting_confirmation()):
        return _redirect(request, "/app")
    return templates.TemplateResponse(
        request, "capture.html",
        _ctx(request, target_name=target.get_name(), report_label=mode.t("report_btn"),
             error=""),
    )


@app.post("/app/capture", response_class=HTMLResponse)
async def capture_submit(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    game = Game()
    target = user.get_target_user()
    if not (app_settings.photo_proof() and user.is_player() and user.is_alive()
            and game.is_started() and not game.is_paused()
            and target and not user.is_awaiting_confirmation()):
        return _redirect(request, "/app")

    form = await request.form()
    photo = form.get("photo")
    if not _save_capture_photo(user, photo):
        return templates.TemplateResponse(
            request, "capture.html",
            _ctx(request, target_name=target.get_name(), report_label=mode.t("report_btn"),
                 error="Пожалуйста, прикрепите фотографию поимки."),
        )
    admin_log.log(f"🖼️ {user.get_name()} приложил(а) фото-пруф поимки цели")
    ok, msg = user.attempt_capture()
    request.session["flash"] = ("✅ " if ok else "⚠️ ") + msg
    return _redirect(request, "/app")


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
    is_player = target.is_player()
    alive = target.is_alive()
    root = target.is_default_admin()
    tname = target.get_name()
    b = []

    def add(label, action, kind="", disabled=False, note="", confirm=""):
        # Опасные действия (danger) требуют подтверждения (общий текст по умолчанию).
        if kind == "danger" and not disabled and not confirm:
            confirm = "Вы уверены?"
        b.append({"label": label, "action": action, "kind": kind,
                  "disabled": disabled, "note": note, "confirm": confirm})

    not_player_note = "Пользователь не участвует в игре."

    # 1) Роль администратора — один слот-переключатель (всегда на месте).
    if root:
        add("🧹 Забрать админа", None, disabled=True,
            note="root-пользователя нельзя разжаловать.")
    elif target.is_admin():
        add("🧹 Забрать админа", "demote")
    else:
        add("👑 Выдать админа", "promote")

    # Модерация заявки о поимке (засчитать/отклонить) вынесена в отдельную красную
    # строку-заявку прямо над карточкой «жертвы» в списке (см. _userlist.html),
    # поэтому в наборе кнопок карточки её больше нет.

    # 3) Устранить (живого игрока, на паузе).
    if not is_player:
        kill_note = not_player_note
    elif not alive:
        kill_note = "Игрок уже выбыл из игры."
    else:
        kill_note = "Устранять игрока можно только во время паузы."
    add("🔪 Устранить", "kill", "danger", disabled=not (is_player and alive and paused),
        note=kill_note, confirm=f"Устранить игрока {tname} из игры?")

    # 4) Оживить (выбывшего игрока, на паузе).
    if not is_player:
        revive_note = not_player_note
    elif alive:
        revive_note = "Игрок сейчас в игре — оживлять некого."
    else:
        revive_note = "Оживлять игрока можно только во время паузы."
    add("♻️ Оживить", "revive", "primary",
        disabled=not (is_player and not alive and paused), note=revive_note)

    # 5) Жизнь (очередь возрождения) — только выбывшему игроку; слот-переключатель.
    if is_player and not alive and target.is_queued_for_revival():
        add("🚫 Отобрать жизнь", "take_life")
    elif is_player and not alive:
        add("🎁 Подарить жизнь", "give_life")
    else:
        add("🎁 Подарить жизнь", None, disabled=True,
            note=not_player_note if not is_player else "Подарить жизнь можно только выбывшему игроку.")

    # 6) Удалить из игры (игрока, на паузе или до старта).
    add("👋 Удалить из игры", "kick", "danger",
        disabled=not (is_player and (paused or not started)),
        note=not_player_note if not is_player
             else "Убирать игрока из игры можно во время паузы или до старта игры.",
        confirm=f"Удалить игрока {tname} из игры?")

    # 7) Удалить из системы (не-игрока, не root).
    if root:
        add("🗑️ Удалить из системы", None, "danger", disabled=True,
            note="root-пользователя удалить нельзя.")
    elif is_player:
        add("🗑️ Удалить из системы", None, "danger", disabled=True,
            note="Сначала уберите игрока из игры («Удалить из игры»), "
                 "потом его можно удалить из системы.")
    else:
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
    """Уведомить о смене цели тех живых игроков, у кого она изменилась (без имени)."""
    for u in User.alive_players():
        new = u.get_target_id()
        if new and before.get(u.id) != new:
            notify_target(u, changed=True)


# Отдельные страницы «Пользователи» (/app/users) и «Профиль пользователя»
# (/app/users/{uid}) убраны — они дублировали встроенный список на дашборде.
# Остался только POST-обработчик действий над пользователем (формы карточек).


@app.post("/app/users/{uid}/act")
def user_action(request: Request, uid: int, action: str = Form(...),
                value: str = Form(""), next_url: str = Form("", alias="next")):
    admin = current_user(request)
    if admin is None:
        return RedirectResponse(url="/", status_code=303)
    if not admin.is_admin():
        return _redirect(request, "/app")
    # Куда вернуться после действия: туда, откуда пришли (дашборд). Отдельных
    # страниц пользователя больше нет, поэтому по умолчанию — на дашборд.
    dest = next_url if next_url.startswith("/app") else "/app"
    target = User.by_id(uid)
    if target is None:
        return _redirect(request, "/app")

    # Каждое админское действие оставляет плашку-фидбек (что произошло / почему нет).
    flash = ""
    if action == "promote":
        if target.is_admin():
            flash = f"ℹ️ {target.get_name()} уже администратор."
        else:
            target.set_admin(True)
            admin_log.log(f"👑 {admin.get_name()} выдал(а) права админа: {target.get_name()}")
            target.notify("👑 Вам выданы права администратора игры.")
            flash = f"👑 {target.get_name()} назначен(а) администратором."
    elif action == "demote":
        if target.is_default_admin():
            flash = "⛔ root-пользователя нельзя разжаловать."
        elif not target.is_admin():
            flash = f"ℹ️ {target.get_name()} и так не администратор."
        else:
            target.set_admin(False)
            admin_log.log(f"🧹 {admin.get_name()} снял(а) права админа: {target.get_name()}")
            target.notify("Права администратора сняты.")
            flash = f"🧹 С {target.get_name()} сняты права администратора."
    elif action == "set_score":
        if not target.is_player():
            flash = "⚠️ Пользователь не участвует в игре — счёт менять нечему."
        else:
            try:
                flash = "✅ " + target.admin_set_score(admin, max(0, int(value)))
            except (ValueError, TypeError):
                flash = "⚠️ Введите число — счёт не изменён."
    elif action == "set_order":
        if not target.is_player():
            flash = "⚠️ Пользователь не участвует в игре — позиция не меняется."
        else:
            try:
                before = _snapshot_targets()
                flash = "✅ " + target.admin_set_order(admin, int(value))
                _notify_retargets(before)
            except (ValueError, TypeError):
                flash = "⚠️ Введите число — позиция не изменена."
    elif action == "message":
        # Одностороннее сообщение админа игроку (как admin_msg в боте).
        text = (value or "").strip()
        if not text:
            flash = "⚠️ Пустое сообщение не отправлено."
        else:
            target.notify(f"✉️ Сообщение от организаторов:\n{text}")
            admin_log.log(f"✉️ {admin.get_name()} написал(а) игроку "
                          f"{target.get_name()}: {text}")
            flash = f"✉️ Сообщение отправлено игроку {target.get_name()}."
    elif action == "reassign_kill":
        if not target.is_player():
            flash = "⚠️ Пользователь не участвует в игре — поимку переназначить нельзя."
        else:
            # value — позиция в круге (#) нового «охотника», которому засчитать поимку.
            pos = None
            try:
                pos = int(value)
            except (ValueError, TypeError):
                pass
            new_m = User.by_game_order(pos) if pos else None
            if new_m is None:
                flash = f"⚠️ Игрок с позицией №{value} не найден — поимка не переназначена."
            else:
                flash = target.admin_reassign_kill(admin, new_m)
    elif action == "delete":
        # нельзя удалить себя или дефолт-админа
        if target.id == admin.id:
            flash = "⛔ Нельзя удалить самого себя."
        elif target.is_default_admin():
            flash = "⛔ root-пользователя удалить нельзя."
        else:
            before = _snapshot_targets()
            name = target.get_name()
            target.delete_from_system(admin)
            _notify_retargets(before)
            request.session["flash"] = f"🗑️ Пользователь {name} удалён из системы."
            return _redirect(request, "/app")
    elif action in _USER_ACTIONS:
        method, structural = _USER_ACTIONS[action]
        before = _snapshot_targets() if structural else None
        flash = method(target, admin)
        if structural:
            _notify_retargets(before)
    else:
        flash = "⚠️ Неизвестное действие."
    if flash:
        request.session["flash"] = flash
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
        # Фото-пруф поимки (item 37): показываем кнопку только когда фото-пруф
        # включён и у «убийцы» этого игрока есть сохранённое фото. Ведёт на снимок
        # охотника (u.get_murderer()) — и в строке-заявке, и в карточке выбывшего.
        r["capture_photo_uid"] = None
        if app_settings.photo_proof():
            murderer = u.get_murderer()
            if murderer and _latest_capture_photo(murderer):
                r["capture_photo_uid"] = murderer.id
        return r

    # Неподтверждённые поимки показываются inline — красной строкой-заявкой прямо
    # над карточкой «жертвы» в списке (по данным карточки: kill_pending + killed_by),
    # см. _userlist.html. Отдельный список pending больше не нужен.
    return {
        "total_users": len(all_users),
        "total_players": len(players),
        "game_started": game.is_started(),
        "game_paused": game.is_paused(),
        "players": [row(u) for u in players],
        "non_players": [row(u) for u in non_players],
    }


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
def act(request: Request, action: str = Form(...), count: str = Form("5")):
    """Применить действие текущего пользователя и вернуться (Post/Redirect/Get).

    ``count`` используют действия с тестовыми пользователями (сколько создать).
    """
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/", status_code=303)
    try:
        n = int(count)
    except (TypeError, ValueError):
        n = 5
    flash = apply_action(action, user, count=n)
    if flash:
        request.session["flash"] = flash
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
# Журналы: игровой (admin_log, события игры + действия) и лог бота (bot_log)
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


@app.get("/admin-log/raw", response_class=PlainTextResponse)
def admin_log_raw(request: Request):
    """Сырой текст игрового журнала — открывается в новой вкладке (только админ)."""
    user = current_user(request)
    if user is None or not user.is_admin():
        return PlainTextResponse("403", status_code=403)
    return PlainTextResponse(admin_log.read_raw() or "Журнал игры пуст.")


@app.get("/bot-log/raw", response_class=PlainTextResponse)
def bot_log_raw(request: Request):
    """Сырой текст лога бота — открывается в новой вкладке (только админ)."""
    user = current_user(request)
    if user is None or not user.is_admin():
        return PlainTextResponse("403", status_code=403)
    return PlainTextResponse(bot_log.read_raw() or "Лог бота пуст.")
