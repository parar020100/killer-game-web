"""Управление процессом приложения: перезагрузка / остановка / обновление.

Веб-процесс (страница «Настройки игры») не может сам корректно перезапустить всё
дерево процессов, поэтому он лишь **кладёт команду** в файл `data/control.cmd`, а
исполняет её супервизор `run_all.py` (он запускает веб + ботов) с учётом
`config.START_MODE`:

  • manual    — перезапуск/остановка самим run_all (re-exec / выход);
  • systemctl — через `systemctl restart|stop <SYSTEMD_SERVICE>`.

Команда «update» перед перезапуском делает `git pull` в папке кода (CODE_DIR или
папка проекта).
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_CONTROL_FILE = BASE_DIR / "data" / "control.cmd"

VALID = {"restart", "stop", "update"}


def request(command: str) -> bool:
    """Положить команду управления для run_all. True, если команда допустима."""
    command = (command or "").strip().lower()
    if command not in VALID:
        return False
    _CONTROL_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONTROL_FILE.write_text(command, encoding="utf-8")
    return True


def take() -> str | None:
    """Прочитать и удалить команду (для run_all). None, если команды нет."""
    try:
        cmd = _CONTROL_FILE.read_text(encoding="utf-8").strip().lower()
    except OSError:
        return None
    try:
        _CONTROL_FILE.unlink()
    except OSError:
        pass
    return cmd if cmd in VALID else None
