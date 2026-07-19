"""Диалоговая регистрация в игре через бота (Telegram/VK) — базовое участие без сайта.

Бот пошагово спрашивает поля регистрации и заводит игрока:
  1. настоящее имя и фамилия (валидация как на сайте);
  2. пароль игры — если он задан (обязательная проверка);
  3. ответы на доп. вопросы регистрации — по одному, если заданы.
Затем игрок регистрируется (`registration.join_player`) и получает ссылку на сайт.

Состояние диалога хранится в ПАМЯТИ процесса бота, по `user.id`. Регистрация идёт в
одном канале (у пользователя один активный диалог), поэтому одного процесса
достаточно. При перезапуске бота незавершённая регистрация теряется — не критично
(пользователь начинает заново командой/кнопкой).

Оба бота используют один и тот же автомат:
  • `in_progress(user_id)` — идёт ли регистрация (проверять ПЕРВЫМ в обработчике);
  • `start(user, identity)` — начать (вернуть первый вопрос или причину отказа);
  • `handle(user, identity, text)` — обработать ответ, вернуть следующий вопрос/итог.
Все они возвращают текст ответа игроку.
"""
from core import registration, settings, botcommon
from core.game import Game

# user_id -> {"step": "name"|"password"|"extra", "name": str, "answers": {метка: ответ}, "idx": int}
_sessions = {}

_CANCEL_WORDS = {"/cancel", "cancel", "отмена", "отменить", "стоп"}


def in_progress(user_id) -> bool:
    return user_id in _sessions


def start(user, identity) -> str:
    """Начать регистрацию. Вернуть первый вопрос или причину, почему нельзя."""
    game = Game()
    if user.is_player():
        return "✅ Вы уже зарегистрированы в игре."
    if not game.is_registration_open():
        return ("🚫 Регистрация на игру сейчас закрыта. "
                "Дождитесь, когда организаторы её откроют.")
    _sessions[user.id] = {"step": "name", "name": "", "answers": {}, "idx": 0}
    return ("📝 Регистрация в игре.\n"
            "Как вас зовут? Напишите настоящие имя и фамилию.\n\n"
            "(в любой момент напишите «отмена», чтобы прервать)")


def handle(user, identity, text: str):
    """Обработать очередной ответ игрока. Вернуть текст следующего шага/итога.

    Возвращает None, если регистрация не идёт (вызывающему стоит проверять
    `in_progress` заранее)."""
    s = _sessions.get(user.id)
    if s is None:
        return None
    t = (text or "").strip()
    if t.lower() in _CANCEL_WORDS:
        _sessions.pop(user.id, None)
        return "❌ Регистрация отменена. Чтобы начать заново — «Регистрация» или /register."
    # Случайное нажатие кнопок клавиатуры во время диалога — не ответ на вопрос.
    if t in (botcommon.LOGIN_BUTTON, botcommon.REGISTER_BUTTON, botcommon.TARGET_BUTTON,
             botcommon.CONFIRM_BUTTON, botcommon.DENY_BUTTON, botcommon.STATUS_BUTTON,
             botcommon.LEAVE_BUTTON, botcommon.MENU_BUTTON, botcommon.RULES_BUTTON
             ) or t == botcommon.report_button():
        return "Идёт регистрация. Ответьте на вопрос выше или напишите «отмена»."

    game = Game()
    step = s["step"]

    if step == "name":
        name, err = registration.validate_name(t)
        if err:
            return f"⚠️ {err}\nНапишите имя и фамилию ещё раз:"
        s["name"] = name
        return _after_name(user, s, game, identity)

    if step == "password":
        if t != (game.get_password() or ""):
            return "❌ Неверный пароль игры. Введите пароль ещё раз (или «отмена»):"
        return _start_extra_or_finish(user, s, game, identity)

    if step == "extra":
        if not t:
            return "⚠️ Пустой ответ. Пожалуйста, ответьте на вопрос:"
        questions = settings.extra_questions()
        # Вопросы могли измениться на лету — подстрахуемся индексом.
        if s["idx"] >= len(questions):
            return _finish(user, s, identity)
        label = questions[s["idx"]][0]
        s["answers"][label] = t
        s["idx"] += 1
        return _ask_extra_or_finish(user, s, game, identity)

    # неизвестный шаг — сбросить
    _sessions.pop(user.id, None)
    return None


# --- переходы между шагами ---------------------------------------------------

def _after_name(user, s, game, identity) -> str:
    if game.get_password():
        s["step"] = "password"
        return "🔑 Введите пароль игры (спросите его у организатора):"
    return _start_extra_or_finish(user, s, game, identity)


def _start_extra_or_finish(user, s, game, identity) -> str:
    questions = settings.extra_questions()
    if questions:
        s["step"] = "extra"
        s["idx"] = 0
        return _extra_prompt(questions[0], 0, len(questions))
    return _finish(user, s, identity)


def _ask_extra_or_finish(user, s, game, identity) -> str:
    questions = settings.extra_questions()
    if s["idx"] < len(questions):
        return _extra_prompt(questions[s["idx"]], s["idx"], len(questions))
    return _finish(user, s, identity)


def _extra_prompt(question_pair, idx: int, total: int) -> str:
    _label, question = question_pair
    prefix = f"❓ Вопрос {idx + 1} из {total}:\n" if total > 1 else "❓ "
    return f"{prefix}{question}"


def _finish(user, s, identity) -> str:
    user.set_real_name(s["name"])
    registration.set_extra_answers(user, s["answers"])
    registration.join_player(user)
    _sessions.pop(user.id, None)
    link = botcommon.login_link(identity)
    return ("✅ Готово! Вы зарегистрированы в игре как "
            f"«{user.get_name()}».\n\n"
            "Всё управление игрой — на сайте. Ваша ссылка для входа:\n"
            f"{link}")
