"""Общая логика ботов (Telegram/VK): вход по ссылке, привязка, пересылка админам.

Платформо-специфична только доставка (как отправить ответ) и получение id/имени
пользователя — это остаётся в tg_bot.py / vk_bot.py. Здесь — общее ядро.
"""
import config
from core import auth, admin_log, linking
from core.user import User


def base_url() -> str:
    return (getattr(config, "APP_BASE_URL", "") or "http://127.0.0.1:8000").rstrip("/")


# Текст постоянной кнопки-«/start» (её нажатие боты трактуют как команду /start).
LOGIN_BUTTON = "🔗 Ссылка для входа"
# Подпись кнопки «открыть меню игры», которая добавляется к каждому уведомлению.
MENU_BUTTON = "🎮 Открыть меню игры"


def menu_url() -> str:
    """Ссылка на игровое меню (дашборд). Внутри веб-вью бота, где пользователь уже
    вошёл по ссылке /start, откроется его дашборд; иначе — предложит войти."""
    return base_url() + "/app"


def issue_login_link(identity) -> tuple[str, bool]:
    """Выдать постоянную ссылку входа для канала. (ссылка, была_ли_перевыпущена)."""
    reissued = auth.has_permanent_token(identity.id)
    token = auth.set_permanent_token(identity.id)
    return f"{base_url()}/login?token={token}", reissued


def welcome_text(link: str, reissued: bool) -> str:
    note = "Прежняя ссылка больше не работает.\n" if reissued else ""
    return (
        "🕵️ Игра «Киллер / Папарацци».\n\n"
        "Ваша постоянная ссылка для входа на сайт (сохраните в закладки):\n"
        f"{link}\n"
        f"{note}Она работает всегда и не имеет срока. Никому её не пересылайте — "
        "по ней входят в вашу учётку. Наберёте /start ещё раз — будет выдана новая, "
        "а старая перестанет работать.\n\n"
        "Всё управление игрой — на сайте по ссылке. Сюда можно писать организаторам, "
        "а командой /link КОД можно привязать этот канал к вашему аккаунту "
        "(код — в «Настройках профиля» на сайте)."
    )


def link_usage() -> str:
    return ("Привязка канала к вашему аккаунту.\n"
            "Использование: /link КОД\n"
            "Код получите на сайте в «Настройках профиля» → «Привязать аккаунт».")


def do_link(identity, code: str) -> str:
    """Обработать `/link КОД` для канала identity. Возвращает текст ответа."""
    if not (code or "").strip():
        return link_usage()
    ok, msg = linking.link_by_code(code, identity)
    return msg


def forward_to_admins(sender_user, platform_label: str, text: str) -> bool:
    """Переслать сообщение игрока всем администраторам (в их каналы). True — если есть кому."""
    who = sender_user.get_name() if sender_user else "неизвестный"
    admins = User.all_admins()
    body = f"✉️ Сообщение от игрока {who} ({platform_label}):\n{text}"
    for a in admins:
        a.notify(body)
    admin_log.log(f"✉️ Игрок {who} ({platform_label}) написал боту: {text}")
    return bool(admins)
