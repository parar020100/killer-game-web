"""Веб-версия игры «Убийца / Папарацци» — стартовый каркас (FastAPI + Jinja2).

Пока это динамический макет главного экрана: Python формирует текст статуса и набор
кнопок (как в исходном боте) и отдаёт их в шаблон. Данные захардкожены — БД и реальная
логика подключатся на следующих шагах (см. план в web_version/README.md).

Запуск:
    cd web_version
    uvicorn app:app --reload
    # затем открыть http://127.0.0.1:8000
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Killer / Paparazzi — web")

SUPPORT_CONTACT = "@parar020100"


def _btn(label, kind="", full=False):
    """Кнопка нейтрального вида: kind ∈ {'', 'primary', 'danger'}, full — во всю ширину."""
    return {"label": label, "kind": kind, "full": full}


def player_view():
    """Экран игрока: игра идёт, игрок жив (как в user_commands_inline_keyboard исходника)."""
    message = "\n".join([
        '<span class="hi">Привет, Артём!</span>',
        "📷 Добро пожаловать в игру Папарацци!",
        "",
        '<span class="divider">══ 📋 Статус игры ══</span>',
        "🟢 Игра <strong><em>запущена</em></strong>",
        "💚 <em>Вы участвуете в игре</em>",
        "🧮 <em>Вы поймали <strong>3</strong> человек</em>",
        "👥 Игроков: <strong>12</strong> (живы: <strong>7</strong>)",
    ])
    buttons = [
        _btn("🎯 Узнать цель", "primary"),
        _btn("📸 Сообщить о поимке", "primary"),
        _btn("📜 Правила"),
        _btn("👤 Изменить профиль"),
        _btn("🔴 Выйти из игры", "danger"),
        _btn("🤫 Секрет бота"),
        _btn("⛔ Отключить бота", "danger", full=True),
        _btn("🔄 Обновить", full=True),
    ]
    return message, buttons


def admin_view():
    """Экран админа: игра на паузе (как в admin_commands_inline_keyboard исходника)."""
    message = "\n".join([
        '<span class="hi">Привет, Артём!</span>',
        "📷 Ты — администратор игры. Добро пожаловать!",
        "",
        '<span class="divider">══ 📋 Статус игры ══</span>',
        "⏸️ Игра <strong><em>приостановлена</em></strong>",
        "🟡 <strong><em>Разрешена регистрация</em></strong> на игру",
        "👥 Игроков: <strong>12</strong> (живы: <strong>7</strong>)",
    ])
    buttons = [
        _btn("👤 Открыть меню игрока", full=True),
        _btn("▶️ Возобновить", "primary"),
        _btn("✅ Открыть регистрацию"),
        _btn("↩️ Сброс игры", "danger"),
        _btn("⏹️ Закончить игру"),
        _btn("👥 Пользователи"),
        _btn("🪪 Профиль игрока"),
        _btn("🏆 Результаты"),
        _btn("🚫 Выкл-ть ADMIN LOG"),
        _btn("🔑 Настроить пароль"),
        _btn("📄 Лог"),
        _btn("🔁 Перезагрузить бота"),
        _btn("🛑 Остановить бота", "danger"),
        _btn("💣 Полный сброс бота", "danger", full=True),
        _btn("🔄 Обновить", full=True),
    ]
    return message, buttons


@app.get("/", response_class=HTMLResponse)
def index(request: Request, role: str = "player"):
    if role == "admin":
        message, buttons = admin_view()
    else:
        role = "player"
        message, buttons = player_view()

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "role": role,
            "message_html": message,
            "buttons": buttons,
            "support_contact": SUPPORT_CONTACT,
        },
    )
