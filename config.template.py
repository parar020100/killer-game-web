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

# Базовый адрес сайта — из него боты собирают ссылку входа (/login?token=...).
# В проде укажите публичный адрес (напр. https://killer.example.com).
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000")

# Длина случайной части токена входа (secrets.token_urlsafe).
LOGIN_TOKEN_BYTES = 32

# Отладочный «вход как» через ?user=<id|username>: держать разных пользователей в
# разных вкладках одного браузера. НЕБЕЗОПАСНО. 1 = включить, 0 = выключить.
ALLOW_DEV_LOGIN = 1

# --- Telegram-бот -----------------------------------------------------------
# ENABLE_TG_BOT — общий выключатель Telegram-канала (запуск бота + доставка).
ENABLE_TG_BOT = os.getenv("ENABLE_TG_BOT", "0") == "1"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")         # токен @BotFather
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "")   # напр. @my_game_bot

# --- VK-бот (сообщество) ----------------------------------------------------
# ENABLE_VK_BOT — общий выключатель VK-канала (запуск бота + доставка).
# Пошаговая настройка сообщества — в info/SETUP.md.
ENABLE_VK_BOT = os.getenv("ENABLE_VK_BOT", "0") == "1"
VK_GROUP_TOKEN = os.getenv("VK_GROUP_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")   # число, id сообщества (без минуса)
VK_API_VERSION = "5.199"
VK_BOT_URL = os.getenv("VK_BOT_URL", "")     # напр. https://vk.com/my_game

# --- управление процессами (кнопки «перезагрузить / выключить / обновить») ---
# START_MODE — как запущено приложение (влияет на кнопки управления в настройках):
#   "manual"    — вручную из терминала (./start.sh / run_all.py);
#   "systemctl" — как сервис systemd (перезапуск/остановка через systemctl).
START_MODE = os.getenv("START_MODE", "manual")
SYSTEMD_SERVICE = os.getenv("SYSTEMD_SERVICE", "killer")   # имя unit-сервиса
CODE_DIR = os.getenv("CODE_DIR", "")   # папка для `git pull` (пусто = папка проекта)

# Игровые настройки (контакт поддержки, файл правил, доп. вопросы, подтверждение
# поимок) хранятся в БД и правятся на странице «Настройки игры» (core/settings.py).
# Администраторов не задают в конфиге: при первом запуске создаётся root-
# пользователь, ссылка на вход для него печатается в лог — через него выдают права.
