"""Слой базы данных (SQLite) для веб-версии игры «Киллер / Папарацци».

Схема — по мотивам telegram-бота, но адаптирована под мультиплатформенность.

Ключевое отличие от бота и от прошлой версии этой схемы: всё, что относится к
**каналу связи** (идентификатор в платформе, username, имя, mute, доставка
ADMIN LOG), вынесено из таблицы `user` в отдельную таблицу `identity`. Один
профиль (`user`) может иметь несколько идентичностей — например Telegram и VK
одновременно, — а также специальную тестовую идентичность (platform='test'),
которая не требует реального аккаунта: её «входящие» пишутся в текстовый файл
(см. core/inbox.py) и показываются на отдельной веб-странице.

  user      — профиль и игровое состояние (общее для человека)
  identity  — привязка к каналу связи: tg / vk / test  (0..N на пользователя)

Соответствие полей боту:
  tg_user_id (PK)   → user.id  +  identity(platform='tg', platform_uid=tg_id)
  tg_nickname       → identity.username
  tg_name           → identity.name
  bot_stopped       → identity.muted
  admin_log_enabled → identity.admin_log_enabled
"""
import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "killer_game.db"


def get_connection() -> sqlite3.Connection:
    """Соединение с включёнными FK и WAL (как договорено в CONCEPT.md)."""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# --- маленькие помощники, чтобы не плодить boilerplate в моделях ------------

def query_one(sql: str, params: tuple = ()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def query_all(sql: str, params: tuple = ()):
    conn = get_connection()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE. Возвращает lastrowid (полезно для INSERT)."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# --- схема ------------------------------------------------------------------

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # Единственная строка (id=1) с глобальным состоянием игры.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS game (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        registration_open BOOLEAN NOT NULL DEFAULT 0,
        is_started        BOOLEAN NOT NULL DEFAULT 0,
        is_paused         BOOLEAN NOT NULL DEFAULT 0,
        password          TEXT
    );
    """)

    # Пользователи. id — сквозной, с 1 (AUTOINCREMENT).
    # Только профиль и игровое состояние — никаких полей канала связи.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        real_name    TEXT NOT NULL DEFAULT '',   -- имя в игре (вводит игрок)
        extra_info   TEXT NOT NULL DEFAULT '',   -- ответ на доп. вопрос регистрации

        is_player    BOOLEAN NOT NULL DEFAULT 0,
        is_alive     BOOLEAN NOT NULL DEFAULT 0,
        kill_count   INTEGER NOT NULL DEFAULT 0,
        game_order   INTEGER UNIQUE,             -- позиция в круге целей
        target       INTEGER REFERENCES user(id),
        killed_by    INTEGER REFERENCES user(id),

        is_admin     BOOLEAN NOT NULL DEFAULT 0,

        created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Идентичности — привязки профиля к каналам связи.
    #   platform     : 'tg' | 'vk' | 'test'
    #   platform_uid : id пользователя в платформе (для test — произвольная метка)
    #   muted             : не слать игровые уведомления в этот канал
    #   admin_log_enabled : слать ADMIN LOG в этот канал (если пользователь админ)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS identity (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        platform      TEXT NOT NULL,
        platform_uid  TEXT NOT NULL,
        username      TEXT,
        name          TEXT,
        muted             BOOLEAN NOT NULL DEFAULT 0,
        admin_log_enabled BOOLEAN NOT NULL DEFAULT 1,
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (platform, platform_uid)
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_identity_user ON identity(user_id)")

    # Очередь на воскрешение (подаренные жизни).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS revive_queue (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id  INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Одноразовые токены для входа по «магической ссылке» из бота.
    # (таблица готова заранее; сам вход подключим на шаге авторизации)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS login_token (
        token      TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP NOT NULL,
        used_at    TIMESTAMP
    );
    """)

    _migrate_flat_to_identity(cur)

    if cur.execute("SELECT COUNT(*) FROM game").fetchone()[0] == 0:
        cur.execute("INSERT INTO game (id) VALUES (1)")

    conn.commit()
    conn.close()
    print("[db] База данных инициализирована:", DB_PATH)


def _migrate_flat_to_identity(cur):
    """Разовый перенос старых плоских колонок user.tg_*/vk_* в таблицу identity.

    Нужен только для dev-БД, созданных прошлой версией схемы. Сами колонки не
    удаляем (SQLite это делает лишь пересозданием таблицы) — они просто перестают
    использоваться. INSERT OR IGNORE не создаёт дублей благодаря UNIQUE.
    """
    cols = {row["name"] for row in cur.execute("PRAGMA table_info(user)")}

    def col(name, default):
        return name if name in cols else default

    for platform, id_col in (("tg", "tg_id"), ("vk", "vk_id")):
        if id_col not in cols:
            continue
        uname = col(f"{platform}_username", "NULL")
        name = col(f"{platform}_name", col("display_name", "NULL"))
        muted = col(f"{platform}_muted", "0")
        log = col(f"{platform}_admin_log_enabled", "1")
        cur.execute(
            f"INSERT OR IGNORE INTO identity "
            f"(user_id, platform, platform_uid, username, name, muted, admin_log_enabled) "
            f"SELECT id, '{platform}', {id_col}, {uname}, {name}, {muted}, {log} "
            f"FROM user WHERE {id_col} IS NOT NULL"
        )
        if cur.rowcount:
            print(f"[db] миграция: перенесено {cur.rowcount} '{platform}'-идентичностей")


def drop_db():
    """Полный сброс: удаляет файл БД (+ WAL/SHM) и тестовые «входящие»."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    inbox_dir = DATA_DIR / "inboxes"
    if inbox_dir.exists():
        for f in inbox_dir.glob("*.txt"):
            f.unlink()
    print("[db] Файл базы данных удалён.")


# Автоинициализация при импорте (можно отключить переменной NO_INITDB).
if not os.getenv("NO_INITDB"):
    init_db()
