# deploy/ — шаблоны и скрипты развёртывания

Готовые файлы, чтобы поднять «Киллер / Папарацци» на сервере по шагам. Полная
инструкция с пояснениями (домен, DNS, HTTPS, диагностика) — в
[../info/DEPLOY.md](../info/DEPLOY.md); базовая настройка — в
[../info/SETUP.md](../info/SETUP.md).

## Файлы

| Файл | Что это |
|------|---------|
| `install.sh` | Шаг 1: система → venv → зависимости → `config.py` (из шаблона, с генерацией `SECRET_KEY`). |
| `setup-service.sh` | Шаг 2: устанавливает systemd-сервис, nginx-сайт и правило sudoers из шаблонов ниже. |
| `killer.service` | Шаблон systemd-юнита (плейсхолдеры `__USER__`, `__APP_DIR__`). |
| `nginx.conf` | Шаблон обратного прокси nginx (плейсхолдер `__DOMAIN__`). |
| `killer.sudoers` | Шаблон правила sudoers для кнопок «Перезагрузить/Выключить» (`__USER__`, `__SERVICE__`). |

Скрипты **идемпотентны** и подставляют плейсхолдеры сами (через `sed`).

## Порядок (Ubuntu 22.04)

```bash
# 0) Код на сервере
sudo mkdir -p /opt/killer && sudo chown $USER:$USER /opt/killer
cd /opt/killer
git clone -b dev_web <URL-репозитория> app
cd app

# 1) Установка (пакеты, venv, зависимости, config.py)
bash deploy/install.sh
nano config.py            # APP_BASE_URL, токены ботов, DEFAULT_ADMINS_TG/VK,
                          # START_MODE="systemctl", SYSTEMD_SERVICE, CODE_DIR

# 2) Сервис + nginx + sudoers (одним скриптом)
sudo DOMAIN=killer.example.com SERVICE_USER=$USER bash deploy/setup-service.sh

# 3) HTTPS
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d killer.example.com

# 4) Проверка + ссылка root-входа
systemctl status killer
journalctl -u killer | grep root      # зайдите по ссылке и выдайте права админам
```

Обновление кода потом — кнопкой «🔁 Управление приложением → Обновить» в веб-UI
(делает `git pull` + рестарт) или вручную:
`cd /opt/killer/app && git pull && sudo systemctl restart killer`.
