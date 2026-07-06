#!/usr/bin/env bash
# Шаг развёртывания: systemd-сервис + nginx-сайт + правило sudoers из шаблонов
# папки deploy/. Подставляет ваши значения в плейсхолдеры и устанавливает файлы.
#
#   sudo DOMAIN=killer.example.com SERVICE_USER=ubuntu bash deploy/setup-service.sh
#
# Переменные окружения:
#   DOMAIN        — домен сайта (обязательно для nginx)
#   SERVICE_USER  — пользователь-владелец кода (по умолчанию: SUDO_USER или текущий)
#   SERVICE       — имя сервиса systemd (по умолчанию killer)
#   APP_DIR       — папка проекта (по умолчанию родитель этого скрипта)
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SERVICE="${SERVICE:-killer}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
DOMAIN="${DOMAIN:-}"
DEPLOY="$APP_DIR/deploy"

if [ "$(id -u)" != "0" ]; then
  echo "Запустите через sudo (нужны права на /etc)." >&2; exit 1
fi

echo "== APP_DIR=$APP_DIR  SERVICE=$SERVICE  USER=$SERVICE_USER  DOMAIN=${DOMAIN:-<нет>} =="

subst() { sed -e "s#__APP_DIR__#$APP_DIR#g" -e "s#__USER__#$SERVICE_USER#g" \
              -e "s#__SERVICE__#$SERVICE#g" -e "s#__DOMAIN__#$DOMAIN#g" "$1"; }

# 1) systemd-сервис
echo "== systemd: /etc/systemd/system/$SERVICE.service =="
subst "$DEPLOY/killer.service" > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"

# 2) sudoers (кнопки управления из UI)
echo "== sudoers: /etc/sudoers.d/$SERVICE =="
subst "$DEPLOY/killer.sudoers" > "/tmp/$SERVICE.sudoers"
install -m 0440 "/tmp/$SERVICE.sudoers" "/etc/sudoers.d/$SERVICE"
visudo -cf "/etc/sudoers.d/$SERVICE"
rm -f "/tmp/$SERVICE.sudoers"

# 3) nginx (если задан DOMAIN)
if [ -n "$DOMAIN" ]; then
  echo "== nginx: /etc/nginx/sites-available/$DOMAIN =="
  subst "$DEPLOY/nginx.conf" > "/etc/nginx/sites-available/$DOMAIN"
  ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
  nginx -t && systemctl reload nginx
  echo "  Теперь HTTPS:  sudo certbot --nginx -d $DOMAIN"
else
  echo "== nginx: пропущен (не задан DOMAIN) =="
fi

echo
echo "== Готово. Проверка =="
echo "  systemctl status $SERVICE"
echo "  journalctl -u $SERVICE -f        # ссылка root-входа печатается при старте"
