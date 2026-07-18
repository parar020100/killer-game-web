"""Общие примитивы регистрации игрока — единый источник правды для веба и ботов.

Раньше валидация имени, работа с ответами на доп. вопросы и заведение игрока жили в
`app.py` (веб). Бот-процессы `app.py` импортировать не могут (Starlette/itsdangerous),
поэтому эти примитивы вынесены сюда, в `core`, и переиспользуются и веб-формой
регистрации, и диалоговой регистрацией через бота (`core/bot_register.py`).
"""
import json
import re

import config
from core import settings, mode, admin_log
from core.game import Game

# Имя: начинается с буквы, дальше буквы/пробел/дефис (как в веб-форме). Пробел нужен
# для «имя фамилия».
_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё \-]*$")


def validate_name(raw: str):
    """Вернуть (имя, None) при успехе или (None, текст_ошибки). Схлопывает пробелы."""
    name = " ".join((raw or "").split())
    if not (config.NAME_MIN_LEN <= len(name) <= config.NAME_MAX_LEN):
        return None, (f"Имя должно быть от {config.NAME_MIN_LEN} до "
                      f"{config.NAME_MAX_LEN} символов.")
    if not _NAME_RE.match(name):
        return None, "Имя может содержать только буквы, пробел и дефис."
    return name, None


def get_extra_answers(user) -> dict:
    """Ответы игрока на доп. вопросы как {метка: ответ}. Старый одиночный ответ строкой
    читаем как legacy (привязываем к первому вопросу)."""
    raw = (user.get_extra_info() or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    questions = settings.extra_questions()
    return {questions[0][0]: raw} if questions else {"Доп. вопрос": raw}


def set_extra_answers(user, answers: dict):
    user.set_extra_info(json.dumps(answers, ensure_ascii=False) if answers else "")


def join_player(user):
    """Завести игрока в игру (после успешной валидации полей). Как в веб do_join."""
    game = Game()
    user.join(alive=not game.is_started())
    admin_log.log(f"➕ {user.get_name()} зарегистрировал(ся/ась) в игре")
    user.notify(mode.t("joined"))
