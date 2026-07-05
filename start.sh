#!/usr/bin/env bash
# Запуск веб-версии игры «Киллер / Папарацци».
#
# Делает всё сам, чтобы не запоминать команды:
#   1. создаёт venv (.venv) при первом запуске;
#   2. ставит/обновляет зависимости из requirements.txt;
#   3. поднимает сервер uvicorn с автоперезагрузкой.
#
# Использование:
#   ./start.sh              # запуск на http://127.0.0.1:8000
#   ./start.sh 9000         # свой порт
#   HOST=0.0.0.0 ./start.sh # слушать все интерфейсы (доступ по сети)
#
# Работает в Git Bash (Windows), Linux и macOS. Всё вызывается через `python -m …`,
# поэтому PATH к uvicorn.exe/pip.exe не нужен (см. заметку в README.md).

set -euo pipefail

# Перейти в папку скрипта, чтобы запускать откуда угодно.
cd "$(dirname "$0")"

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
"$VENV_PY" -m pip install -r requirements.txt

echo "🚀 Запускаю сервер:  http://${HOST}:${PORT}   (Ctrl+C — остановить)"
exec "$VENV_PY" -m uvicorn app:app --reload --host "$HOST" --port "$PORT"
