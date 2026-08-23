"""Единый источник inline-меню действий бота.

Кнопки под сообщением должны быть одинаковыми и появляться СРАЗУ — кто бы сообщение
ни отправил: сам процесс бота (ответ на команду) или веб-процесс (игровое
уведомление через tg_send/vk_send). Раньше сборка меню жила в файлах ботов, поэтому
уведомления веб-процесса несли только кнопку-ссылку «Открыть меню игры», а действия
(принять приглашение, подтвердить поимку и т.п.) появлялись лишь при следующем
сообщении боту. Теперь состояние-зависимый набор кнопок собирается здесь и
переиспользуется всеми четырьмя местами (tg_bot, vk_bot, tg_send, vk_send).

`action_rows(user)` — ЕДИНСТВЕННЫЙ источник логики: какие кнопки показать при текущем
состоянии игрока (как кнопки на дашборде). Рендер в конкретный формат (объекты
python-telegram-bot / dict Bot API / JSON VK) — тонкие обёртки в местах-потребителях.
"""
from core import botcommon

# Префикс callback_data кнопок Telegram (нажатие обрабатывает CallbackQueryHandler
# в процессе бота — он запущен независимо от того, кто отправил сообщение).
CB_PREFIX = "act:"


def action_rows(user):
    """[[(label, action), …], …] — набор игровых действий, АКТУАЛЬНЫХ прямо сейчас.

    Показываем только то, что игрок реально может сделать в текущем состоянии (не как
    на дашборде, где недоступное серое — в чате кнопку не «погасить», поэтому лишнюю
    просто не показываем):
      • приглашение в игру → «Присоединиться» / «Отклонить» (только приглашённому);
      • «Регистрация» — только не-игроку и только когда регистрация открыта;
      • «Моя цель» — играющему живому при идущей игре;
      • главная кнопка «Сообщить о поимке» / «Отменить заявку» — живому игроку с целью,
        игра идёт и не на паузе;
      • «Подтвердить» / «Это не так» — только когда о поимке этого игрока заявили;
      • «Выйти из игры» — только участнику игры.
    """
    from core.game import Game
    if user is None:
        return []
    game = Game()
    started, paused = game.is_started(), game.is_paused()
    is_player, alive = user.is_player(), user.is_alive()
    rows = []

    # Приглашение в идущую игру — приглашённому не-игроку.
    if not is_player and user.has_game_invite():
        rows.append([(botcommon.ACCEPT_INVITE_BUTTON, "accept_invite"),
                     (botcommon.REJECT_INVITE_BUTTON, "reject_invite")])

    # Верхний ряд: регистрация (не-игроку при открытой регистрации) и/или «Моя цель»
    # (играющему живому при идущей игре).
    top = []
    if not is_player and game.is_registration_open():
        top.append((botcommon.REGISTER_BUTTON, "register"))
    if is_player and alive and started:
        top.append((botcommon.TARGET_BUTTON, "target"))
    if top:
        rows.append(top)

    # Главная игровая кнопка: сообщить о поимке / отменить заявку — только когда
    # действие реально доступно (живой игрок с целью, игра идёт и не на паузе).
    if (is_player and alive and started and not paused
            and (user.is_awaiting_confirmation() or user.get_target_user() is not None)):
        rows.append([(botcommon.report_button(user), "report")])

    # Ответ на заявку о поимке — только если о поимке этого игрока сейчас заявили.
    if user.is_being_caught():
        rows.append([(botcommon.CONFIRM_BUTTON, "confirm"),
                     (botcommon.DENY_BUTTON, "deny")])

    # Выйти из игры — только участнику.
    if is_player:
        rows.append([(botcommon.LEAVE_BUTTON, "leave")])
    return rows


# --- рендер в форматы платформ ----------------------------------------------

def tg_api_markup(user, menu_uid=None):
    """reply_markup для Telegram Bot API (dict) или None, если кнопок нет. menu_uid —
    добавить сверху кнопку-ссылку «Открыть меню игры» (для уведомлений веб-процесса).
    Кнопки действий — callback (`act:*`), их нажатие обрабатывает процесс бота."""
    kb = []
    if menu_uid is not None:
        kb.append([{"text": botcommon.MENU_BUTTON,
                    "url": botcommon.menu_url_for("tg", menu_uid)}])
    for row in action_rows(user):
        kb.append([{"text": label, "callback_data": CB_PREFIX + action}
                   for label, action in row])
    return {"inline_keyboard": kb} if kb else None


def vk_button_rows(user, menu_uid=None):
    """Строки inline-клавиатуры VK (list). VK маршрутизирует по подписи кнопки
    (см. botcommon.is_*_request), поэтому код действия в payload не нужен.

    ВНИМАНИЕ: у VK inline-клавиатуры лимит 6 кнопок. `action_rows` укладывается в 6
    (взаимоисключающие состояния), поэтому menu_uid-ссылку сверху здесь НЕ добавляем,
    чтобы не превысить лимит — «Открыть меню игры» живёт в нижней клавиатуре."""
    rows = []
    if menu_uid is not None:
        rows.append([{"action": {"type": "open_link",
                                 "link": botcommon.menu_url_for("vk", menu_uid),
                                 "label": botcommon.MENU_BUTTON}}])
    for row in action_rows(user):
        rows.append([{"action": {"type": "text", "label": label}}
                     for label, _action in row])
    return rows
