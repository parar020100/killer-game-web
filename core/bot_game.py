"""Игровые действия участника прямо из бота (Telegram/VK) — базовое участие без сайта.

Сюда вынесено всё, что бот показывает и делает по игре: статус, цель, заявка о
поимке (с фото-пруфом, если он включён), подтверждение/отклонение поимки с причиной
и выход из игры.

Механика НЕ дублируется: сами действия выполняет `core/gameflow.py` — тот же модуль,
что вызывает сайт. Здесь только диалоговая «обёртка» (что спросить у игрока и как
показать ответ) и текстовый рендер статуса. Благодаря этому условия доступности,
уведомления и записи в ADMIN LOG у бота и у сайта совпадают.

Приватность: бот не показывает ничего, чего не показывает сайт. В частности, имя
«охотника» жертве не раскрывается никогда («Другой игрок сообщил…»), а цель на паузе
скрыта — ровно как на дашборде.

Состояние диалогов (ожидание фото / причины отказа / подтверждения выхода) хранится
в ПАМЯТИ процесса бота, по `user.id` — как и в `core/bot_register.py`.
"""
import html as _html
import re

from core import admin_log, gameflow, mode, photos, settings
from core.game import Game

# user_id -> {"step": "photo" | "deny_reason" | "leave_confirm"}
_sessions = {}

_CANCEL_WORDS = {"/cancel", "cancel", "отмена", "отменить", "стоп", "нет"}
_YES_WORDS = {"да", "yes", "y", "ага", "подтверждаю", "выйти"}


def in_progress(user_id) -> bool:
    return user_id in _sessions


def _plain(text: str) -> str:
    """Убрать HTML-разметку из текстов, общих с сайтом (<strong>, <em> и т.п.)."""
    return _html.unescape(re.sub(r"<[^>]+>", "", text or ""))


# --- статус ------------------------------------------------------------------

def status_text(user) -> str:
    """Статус игры и игрока — текстовый аналог левой колонки дашборда.

    Строки и условия — те же, что в `app.py::player_section` / `_player_game_split`:
    состояние игры, участие, личный счёт, счётчик игроков. Ничего сверх того, что
    видно на сайте.
    """
    game = Game()
    lines = ["📋 Статус игры", _plain(game.status_html())]

    if not user.is_player():
        lines.append("❌ Вы пока не участвуете в игре")
    elif not game.is_started():
        lines.append("✅ Вы зарегистрированы, ждём старта игры")
    elif not user.is_alive():
        lines.append(_plain(mode.t("status_out")))
        lines.append(_plain(mode.t("score_line", score=user.get_score())))
    else:
        lines.append("💚 Вы участвуете в игре")
        lines.append(_plain(mode.t("score_line", score=user.get_score())))
        if game.is_paused():
            lines.append("⏸️ Игра на паузе — цель временно скрыта.")
        else:
            if user.is_awaiting_confirmation():
                lines.append("⏳ Вы заявили о поимке — ждём подтверждения цели.")
            if user.is_being_caught():
                # Имя «охотника» не раскрываем — как и на сайте.
                lines.append(_plain(mode.t("caught_msg")))

    # Счётчик игроков — как в статусе на сайте (виден всем).
    if game.is_started():
        lines.append(f"👥 Игроков: {game.count_players()} (живы: {game.count_alive()})")
    elif game.is_registration_open():
        lines.append(f"👥 Зарегистрировалось игроков: {game.count_players()}")
    return "\n".join(lines)


def with_status(user, text: str) -> str:
    """Добавить блок статуса к любому ответу бота (статус — под каждым сообщением)."""
    if not text:
        return status_text(user)
    return f"{text}\n\n———\n{status_text(user)}"


# --- цель --------------------------------------------------------------------

def target_status(user):
    """Что показать игроку по кнопке «Моя цель». Возвращает (kind, payload):

      • ('target', имя_цели) — есть активная цель; имя прячем под спойлер (в TG);
      • ('info', текст)      — цели нет/скрыта: игра не идёт, пауза, не в игре, выбыл,
                               ждём подтверждения заявки или цель не назначена.

    Условия совпадают с тем, как цель показывается на сайте (_player_game_split)."""
    game = Game()
    if not game.is_started():
        return ("info", "Игра ещё не началась — цель пока не назначена.")
    if not user.is_player():
        return ("info", "❌ Вы не участвуете в игре.")
    if not user.is_alive():
        return ("info", "🗿 Вы выбыли из игры — цели больше нет.")
    if game.is_paused():
        return ("info", "⏸️ Игра на паузе — цель временно скрыта. "
                        "Дождитесь возобновления.")
    if user.is_awaiting_confirmation():
        return ("info", "⏳ Вы заявили о поимке — ждём подтверждения цели.")
    target = user.get_target_user()
    if target is None:
        return ("info", "Сейчас у вас нет активной цели.")
    return ("target", target.get_name())


# --- заявка о поимке / устранении --------------------------------------------

def report(user) -> str:
    """Главная игровая кнопка. Если включён фото-пруф — сначала просим фото.

    Повторяет главный слот дашборда: пока заявка ждёт ответа цели, та же кнопка
    отзывает заявку. Проверки — те же, что открывают страницу /app/capture.
    """
    blocked = gameflow.capture_block_reason(user)
    if blocked:
        return blocked
    if not user.is_alive():
        return "🗿 Вы выбыли из игры — ловить цель больше нельзя."
    if user.is_awaiting_confirmation():
        ok, msg = gameflow.cancel_capture(user)
        return ("✅ " if ok else "⚠️ ") + msg
    if user.get_target_user() is None:
        return "Сейчас у вас нет активной цели."

    if settings.photo_proof():
        _sessions[user.id] = {"step": "photo"}
        return ("📷 Пришлите фотографию — она нужна как подтверждение "
                f"({_plain(mode.t('report_btn'))}).\n\n"
                "Отправьте фото одним сообщением. Чтобы прервать — «отмена».")
    ok, msg = gameflow.report_capture(user)
    return ("✅ " if ok else "⚠️ ") + msg


def _finish_report_with_photo(user, data: bytes, ext: str) -> str:
    # Жертву фиксируем в имени файла (до заявки) — чтобы снимок остался привязан
    # именно к этой поимке, а не «перебивался» следующей поимкой того же охотника.
    if not photos.save_bytes(user, data, ext, victim=user.get_target_user()):
        return "⚠️ Не удалось сохранить фото. Попробуйте отправить его ещё раз."
    _sessions.pop(user.id, None)
    admin_log.log(f"🖼️ {user.get_name()} приложил(а) фото-пруф поимки цели")
    ok, msg = gameflow.report_capture(user)
    return ("✅ " if ok else "⚠️ ") + msg


def handle_photo(user, data: bytes, ext: str = ".jpg"):
    """Пришло фото. Если ждём фото-пруф — засчитываем заявку, иначе None."""
    s = _sessions.get(user.id)
    if not s or s.get("step") != "photo":
        return None
    return _finish_report_with_photo(user, data, ext)


# --- подтверждение / отклонение поимки ---------------------------------------

def confirm(user) -> str:
    blocked = gameflow.capture_block_reason(user)
    if blocked:
        return blocked
    if not user.is_being_caught():
        return _plain(mode.t("confirm_none"))
    ok, msg = gameflow.confirm_capture(user)
    return ("✅ " if ok else "⚠️ ") + msg


def deny(user) -> str:
    """«Это не так» — спросить необязательную причину, как окошко на сайте (TODO 79)."""
    blocked = gameflow.capture_block_reason(user)
    if blocked:
        return blocked
    if not user.is_being_caught():
        return _plain(mode.t("confirm_none"))
    _sessions[user.id] = {"step": "deny_reason"}
    return ("✍️ Напишите причину, почему вы не подтверждаете это "
            "(её увидят организаторы и заявивший игрок).\n"
            "Если причины нет — напишите «-». Чтобы прервать — «отмена».")


# --- выход из игры ------------------------------------------------------------

def leave(user) -> str:
    if not user.is_player():
        return "ℹ️ Вы и так не участвуете в игре."
    _sessions[user.id] = {"step": "leave_confirm"}
    return ("🚪 Точно выйти из игры? Вернуться можно будет только через новую "
            "регистрацию.\n\nНапишите «да» для подтверждения или «отмена».")


# --- диалоги ------------------------------------------------------------------

def handle(user, text: str):
    """Очередной ТЕКСТОВЫЙ ответ игрока в диалоге. None — диалога нет."""
    s = _sessions.get(user.id)
    if s is None:
        return None
    t = (text or "").strip()
    step = s.get("step")

    if t.lower() in _CANCEL_WORDS and not (step == "deny_reason" and t == "-"):
        _sessions.pop(user.id, None)
        return "❌ Отменено."

    if step == "photo":
        # Случайное нажатие кнопки вместо фото — не ответ, просто напоминаем.
        return ("📷 Ждём именно фотографию — пришлите её одним сообщением "
                "(или напишите «отмена»).")

    if step == "deny_reason":
        _sessions.pop(user.id, None)
        reason = "" if t in ("-", "—") else t
        ok, msg = gameflow.deny_capture(user, reason)
        return ("✅ " if ok else "⚠️ ") + msg

    if step == "leave_confirm":
        _sessions.pop(user.id, None)
        if t.lower() not in _YES_WORDS:
            return "Выход из игры отменён."
        ok, msg = gameflow.leave_game(user)
        return msg

    _sessions.pop(user.id, None)
    return None
