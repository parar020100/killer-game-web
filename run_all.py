"""Запуск всего сразу в одном окне: веб-сервер + Telegram-бот + VK-бот.

Каждый процесс печатает со своим префиксом: [web] / [tg] / [vk]. Боты стартуют
только если заданы их токены в config.py (иначе пропускаются — останется эмуляция
чата). Ctrl+C останавливает всё.

Запуск:
    .venv/Scripts/python.exe run_all.py     # Windows
    .venv/bin/python run_all.py             # Linux/macOS
    ./start.sh                              # то же самое (сам создаст venv/зависимости)

Порт/хост — переменными окружения HOST и PORT (по умолчанию 127.0.0.1:8000).
Для веб-разработки с автоперезагрузкой запускайте веб отдельно:
    python -m uvicorn app:app --reload
"""
import os
import sys
import signal
import subprocess
import threading
import time

import config
from core import control, bot_log

PY = sys.executable
HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.getenv("HOST", "127.0.0.1")
PORT = os.getenv("PORT", "8000")

_procs = []


def _log(msg):
    """Событие супервизора: и в консоль (с префиксом [run]), и в лог бота."""
    print(f"[run] {msg}")
    sys.stdout.flush()
    bot_log.log(msg, "run")


def _pump(name, proc, to_botlog):
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()
        # stdout/stderr ботов целиком уводим в лог бота (веб — только его lifecycle).
        if to_botlog:
            bot_log.log(line.rstrip("\n"), name)


def _start(name, args, to_botlog=False):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env)
    _procs.append((name, proc))
    threading.Thread(target=_pump, args=(name, proc, to_botlog), daemon=True).start()
    _log(f"процесс [{name}] запущен (pid {proc.pid})")


def _tg_on():
    return (bool(getattr(config, "ENABLE_TG_BOT", True))
            and bool((getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip()))


def _vk_on():
    return (bool(getattr(config, "ENABLE_VK_BOT", True))
            and bool((getattr(config, "VK_GROUP_TOKEN", "") or "").strip()))


def main():
    control.take()   # сбросить возможную «залежавшуюся» команду управления
    _log(f"супервизор run_all запущен (режим: {getattr(config, 'START_MODE', 'manual')})")

    # web — не бот: в лог бота уводим только его старт/стоп, не весь поток uvicorn.
    _start("web", [PY, "-m", "uvicorn", "app:app", "--host", HOST, "--port", PORT])

    if _tg_on():
        _start("tg", [PY, "tg_bot.py"], to_botlog=True)
    else:
        _log("Telegram-бот выключен/без токена — пропущен (эмуляция чата).")

    if _vk_on():
        _start("vk", [PY, "vk_bot.py"], to_botlog=True)
    else:
        _log("VK-бот выключен/без токена — пропущен (эмуляция чата).")

    _log(f"Запущено: веб http://{HOST}:{PORT} + боты. Ctrl+C — остановить всё.")
    try:
        while True:
            cmd = control.take()
            if cmd:
                _log(f"команда управления: {cmd}")
                _handle_control(cmd)
            for name, proc in list(_procs):
                if proc.poll() is not None:
                    _log(f"⚠️  процесс [{name}] завершился (код {proc.returncode}).")
                    _procs.remove((name, proc))
            if not _procs:
                _log("Все процессы завершились.")
                return
            time.sleep(1)
    except KeyboardInterrupt:
        _log("Останавливаю все процессы…")
    finally:
        _shutdown()


def _shutdown():
    """Мягко остановить все дочерние процессы, затем добить не откликнувшиеся."""
    for name, proc in _procs:
        if proc.poll() is None:
            _log(f"останавливаю процесс [{name}] (pid {proc.pid})…")
            proc.terminate()
    deadline = time.time() + 6
    for name, proc in _procs:
        try:
            proc.wait(timeout=max(0.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            _log(f"[{name}] не остановился — снимаю принудительно.")
            proc.kill()
    _log("Остановлено.")


def _code_dir():
    d = (getattr(config, "CODE_DIR", "") or "").strip()
    return d or HERE


def _git_pull() -> bool:
    d = _code_dir()
    _log(f"git pull в {d} …")
    try:
        r = subprocess.run(["git", "-C", d, "pull", "--ff-only"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"git pull не выполнен: {exc}")
        return False
    if r.stdout:
        _log(r.stdout.strip())
    if r.returncode != 0:
        _log("git pull ошибка: " + (r.stderr or "").strip())
    return r.returncode == 0


def _systemctl(action: str):
    svc = getattr(config, "SYSTEMD_SERVICE", "killer")
    _log(f"sudo systemctl {action} {svc} …")
    subprocess.Popen(["sudo", "systemctl", action, svc])


def _do_restart():
    if getattr(config, "START_MODE", "manual") == "systemctl":
        _systemctl("restart")     # systemd перезапустит unit (нас снимет SIGTERM)
    else:
        _shutdown()
        _log("перезапуск…")
        os.execv(PY, [PY, os.path.join(HERE, "run_all.py")])


def _do_stop():
    if getattr(config, "START_MODE", "manual") == "systemctl":
        _systemctl("stop")        # systemd остановит unit
    else:
        _shutdown()
        os._exit(0)


def _handle_control(cmd: str):
    if cmd == "update":
        _git_pull()
        _do_restart()
    elif cmd == "restart":
        _do_restart()
    elif cmd == "stop":
        _do_stop()


def _on_term(signum, frame):
    # SIGTERM (kill / systemd stop) → выходим через ту же ветку, что и Ctrl+C.
    raise KeyboardInterrupt


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, AttributeError, OSError):
        pass  # SIGTERM может быть недоступен (напр. не в главном потоке / Windows)
    main()
