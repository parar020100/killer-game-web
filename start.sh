#!/usr/bin/env bash
# Запуск веб-версии игры «Киллер / Папарацци» — ВСЁ СРАЗУ.
#
# Делает всё сам, чтобы не запоминать команды:
#   1. создаёт venv (.venv) при первом запуске;
#   2. ставит/обновляет зависимости из setup/requirements.txt;
#   3. запускает веб-сервер + Telegram-бот + VK-бот в одном окне (run_all.py).
#      Боты стартуют, только если в config.py заданы их токены (иначе пропускаются,
#      остаётся эмуляция чата). У каждого свой префикс в выводе: [web]/[tg]/[vk].
#
# Использование:
#   ./start.sh              # запуск на http://127.0.0.1:8000 + боты
#   ./start.sh 9000         # свой порт
#   HOST=0.0.0.0 ./start.sh # слушать все интерфейсы (доступ по сети)
#
# Только веб с автоперезагрузкой (для разработки): python -m uvicorn app:app --reload
#
# Работает в Git Bash (Windows), Linux и macOS. Всё вызывается через `python -m …`,
# поэтому PATH к uvicorn.exe/pip.exe не нужен (см. заметку в README.md).

set -euo pipefail

# Перейти в папку скрипта, чтобы запускать откуда угодно.
cd "$(dirname "$0")"

# Локальный config.py в .gitignore — создаём из шаблона при первом запуске.
if [ ! -f "config.py" ]; then
    echo "⚙️  Создаю config.py из config.template.py (отредактируйте под себя) ..."
    cp config.template.py config.py
fi

PORT="${1:-8000}"
HOST="${HOST:-127.0.0.1}"

# Найти интерпретатор Python (py -3 на Windows, иначе python3/python).
if command -v py >/dev/null 2>&1; then
    PY="py -3"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    echo "❌ Python не найден. Установите Python 3 и повторите." >&2
    exit 1
fi

# Создать виртуальное окружение при первом запуске.
if [ ! -d ".venv" ]; then
    echo "📦 Создаю виртуальное окружение .venv ..."
    $PY -m venv .venv
fi

# Путь к python внутри venv (Windows: Scripts, Unix: bin).
if [ -x ".venv/Scripts/python.exe" ]; then
    VENV_PY=".venv/Scripts/python.exe"
else
    VENV_PY=".venv/bin/python"
fi

echo "📥 Проверяю зависимости ..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install -r setup/requirements.txt

echo "🚀 Запускаю веб + боты:  http://${HOST}:${PORT}   (Ctrl+C — остановить всё)"
export HOST PORT
exec "$VENV_PY" run_all.py
