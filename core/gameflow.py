"""Игровые действия участника — ОДНА реализация для сайта и для ботов.

Раньше проверки состояния («игра идёт», «не на паузе», «вы участвуете») и выход из
игры жили прямо в app.py (`_do_action`). Боты — отдельные процессы и app.py
импортировать не могут, поэтому при добавлении игровых действий в бота логика
неизбежно разъехалась бы с сайтом. Здесь она общая: и веб-маршрут, и бот вызывают
одни и те же функции, значит и условия доступности, и тексты, и уведомления,
и записи в ADMIN LOG совпадают.

Все функции возвращают `(ok, сообщение)`; сообщение — готовый текст для игрока.
"""
from core import admin_log, mode, registration
from core.game import Game
from core.user import User


# --- уведомления о смене цели ------------------------------------------------
# Имя цели НЕ раскрывается в уведомлениях (ни на сайте, ни в боте) — только факт
# смены и приглашение открыть игру. Имя цель показывает лишь сам интерфейс
# (на сайте — под спойлером, в Telegram — под <tg-spoiler>).

def notify_target(user: User, changed: bool = False):
    """Уведомить игрока, что цель назначена/изменилась (БЕЗ раскрытия имени)."""
    if user.get_target_user():
        user.notify(mode.t("target_changed" if changed else "target_assigned"))


def snapshot_targets():
    return {u.id: u.get_target_id() for u in User.alive_players()}


def notify_retargets(before, reason_key=None):
    """Уведомить о смене цели тех живых игроков, у кого она изменилась (без имени).

    `reason_key` (напр. "retarget_left") — объяснить ПРИЧИНУ смены формулировкой из
    бота; без него — нейтральное «цель изменилась».
    """
    reason = mode.t(reason_key) if reason_key else None
    for u in User.alive_players():
        new = u.get_target_id()
        if new and before.get(u.id) != new:
            if reason:
                u.notify(reason + "\n🎯 Откройте приложение, чтобы узнать новую цель.")
            else:
                notify_target(u, changed=True)


# --- общие проверки ----------------------------------------------------------

def capture_block_reason(user: User):
    """Почему игровое действие (заявка/подтверждение/отказ) сейчас недоступно.

    Возвращает текст причины или None, если действие допустимо. Ровно те же
    проверки, что были в `app.py::_do_action` (и в боте `h_user`): игра запущена,
    не на паузе, пользователь участвует.
    """
    game = Game()
    if not game.is_started():
        return "⛔ Игра ещё не началась."
    if game.is_paused():
        return "⏸️ Игра на паузе — действие сейчас недоступно."
    if not user.is_player():
        return "❌ Вы не участвуете в игре."
    return None


def report_capture(user: User):
    """Заявить о поимке/устранении своей цели.

    ВАЖНО: фото-пруф (если включён в настройках) здесь не проверяется — его
    запрашивает интерфейс ДО вызова (на сайте — страница /app/capture, в боте —
    шаг диалога), как и было в вебе.
    """
    err = capture_block_reason(user)
    if err:
        return False, err
    return user.attempt_capture()


def cancel_capture(user: User):
    err = capture_block_reason(user)
    if err:
        return False, err
    return user.cancel_capture()


def confirm_capture(user: User):
    err = capture_block_reason(user)
    if err:
        return False, err
    return user.confirm_capture()


def deny_capture(user: User, reason: str = ""):
    err = capture_block_reason(user)
    if err:
        return False, err
    return user.deny_capture(reason)


# --- приглашение незарегистрированного в идущую игру (TODO 94) ---------------
# Админ зовёт пользователя, тот принимает/отклоняет (как подтверждение поимки).
# При приёме — присоединяется «мёртвым» в случайной позиции круга, без паузы.

def invite_to_game(admin: User, user: User):
    """Админ приглашает незарегистрированного пользователя в идущую игру."""
    game = Game()
    if not game.is_started():
        return False, "Пригласить в игру можно только когда игра идёт."
    if user.is_player():
        return False, "Пользователь уже участвует в игре."
    # Повторно приглашать МОЖНО: игрок мог случайно отклонить, а спам не проблема —
    # пользователь всегда может заблокировать бота. add_game_invite идемпотентно.
    resent = user.has_game_invite()
    user.add_game_invite()
    verb = "повторно пригласил(а)" if resent else "пригласил(а)"
    admin_log.log(f"✉️ {admin.get_name()} {verb} {user.get_name()} в игру")
    user.notify("✉️ Организаторы приглашают вас присоединиться к идущей игре!\n"
                "Чтобы принять — пройдите регистрацию (имя, пароль игры, анкета). "
                "Вы войдёте выбывшим и сможете вернуться в игру позже.\n"
                "Откройте меню (или бот), чтобы принять или отклонить приглашение.")
    again = " (повторно)" if resent else ""
    return True, f"Приглашение отправлено{again}: {user.get_name()}."


# Принятие приглашения запускает ПОЛНУЮ регистрацию (веб-форма /app/join или диалог
# бота), а не заводит игрока напрямую — само приглашение служит разрешением
# зарегистрироваться при закрытой общей регистрации (registration.registration_allowed)
# и снимается по завершении (User.join). Поэтому отдельной accept_invite здесь нет.

def reject_invite(user: User):
    """Пользователь отклоняет приглашение."""
    if not user.has_game_invite():
        return False, "Активного приглашения в игру нет."
    user.remove_game_invite()
    admin_log.log(f"🚫 {user.get_name()} отклонил(а) приглашение в игру")
    return True, "Приглашение отклонено."


def revoke_invite(admin: User, user: User):
    """Админ отзывает ранее отправленное приглашение (передумал/ошибся)."""
    if not user.has_game_invite():
        return False, "У пользователя нет активного приглашения."
    user.remove_game_invite()
    admin_log.log(f"🚫 {admin.get_name()} отозвал(а) приглашение в игру: {user.get_name()}")
    # Уведомляем игрока: заодно у него в боте обновится меню (кнопки принять/отклонить
    # исчезнут, т.к. приглашения больше нет).
    user.notify("✉️ Организаторы отозвали приглашение в игру.")
    return True, f"Приглашение отозвано: {user.get_name()}."


# --- выход из игры -----------------------------------------------------------

def leave_game(user: User):
    """Игрок выходит из игры сам (кнопка «🚪 Выйти из игры»)."""
    if not user.is_player():
        return False, "ℹ️ Вы и так не участвуете в игре."
    game = Game()
    was_alive = user.is_alive()
    before = snapshot_targets()
    score = user.get_score()   # до leave(): он обнуляет счёт
    user.leave()
    admin_log.log(f"➖ {user.get_name()} вышел(ла) из игры")
    user.notify(mode.t("leave_self", score=score))
    # Если игра шла, а игрок был жив — чинить круг (в боте выход НЕ оживляет
    # очередь): пересобрать цели, уведомить «охотника» выбывшего о смене цели и
    # проверить конец игры.
    if was_alive and game.is_started():
        game.reassign_targets()
        notify_retargets(before, "retarget_left")
        if game.check_finished():
            game.announce_winner()
    return True, "🚪 Вы вышли из игры."
