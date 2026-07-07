"""Слой базы данных (SQLite) для веб-версии игры «Киллер / Папарацци».

Схема — по мотивам telegram-бота, но адаптирована под мультиплатформенность.

Ключевое отличие от бота и от прошлой версии этой схемы: всё, что относится к
**каналу связи** (идентификатор в платформе, username, имя, mute, доставка
ADMIN LOG), вынесено из таблицы `user` в отдельную таблицу `identity`. Один
профиль (`user`) может иметь несколько идентичностей — например Telegram и VK
одновременно.

  user      — профиль и игровое состояние (общее для человека)
  identity  — привязка к каналу связи: tg / vk  (0..N на пользователя)

Соответствие полей боту:
  tg_user_id (PK)   → user.id  +  identity(platform='tg', platform_uid=tg_id)
  tg_nickname       → identity.username
  tg_name           → identity.name
  bot_stopped       → identity.muted
  admin_log_enabled → identity.admin_log_enabled
"""
import os
import re
import sqlite3
from pathlib import Path

import config

BASE_DIR = Path(__file__).parent

# Движок БД: 'sqlite' (файл DB_NAME) или 'postgres' (config.POSTGRES_DSN).
# По умолчанию sqlite — старые config.py без этого ключа продолжают работать.
BACKEND = str(getattr(config, "DB_BACKEND", "sqlite")).strip().lower()
IS_PG = BACKEND in ("postgres", "postgresql", "pg")

# Активная «игра» = отдельный файл БД. По умолчанию — config.DB_NAME, но админ может
# выбрать другой файл в «⚙️ Настройки игры» (требует перезапуска). Выбор хранится в
# указателе active_db.txt рядом с БД — вне самой базы, чтобы можно было переключаться
# между играми. Указатель ищется в папке БД по умолчанию; в тестах config.DB_NAME
# переопределяют на абсолютный путь, поэтому указатель из рабочей папки их не задевает.
_DEFAULT_DB = BASE_DIR / config.DB_NAME
_POINTER = _DEFAULT_DB.parent / "active_db.txt"


def _safe_db_name(name: str) -> str:
    """Безопасное имя файла БД (только basename, расширение .db)."""
    n = Path((name or "").strip()).name
    if not n:
        return ""
    if not n.endswith(".db"):
        n += ".db"
    return n


def _resolve_db_path() -> Path:
    try:
        if _POINTER.exists():
            name = _safe_db_name(_POINTER.read_text(encoding="utf-8"))
            if name:
                return _DEFAULT_DB.parent / name
    except OSError:
        pass
    return _DEFAULT_DB


def list_db_files():
    """Имена файлов БД (*.db) в папке данных — список доступных «игр».

    Для postgres выбор «игры по файлу» не применяется (одна база) — возвращаем пусто.
    """
    if IS_PG:
        return []
    d = _DEFAULT_DB.parent
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.db"))


def active_db_name() -> str:
    return "postgres" if IS_PG else DB_PATH.name


def set_active_db(name: str) -> str:
    """Записать указатель активной БД (применяется после перезапуска). Вернуть имя."""
    if IS_PG:
        return active_db_name()   # postgres: одна база, переключать нечего
    n = _safe_db_name(name)
    if not n:
        return active_db_name()
    _DEFAULT_DB.parent.mkdir(exist_ok=True)
    _POINTER.write_text(n, encoding="utf-8")
    return n


DB_PATH = _resolve_db_path()
DATA_DIR = DB_PATH.parent


# --- диалектные различия sqlite / postgres ---------------------------------
# Прикладной код везде пишет SQL в стиле sqlite (плейсхолдеры «?», таблица `user`
# без кавычек, флаги-целые 0/1). Для postgres переводим это на лету здесь, чтобы
# модели (core/*) оставались общими и не знали о движке.

_USER_TBL_RE = re.compile(r"\buser\b")
_INSERT_RE = re.compile(r"^\s*insert\s+into\s+\"?(\w+)\"?", re.I)
# Таблицы с автоинкрементным столбцом id (для postgres — INSERT ... RETURNING id).
_ID_TABLES = ("user", "identity", "revive_queue")


def _tr(sql: str) -> str:
    """SQL под активный движок. Для postgres: ? → %s и user → "user" (reserved)."""
    if not IS_PG:
        return sql
    return _USER_TBL_RE.sub('"user"', sql).replace("?", "%s")


class _Row(dict):
    """Строка результата postgres: доступ и по имени столбца, и по индексу — как sqlite3.Row."""
    __slots__ = ("_seq",)

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._seq = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._seq[key]
        return dict.__getitem__(self, key)


def _pg_row_factory(cursor):
    """Фабрика строк psycopg → _Row (имя столбца берём из cursor.description при выборке)."""
    def make(values):
        cols = [d.name for d in (cursor.description or [])]
        return _Row(cols, values)
    return make


def get_connection():
    """Соединение с БД активного движка.

    sqlite — файл DB_PATH (FK + WAL). postgres — по config.POSTGRES_DSN; строки
    отдаются как _Row (доступ и по имени, и по индексу — совместимо с прикладным кодом).
    """
    if IS_PG:
        import psycopg
        return psycopg.connect(config.POSTGRES_DSN, row_factory=_pg_row_factory)
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
        return conn.execute(_tr(sql), params).fetchone()
    finally:
        conn.close()


def query_all(sql: str, params: tuple = ()):
    conn = get_connection()
    try:
        return conn.execute(_tr(sql), params).fetchall()
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE. Возвращает id вставленной строки (для INSERT в таблицы с id)."""
    conn = get_connection()
    try:
        if IS_PG:
            m = _INSERT_RE.match(sql)
            tbl = m.group(1).lower() if m else ""
            q = _tr(sql)
            if tbl in _ID_TABLES and "returning" not in sql.lower():
                row = conn.execute(q + " RETURNING id", params).fetchone()
                conn.commit()
                return row[0] if row else None
            conn.execute(q, params)
            conn.commit()
            return None
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# --- схема ------------------------------------------------------------------

def init_db():
    if IS_PG:
        _init_pg()
        return
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
    #   platform     : 'tg' | 'vk'
    #   platform_uid : id/username пользователя в платформе
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

    # Постоянная (переиспользуемая) ссылка входа — по одной на КАНАЛ (identity),
    # т.к. у пользователя может быть и Telegram, и VK, каждый со своей ссылкой.
    # Работает всегда, без срока; хранится только SHA-256-хеш токена; перевыпуск
    # заменяет строку (старая ссылка перестаёт работать).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS persistent_login (
        identity_id INTEGER PRIMARY KEY REFERENCES identity(id) ON DELETE CASCADE,
        token_hash  TEXT NOT NULL UNIQUE,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # token_plain — сырой токен, чтобы ссылку входа можно было ПОКАЗАТЬ повторно и
    # переиспользовать (кнопка «Открыть меню игры» ведёт в игру сразу с токеном, без
    # генерации новой ссылки). Хранение допустимо: игра для локальных мероприятий, без
    # персональных данных; отозвать ссылку можно в «Настройках профиля». (TODO 76.)
    _plcols = {r["name"] for r in cur.execute("PRAGMA table_info(persistent_login)")}
    if "token_plain" not in _plcols:
        cur.execute("ALTER TABLE persistent_login ADD COLUMN token_plain TEXT")

    # Редактируемые из UI настройки игры (ключ-значение): контакт поддержки, файл
    # правил, доп. вопросы, режим подтверждения поимок, id root-пользователя и т.п.
    # Раньше часть этого жила в config.py — теперь всё в БД (см. core/settings.py).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS setting (
        key   TEXT PRIMARY KEY,
        value TEXT
    );
    """)

    # Одноразовые коды привязки второго канала (tg↔vk) к одному пользователю.
    # Пользователь получает код на сайте и отправляет его боту другой платформы:
    # `/link <code>` переносит identity бота на этого пользователя (объединение).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS link_code (
        code       TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    _migrate_flat_to_identity(cur)
    _migrate_emulation_to_local(cur)

    if cur.execute("SELECT COUNT(*) FROM game").fetchone()[0] == 0:
        cur.execute("INSERT INTO game (id) VALUES (1)")

    conn.commit()
    conn.close()
    print("[db] База данных инициализирована:", DB_PATH)


# --- схема PostgreSQL -------------------------------------------------------
# Отличия от sqlite: id — SERIAL (вместо AUTOINCREMENT); флаги — INTEGER 0/1 (а не
# BOOLEAN), чтобы прикладной код с `= 1` и int(value) работал одинаково; таблица
# user в кавычках (зарезервированное слово). Миграции старых dev-схем не нужны —
# postgres-БД создаётся сразу в нужной форме.
_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS game (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    registration_open INTEGER NOT NULL DEFAULT 0,
    is_started        INTEGER NOT NULL DEFAULT 0,
    is_paused         INTEGER NOT NULL DEFAULT 0,
    password          TEXT
);
CREATE TABLE IF NOT EXISTS "user" (
    id           SERIAL PRIMARY KEY,
    real_name    TEXT NOT NULL DEFAULT '',
    extra_info   TEXT NOT NULL DEFAULT '',
    is_player    INTEGER NOT NULL DEFAULT 0,
    is_alive     INTEGER NOT NULL DEFAULT 0,
    kill_count   INTEGER NOT NULL DEFAULT 0,
    game_order   INTEGER UNIQUE,
    target       INTEGER REFERENCES "user"(id),
    killed_by    INTEGER REFERENCES "user"(id),
    is_admin     INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS identity (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    platform      TEXT NOT NULL,
    platform_uid  TEXT NOT NULL,
    username      TEXT,
    name          TEXT,
    muted             INTEGER NOT NULL DEFAULT 0,
    admin_log_enabled INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (platform, platform_uid)
);
CREATE INDEX IF NOT EXISTS idx_identity_user ON identity(user_id);
CREATE TABLE IF NOT EXISTS revive_queue (
    id       SERIAL PRIMARY KEY,
    user_id  INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS persistent_login (
    identity_id INTEGER PRIMARY KEY REFERENCES identity(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    token_plain TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS setting (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS link_code (
    code       TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _init_pg():
    """Создать схему в PostgreSQL (идемпотентно) и завести строку game(id=1)."""
    import psycopg
    conn = psycopg.connect(config.POSTGRES_DSN)
    try:
        with conn.cursor() as cur:
            for stmt in _PG_SCHEMA.split(";"):
                s = stmt.strip()
                if s:
                    cur.execute(s)
            cur.execute("SELECT COUNT(*) FROM game")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO game (id) VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    print("[db] База данных (PostgreSQL) инициализирована.")


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


def _migrate_emulation_to_local(cur):
    """Перевести старые эмуляционные каналы на платформу 'local'.

    Раньше эмуляция чата создавала identity с platform='tg' и platform_uid = username
    (нечисловой). Теперь эмуляция — самостоятельная платформа 'local', не связанная с
    настоящим Telegram (там platform_uid — числовой id). Переносим только нечисловые
    tg-uid (это и есть эмуляционные никнеймы); реальные (числовые) не трогаем.
    UPDATE OR IGNORE — на случай коллизии с уже существующим ('local', username).
    """
    cur.execute(
        "UPDATE OR IGNORE identity SET platform = 'local' "
        "WHERE platform = 'tg' AND platform_uid GLOB '*[^0-9]*'")
    if cur.rowcount:
        print(f"[db] миграция: {cur.rowcount} эмуляционных tg-каналов → 'local'")


def _clear_local_data_files():
    """Стереть локальные файлы, не зависящие от движка БД: чаты, журнал, фото-пруфы."""
    chats_dir = DATA_DIR / "chats"
    if chats_dir.exists():
        for f in chats_dir.glob("*.txt"):
            f.unlink()
    admin_log = DATA_DIR / "admin_log.txt"
    if admin_log.exists():
        admin_log.unlink()
    photos_dir = DATA_DIR / "photos"
    if photos_dir.exists():
        for f in photos_dir.glob("*"):
            if f.is_file():
                f.unlink()


def drop_db():
    """Полный сброс: чистая БД + удаление локальных файлов (чаты/журнал/фото)."""
    if IS_PG:
        import psycopg
        conn = psycopg.connect(config.POSTGRES_DSN)
        try:
            with conn.cursor() as cur:
                cur.execute('DROP TABLE IF EXISTS link_code, persistent_login, '
                            'revive_queue, identity, setting, "user", game CASCADE')
            conn.commit()
        finally:
            conn.close()
        _clear_local_data_files()
        init_db()
        print("[db] Таблицы PostgreSQL пересозданы.")
        return
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(DB_PATH) + suffix)
        if p.exists():
            p.unlink()
    _clear_local_data_files()
    print("[db] Файл базы данных удалён.")


# Автоинициализация при импорте (можно отключить переменной NO_INITDB).
if not os.getenv("NO_INITDB"):
    init_db()
