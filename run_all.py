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

PY = sys.executable
HOST = os.getenv("HOST", "127.0.0.1")
PORT = os.getenv("PORT", "8000")

_procs = []


def _pump(name, proc):
    for line in iter(proc.stdout.readline, ""):
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def _start(name, args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env=env)
    _procs.append((name, proc))
    threading.Thread(target=_pump, args=(name, proc), daemon=True).start()


def main():
    _start("web", [PY, "-m", "uvicorn", "app:app", "--host", HOST, "--port", PORT])

    if (getattr(config, "TELEGRAM_BOT_TOKEN", "") or "").strip():
        _start("tg", [PY, "tg_bot.py"])
    else:
        print("[run] TELEGRAM_BOT_TOKEN пуст — Telegram-бот пропущен (эмуляция чата).")

    if (getattr(config, "VK_GROUP_TOKEN", "") or "").strip():
        _start("vk", [PY, "vk_bot.py"])
    else:
        print("[run] VK_GROUP_TOKEN пуст — VK-бот пропущен (эмуляция чата).")

    print(f"[run] Запущено: веб http://{HOST}:{PORT} + боты. Ctrl+C — остановить всё.")
    try:
        while True:
            for name, proc in list(_procs):
                if proc.poll() is not None:
                    print(f"[run] ⚠️  процесс [{name}] завершился (код {proc.returncode}).")
                    _procs.remove((name, proc))
            if not _procs:
                print("[run] Все процессы завершились.")
                return
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[run] Останавливаю все процессы…")
    finally:
        _shutdown()


def _shutdown():
    """Мягко остановить все дочерние процессы, затем добить не откликнувшиеся."""
    for _name, proc in _procs:
        if proc.poll() is None:
            proc.terminate()
    deadline = time.time() + 6
    for name, proc in _procs:
        try:
            proc.wait(timeout=max(0.0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            print(f"[run] [{name}] не остановился — снимаю принудительно.")
            proc.kill()
    print("[run] Остановлено.")


def _on_term(signum, frame):
    # SIGTERM (kill / systemd stop) → выходим через ту же ветку, что и Ctrl+C.
    raise KeyboardInterrupt


if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except (ValueError, AttributeError, OSError):
        pass  # SIGTERM может быть недоступен (напр. не в главном потоке / Windows)
    main()
