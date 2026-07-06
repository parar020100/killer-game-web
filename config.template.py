"""Шаблон настроек веб-версии «Киллер / Папарацци» — аналог config.template.py бота.

Скопируйте этот файл в `config.py` и подставьте свои значения:

    cp config.template.py config.py        # Linux/macOS/Git Bash
    copy config.template.py config.py      # Windows cmd

Сам `config.py` в `.gitignore` — он локальный и может содержать секреты.
Секреты лучше передавать через переменные окружения (см. SECRET_KEY).
"""
import os

# --- инфраструктура ---------------------------------------------------------

# Путь к файлу БД (относительно папки проекта). Смена файла = другая «игра».
DB_NAME = "data/killer_game.db"

# Секрет подписи cookie-сессии. В проде ОБЯЗАТЕЛЬНО задайте переменную окружения
# SECRET_KEY (длинную случайную строку), иначе сессии можно подделать.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-secret-change-me")

# Границы валидации игрового имени.
NAME_MIN_LEN = 2
NAME_MAX_LEN = 50

# Telegram-бот: токен от @BotFather. Пусто = бот отключён (остаётся эмуляция чата).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# VK-бот сообщества (Long Poll API): ключ доступа сообщества и его числовой id.
# Пусто = VK-бот отключён. Пошаговая настройка — в info/SETUP.md.
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")   # число, id сообщества (без минуса)
VK_API_VERSION = "5.199"

# Ссылки на ботов для страницы входа (кнопки «Авторизоваться через …»).
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")   # напр. @my_game_bot
VK_BOT_URL = os.getenv("VK_BOT_URL", "")                         # напр. https://vk.com/my_game

# Базовый адрес сайта — из него бот собирает ссылку входа (/login?token=...).
# В проде укажите публичный адрес (напр. https://killer.example.com).
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")

# Длина случайной части токена входа (secrets.token_urlsafe).
LOGIN_TOKEN_BYTES = 32

# Отладочный «вход как» через ?user=<id|username>: держать разных пользователей в
# разных вкладках одного браузера. НЕБЕЗОПАСНО. 1 = включить, 0 = выключить.
ALLOW_DEV_LOGIN = 1

# Игровые настройки (контакт поддержки, файл правил, доп. вопросы, подтверждение
# поимок) хранятся в БД и правятся на странице «Настройки игры» (core/settings.py).
# Администраторов не задают в конфиге: при первом запуске создаётся root-
# пользователь, ссылка на вход для него печатается в лог — через него выдают права.
