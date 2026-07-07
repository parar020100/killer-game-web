"""Версия сборки: короткий SHA текущего git-коммита, кэшируется при старте.

Используется для подписи внизу страниц («Сборка: <первые 10 символов SHA>»).
SHA читается один раз при импорте (`git rev-parse HEAD`) и дальше берётся из
кэша — на каждый запрос git не дёргаем. Если это не git-репозиторий (например
код выгружен архивом), подпись сборки просто не показывается.
"""
import subprocess
from pathlib import Path

# Кем разработано приложение (подпись внизу страницы).
DEVELOPED_BY = "parar020100"

_BASE_DIR = Path(__file__).resolve().parent.parent


def _read_build_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_BASE_DIR), capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0:
            return out.stdout.strip()[:10]
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


# Кэш: вычисляется один раз при импорте модуля (при старте приложения).
BUILD_SHA = _read_build_sha()

# Файл, где запоминаем SHA предыдущего запуска — чтобы после обновления (git pull +
# перезапуск) заметить смену сборки и один раз записать её в ADMIN LOG (TODO 82).
_LAST_BUILD_FILE = _BASE_DIR / "data" / "last_build.sha"


def detect_build_change() -> tuple[str, str] | None:
    """Сравнить текущий SHA сборки с сохранённым при прошлом запуске.

    Возвращает `(old_sha, new_sha)`, если сборка изменилась (был сохранён другой
    непустой SHA), иначе None. Всегда обновляет файл текущим SHA. Если SHA прочитать
    не удалось (не git-репозиторий) — ничего не делаем."""
    if not BUILD_SHA:
        return None
    old = ""
    try:
        old = _LAST_BUILD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if old != BUILD_SHA:
        try:
            _LAST_BUILD_FILE.parent.mkdir(parents=True, exist_ok=True)
            _LAST_BUILD_FILE.write_text(BUILD_SHA, encoding="utf-8")
        except OSError:
            pass
    if old and old != BUILD_SHA:
        return old, BUILD_SHA
    return None
