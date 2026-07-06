# Развёртывание на сервере — «Киллер / Папарацци»

Полная инструкция: домен и DNS, nginx, HTTPS, установка приложения и ботов,
автозапуск через systemd и управление из веб-интерфейса.

Базовая настройка (config.py, боты, привязка каналов) — в [SETUP.md](SETUP.md).
Здесь — всё про прод-развёртывание на отдельном хосте под доменом.

---

## 0. Схема

- **Веб-приложение** (FastAPI/uvicorn) слушает локально `127.0.0.1:8000`.
- **nginx** на том же хосте терминирует HTTPS и проксирует на приложение.
- **Telegram- и VK-боты** — отдельные процессы; всё вместе поднимает `run_all.py`.
- Автозапуск и перезапуск — через **systemd** (`START_MODE = "systemctl"`).

---

## 1. Домен и DNS

Пусть основной домен `parar.ru` уже ведёт на первый сервер, а это приложение —
на другом хосте `IP_2`, и нужен адрес `killer.parar.ru`.

В DNS добавь **точную** запись для поддомена — она приоритетнее wildcard:
```
killer  A  IP_2          # ← добавить
*       A  IP_1          # остаётся для остальных поддоменов
@       A  IP_1
```
`killer.parar.ru` начнёт резолвиться в `IP_2` напрямую (без первого сервера) —
это надёжнее, чем проксировать через него.

**Скорость распространения** = TTL записи. Заранее снизь TTL (до 300–600 c),
дождись истечения старого, потом меняй. Проверка:
```bash
dig killer.parar.ru @8.8.8.8 +short      # или whatsmydns.net
```
`killer` — новое имя, большинство резолверов увидят его почти сразу; «хвост» до
суток бывает только у тех, кто уже кэшировал имя со старым TTL.

---

## 2. Пакеты на сервере (Ubuntu 22.04)
```bash
sudo apt update
sudo apt install -y git python3-venv python3-pip nginx
```

## 3. Код приложения
```bash
sudo mkdir -p /opt/killer && sudo chown $USER:$USER /opt/killer
cd /opt/killer
git clone -b dev_web <URL-репозитория> app     # или scp/rsync папки без .venv и data
cd app
```

## 4. Виртуальное окружение и зависимости
```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r setup/requirements.txt
```

## 5. Конфигурация (`config.py`)
`config.py` в `.gitignore` — создаём из шаблона и правим:
```bash
cp config.template.py config.py
nano config.py
```
Прод-значения:
```python
APP_BASE_URL = "https://killer.parar.ru"     # ссылки ботов станут https
SECRET_KEY = "<длинная-случайная-строка>"    # см. команду ниже
ALLOW_DEV_LOGIN = 0                          # ОБЯЗАТЕЛЬНО в проде

ENABLE_TG_BOT = True
TELEGRAM_BOT_TOKEN = "…"
TELEGRAM_BOT_USERNAME = "@msu_killer_paparazzi_bot"

ENABLE_VK_BOT = True
VK_GROUP_TOKEN = "vk1.a…."
VK_GROUP_ID = "240073747"
VK_BOT_URL = "https://vk.com/msu_killer_paparazzi"

START_MODE = "systemctl"        # управление процессом — через systemd
SYSTEMD_SERVICE = "killer"      # имя сервиса (см. шаг 8)
CODE_DIR = "/opt/killer/app"    # для кнопки «Обновить» (git pull)
```
Сгенерировать `SECRET_KEY`:
```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

**PostgreSQL (опционально).** По умолчанию используется SQLite (файл `DB_NAME`).
Для перехода на PostgreSQL в `config.py`:
```python
DB_BACKEND = "postgres"
POSTGRES_DSN = "postgresql://killer:пароль@localhost:5432/killer"
```
и поставьте драйвер (в `setup/requirements.txt` строка закомментирована):
```bash
.venv/bin/pip install "psycopg[binary]>=3.1"
```
Схема создаётся автоматически при старте. Выбор «игры по файлу БД» в PostgreSQL
не применяется (одна база); фото и истории чата по-прежнему хранятся в `data/`.

## 6. nginx: обратный прокси
```bash
sudo nano /etc/nginx/sites-available/killer.parar.ru
```
```nginx
server {
    listen 80;
    listen [::]:80;
    server_name killer.parar.ru;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```
```bash
sudo ln -s /etc/nginx/sites-available/killer.parar.ru /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default        # по желанию
sudo nginx -t && sudo systemctl reload nginx
```

## 7. HTTPS (Let's Encrypt)
certbot на Ubuntu 22.04 ставится отдельно:
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d killer.parar.ru
```
certbot сам добавит `listen 443 ssl`, сертификаты, редирект 80→443 и автопродление.
Проверка продления: `sudo certbot renew --dry-run`.

**Порты 80/443 должны быть открыты снаружи** (иначе certbot не пройдёт проверку):
```bash
sudo ss -tlnp | grep -E ':80|:443'        # слушает ли nginx
sudo ufw status                            # локальный фаервол (если включён: ufw allow 'Nginx Full')
# облачный фаервол/Security Group — открой 80 и 443 в панели хостинга
```
Проверка снаружи (с другой машины): `curl -I http://killer.parar.ru` или whatsmydns/checkhost.

## 8. Автозапуск через systemd
```bash
sudo vim /etc/systemd/system/killer.service
```
```ini
[Unit]
Description=Killer/Paparazzi — web + Telegram/VK боты
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu                       # реальный пользователь (владелец /opt/killer/app)
WorkingDirectory=/opt/killer/app
ExecStart=/opt/killer/app/.venv/bin/python run_all.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now killer
sudo systemctl status killer
journalctl -u killer -f            # логи: увидишь «✅ … успешно подключён»
```
systemd по умолчанию гасит всю группу процессов при остановке — веб и оба бота
останавливаются/перезапускаются вместе.

## 9. Право на управление из веб-интерфейса (sudoers)
Кнопки «Перезагрузить / Выключить / Обновить» на странице «⚙️ Настройки игры» при
`START_MODE = "systemctl"` выполняют `systemctl restart|stop killer`. Чтобы это
работало без пароля, разреши сервисному пользователю только эти команды:
```bash
sudo visudo -f /etc/sudoers.d/killer
```
```
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart killer, /usr/bin/systemctl stop killer
```
(замени `ubuntu` на своего пользователя; путь к systemctl проверь через `which systemctl`).

Кнопка **«Обновить»** делает `git pull --ff-only` в `CODE_DIR`, затем перезапуск —
для этого у пользователя должен быть доступ к git-репозиторию (тот же, откуда клонировал).

---

## 10. Проверка
1. `https://killer.parar.ru` открывается, есть кнопки «Авторизоваться через Telegram/VK».
2. Напиши боту в VK «Начать» / в Telegram `/start` → приходит ссылка
   `https://killer.parar.ru/login?token=…` → переходишь → попадаешь в меню.
3. `journalctl -u killer -f` — видно подключение ботов и входящие сообщения.
4. **Ссылка root-доступа** печатается в логах при старте:
   ```bash
   journalctl -u killer | grep root
   ```
   Зайди по ней и выдай права администратора обычному пользователю
   (кнопка «👑 Выдать админа» в его карточке). Дальше играют через обычные аккаунты.

## 11. Управление и обновление
- Из веб-интерфейса: «⚙️ Настройки игры → 🔁 Управление приложением» — Перезагрузить,
  Обновить (git pull + рестарт), Выключить. Работает через systemd (см. шаги 8–9).
- Из консоли:
  ```bash
  sudo systemctl restart killer     # перезапуск
  sudo systemctl stop killer        # остановка
  cd /opt/killer/app && git pull && sudo systemctl restart killer   # обновление
  ```
- Поменял `config.py` → `sudo systemctl restart killer`.

---

## 12. Частые проблемы
- **502 Bad Gateway** → приложение не слушает `127.0.0.1:8000` (сервис не поднялся —
  `journalctl -u killer -n 50`).
- **VK-ссылки открываются как https, но не грузятся** → это ожидаемо: VK требует https,
  а `127.0.0.1` недоступен с телефона. Именно поэтому нужен домен + сертификат (шаги 1–7)
  и `APP_BASE_URL = "https://killer.parar.ru"`.
- **VK-бот молчит** → включи в сообществе Long Poll API и тип события «Входящее
  сообщение», а также «Сообщения сообщества» (см. [SETUP.md](SETUP.md), раздел 5).
- **Кнопки управления не срабатывают** → проверь `START_MODE`, имя `SYSTEMD_SERVICE`
  и правило sudoers (шаг 9); смотри `journalctl -u killer -f` в момент нажатия.
- **Права на `data/`** → пользователь сервиса должен иметь запись в рабочей папке
  (`sudo chown -R ubuntu:ubuntu /opt/killer/app`).
