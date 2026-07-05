"""Слой базы данных (SQLite) для веб-версии игры «Киллер / Папарацци».

Схема — по мотивам telegram-бота, но адаптирована под мультиплатформенность:
  * у пользователя теперь сквозной **id** (INTEGER PK, назначается по порядку с 1),
    а идентификаторы платформ вынесены в отдельные поля **tg_id** и **vk_id**;
  * ссылки target / killed_by / очередь воскрешения указывают на user(id),
    а не на tg-идентификатор, как было в боте.

Соответствие полей боту (что переименовано):
  tg_user_id (PK)  → id (PK) + tg_id / vk_id
  tg_nickname      → tg_username / vk_username         (@username / screen_name)
  tg_name          → tg_name / vk_name                 (имя из платформы, у каждой своё)
  bot_stopped      → tg_muted / vk_muted               (не слать игровые уведомления)
  admin_log_enabled→ tg_admin_log_enabled / vk_admin_log_enabled  (доставка ADMIN LOG)

Всё, что связано с каналом (идентификатор, имя, доставка уведомлений), разделено по
платформам, т.к. один профиль может быть привязан и к TG, и к VK. Игровые поля (счёт,
цель, роль и т.п.) — общие для профиля и повторяют бот 1:1.
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
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id        INTEGER UNIQUE,          -- Telegram user id (может быть NULL)
        vk_id        INTEGER UNIQUE,          -- VK user id (может быть NULL)
        tg_username  TEXT,                    -- @username в TG (для показа/поиска)
        vk_username  TEXT,                    -- screen_name во VK (при наличии)
        tg_name      TEXT,                    -- имя из Telegram
        vk_name      TEXT,                    -- имя из VK
        real_name    TEXT NOT NULL DEFAULT '',-- имя в игре (вводит игрок)
        extra_info   TEXT NOT NULL DEFAULT '',-- ответ на доп. вопрос регистрации

        is_player    BOOLEAN NOT NULL DEFAULT 0,
        is_alive     BOOLEAN NOT NULL DEFAULT 0,
        kill_count   INTEGER NOT NULL DEFAULT 0,
        game_order   INTEGER UNIQUE,          -- позиция в круге целей
        target       INTEGER REFERENCES user(id),
        killed_by    INTEGER REFERENCES user(id),

        is_admin     BOOLEAN NOT NULL DEFAULT 0,

        -- доставка уведомлений — отдельно по платформам
        tg_muted             BOOLEAN NOT NULL DEFAULT 0,  -- не слать игровые уведомления в TG
        vk_muted             BOOLEAN NOT NULL DEFAULT 0,  -- не слать игровые уведомления во VK
        tg_admin_log_enabled BOOLEAN NOT NULL DEFAULT 1,  -- слать ADMIN LOG в TG
        vk_admin_log_enabled BOOLEAN NOT NULL DEFAULT 1,  -- слать ADMIN LOG во VK

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

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

    # Мягкая миграция: добавить недостающие колонки в уже существующую БД
    # (CREATE TABLE IF NOT EXISTS не меняет схему существующей таблицы).
    _ensure_columns(cur, "user", {
        "vk_username": "TEXT",
        "tg_name": "TEXT",
        "vk_name": "TEXT",
        "tg_muted": "BOOLEAN NOT NULL DEFAULT 0",
        "vk_muted": "BOOLEAN NOT NULL DEFAULT 0",
        "tg_admin_log_enabled": "BOOLEAN NOT NULL DEFAULT 1",
        "vk_admin_log_enabled": "BOOLEAN NOT NULL DEFAULT 1",
    })
    # бэкфилл имени из старой единой колонки display_name, если она была
    cols = {row["name"] for row in cur.execute("PRAGMA table_info(user)")}
    if "display_name" in cols:
        cur.execute("UPDATE user SET tg_name = display_name "
                    "WHERE tg_name IS NULL AND tg_id IS NOT NULL")
        cur.execute("UPDATE user SET vk_name = display_name "
                    "WHERE vk_name IS NULL AND vk_id IS NOT NULL AND tg_id IS NULL")

    if cur.execute("SELECT COUNT(*) FROM game").fetchone()[0] == 0:
        cur.execute("INSERT INTO game (id) VALUES (1)")

    conn.commit()
    conn.close()
    print("[db] База данных инициализирована:", DB_PATH)


def _ensure_columns(cur, table: str, columns: dict):
    """Добавляет отсутствующие колонки в таблицу (простая миграция)."""
    existing = {row["name"] for row in cur.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            print(f"[db] миграция: добавлена колонка {table}.{name}")


def drop_db():
    """Полный сброс: удаляет файл БД (и WAL/SHM-спутники)."""
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    print("[db] Файл базы данных удалён.")


# Автоинициализация при импорте (можно отключить переменной NO_INITDB).
if not os.getenv("NO_INITDB"):
    init_db()
