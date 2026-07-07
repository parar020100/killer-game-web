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
# Совпадает с надписью нативной кнопки «Начать» в VK, чтобы вести себя одинаково.
LOGIN_BUTTON = "▶️ Начать"
# Подпись кнопки «открыть меню игры», которая добавляется к каждому уведомлению.
MENU_BUTTON = "🎮 Открыть меню игры"
# Кнопка-ссылка на страницу правил игры (добавляется к приветствию).
RULES_BUTTON = "📖 Правила игры"


def rules_url() -> str:
    """Ссылка на страницу правил игры (<домен>/rules)."""
    return base_url() + "/rules"


def intro_text() -> str:
    """Приветствие на пустом экране чата (до первого «Начать»): сайт, правила, призыв.

    В Telegram назначается автоматически (`setMyDescription`); в VK задать приветствие
    через API нельзя, поэтому текст генерируется в файл `intro.txt` при запуске бота —
    его вставляют в «Приветствие» сообщества вручную (см. SETUP.md)."""
    return (
        "🕵️ Добро пожаловать в бота для игры «Киллер / Папарацци»!\n\n"
        f"Сайт игры: {base_url()}\n"
        f"Правила: {rules_url()}\n\n"
        "Нажмите «Начать» для продолжения..."
    )


def is_login_request(text: str) -> bool:
    """True, если текст — нажатие кнопки «Начать» (равносильно команде /start)."""
    return (text or "").strip() in (LOGIN_BUTTON, "Начать")


def menu_url() -> str:
    """Ссылка на игровое меню (дашборд). Внутри веб-вью бота, где пользователь уже
    вошёл по ссылке /start, откроется его дашборд; иначе — предложит войти."""
    return base_url() + "/app"


def menu_url_for(platform: str, platform_uid) -> str:
    """Ссылка «Открыть меню игры» с постоянным токеном канала — открывает игру сразу
    в аккаунте пользователя, без отдельной генерации ссылки (TODO 76).

    Переиспользует уже сохранённый токен канала; создаёт при отсутствии. Для легаси-
    записей (хранился лишь хеш) токена нет — откатываемся на бестокенный /app (там
    сработает вход по webview-сессии или предложение войти)."""
    from core.identity import Identity
    ident = Identity.by_platform(platform, platform_uid)
    if ident:
        token = auth.get_or_create_permanent_token(ident.id)
        if token:
            return f"{base_url()}/login?token={token}"
    return menu_url()


def login_link(identity) -> str:
    """Ссылка входа для канала. `/start` НЕ сбрасывает ссылку — переиспользуем уже
    сохранённый токен, создаём новый только если его ещё нет (в т.ч. легаси-запись
    без сырого токена). Сменить ссылку можно на сайте в «Настройках профиля» (TODO 84)."""
    token = auth.get_or_create_permanent_token(identity.id)
    if not token:  # легаси-запись только с хешем — переиспользовать нельзя, выдаём новую
        token = auth.set_permanent_token(identity.id)
    return f"{base_url()}/login?token={token}"


def welcome_text(link: str) -> str:
    from core import settings
    support = (settings.support_contact() or "").strip()
    support_line = f"\nПо всем вопросам пишите {support}" if support else ""
    return (
        "🕵️ Добро пожаловать в игру «Киллер / Папарацци»!\n\n"
        f"Ссылка для входа: {link}\n"
        "Не давайте ссылку другим — по ней входят в вашу учётку.\n"
        "Сменить ссылку можно на сайте в «Настройках профиля».\n\n"
        "Кнопки ниже: открыть меню игры и правила. Всё управление игрой — на сайте."
        f"{support_line}"
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
