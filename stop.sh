#!/usr/bin/env bash
# Полная остановка приложения: снять веб-сервер и оба бота.
#
# Обычно достаточно нажать Ctrl+C в окне ./start.sh — run_all.py сам гасит всё.
# Этот скрипт — страховка на случай «зависших» процессов (напр. после запусков
# по отдельности или uvicorn --reload, который плодит воркеры).
#
# Работает в Git Bash (Windows) и Linux/macOS. На проде, где стоит systemd,
# останавливайте сервисом:  sudo systemctl stop killer
cd "$(dirname "$0")"

# Вехи скрипта — в лог бота (data/bot_log.txt), тем же построчным форматом.
blog() {
    mkdir -p data 2>/dev/null || true
    printf '[%s] [stop.sh] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> data/bot_log.txt 2>/dev/null || true
    echo "$*"
}

blog "остановка приложения запрошена (stop.sh)"

# Ищем ТОЛЬКО процессы python с этими командными строками (чтобы случайно не снять
# сам bash/терминал, из которого запускали — у него в cmdline тоже есть эти имена).
PAT='run_all\.py|uvicorn app:app|tg_bot\.py|vk_bot\.py'

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)   # Git Bash / MSYS на Windows
    powershell -NoProfile -Command \
      "Get-CimInstance Win32_Process | Where-Object { \$_.Name -like 'python*' -and \$_.CommandLine -match '$PAT' } | ForEach-Object { Write-Host ('  kill PID {0} ({1})' -f \$_.ProcessId, \$_.Name); Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"
    ;;
  *)                      # Linux / macOS — матчим только python-процессы
    for p in "run_all\.py" "uvicorn app:app" "tg_bot\.py" "vk_bot\.py"; do
      if pkill -f "python[0-9.]* .*$p" 2>/dev/null; then
        echo "  снят: $p"
      fi
    done
    ;;
esac

blog "приложение остановлено"
