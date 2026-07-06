#!/usr/bin/env bash
# Шаг установки: система → код → venv → зависимости → config.py.
# Идемпотентно (можно запускать повторно). Ubuntu 22.04.
#
#   cd /opt/killer/app          # папка с кодом (git clone -b dev_web … app)
#   bash deploy/install.sh      # apt-шаги попросят sudo при необходимости
#
# Переменные окружения (необязательно):
#   APP_DIR    — папка проекта (по умолчанию: родитель этого скрипта)
#   NO_APT=1   — пропустить установку системных пакетов (если уже стоят)
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$APP_DIR"
echo "== Проект: $APP_DIR =="

# 1) Системные пакеты
if [ "${NO_APT:-0}" != "1" ]; then
  echo "== Шаг 1: системные пакеты (git, python3-venv, pip, nginx) =="
  sudo apt update
  sudo apt install -y git python3-venv python3-pip nginx
else
  echo "== Шаг 1: пропущен (NO_APT=1) =="
fi

# 2) Виртуальное окружение
echo "== Шаг 2: виртуальное окружение .venv =="
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip

# 3) Зависимости
echo "== Шаг 3: зависимости (setup/requirements.txt) =="
.venv/bin/pip install -r setup/requirements.txt

# 4) Конфигурация
echo "== Шаг 4: config.py =="
if [ ! -f config.py ]; then
  cp config.template.py config.py
  SECRET="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(48))')"
  # подставим сгенерированный SECRET_KEY в свежесозданный config.py
  python3 - "$SECRET" <<'PY'
import re, sys, pathlib
secret = sys.argv[1]
p = pathlib.Path("config.py")
t = p.read_text(encoding="utf-8")
t = re.sub(r'^SECRET_KEY = .*$', f'SECRET_KEY = "{secret}"', t, count=1, flags=re.M)
p.write_text(t, encoding="utf-8")
PY
  echo "  создан config.py из шаблона (SECRET_KEY сгенерирован)."
  echo "  ▶ отредактируйте config.py: APP_BASE_URL, токены ботов, DEFAULT_ADMINS_TG/VK,"
  echo "    START_MODE=\"systemctl\", SYSTEMD_SERVICE, CODE_DIR."
else
  echo "  config.py уже существует — не трогаю."
fi

echo
echo "== Готово. Дальше =="
echo "  1) nano config.py                       # прод-значения"
echo "  2) bash deploy/setup-service.sh         # systemd + nginx + sudoers"
echo "  3) sudo certbot --nginx -d ВАШ_ДОМЕН    # HTTPS"
echo "  См. подробности в info/DEPLOY.md."
