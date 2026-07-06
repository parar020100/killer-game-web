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
