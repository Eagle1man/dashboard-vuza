# -*- coding: utf-8 -*-
"""
Установка одной командой: окружение, зависимости, модель, индексация.

Отдельный модуль, а не длинный install.bat, по двум причинам:
в .bat нельзя писать по-русски (см. backend/console.py — почему),
и вся диагностика ошибок должна быть в одном месте с логикой,
а не размазана по экранным сообщениям.
"""
import subprocess
import sys
from pathlib import Path

from . import console
from .console import set_console_utf8, pause

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"

MIN_PYTHON = (3, 10)


def line(char="=", n=64):
    return char * n


def step(n, total, title):
    print()
    print(f"[{n}/{total}] {title}")


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=str(ROOT), **kw)


def check_python():
    if sys.version_info < MIN_PYTHON:
        print(f"  Нужен Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} или новее, "
              f"а стоит {sys.version.split()[0]}.")
        return False
    print(f"  Python {sys.version.split()[0]} — подходит.")
    return True


def install_deps():
    print("  Ставлю зависимости, обычно 1–3 минуты...")
    r = run([sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS),
             "--quiet"])
    if r.returncode != 0:
        print()
        print("  ОШИБКА: зависимости не установились.")
        print("  Проверьте подключение к интернету и повторите установку.")
        print("  Если интернета на этом компьютере нет, перенесите папку")
        print("  с другого компьютера целиком — виртуальное окружение")
        print("  (.venv) при этом нужно создать заново.")
        return False
    print("  Зависимости установлены.")
    return True


def main():
    set_console_utf8()
    print()
    print(line())
    print("  Дашборд ректора — установка")
    print(line())

    step(1, 4, "Проверка Python")
    if not check_python():
        print()
        print("  Установите Python с https://www.python.org/downloads/")
        print("  При установке отметьте галочку Add Python to PATH.")
        pause()
        return 1

    step(2, 4, "Зависимости")
    if not install_deps():
        pause()
        return 1

    step(3, 4, "Выбор модели под ваш компьютер")
    r = run([sys.executable, "-m", "backend.autoconfig"])
    if r.returncode != 0:
        print("  Не удалось определить железо, ставлю безопасные значения:")
        print("  языковая модель qwen2.5:1.5b, эмбеддинги nomic-embed-text")

    step(4, 4, "Индексация документов из папки knowledge")
    r = run([sys.executable, "-m", "backend.ingest"])
    if r.returncode != 0:
        print()
        print("  ВНИМАНИЕ: часть документов не проиндексирована,")
        print("  подробности выше. Система всё равно запустится.")

    print()
    print(line())
    print("  Установка завершена.")
    print()
    if sys.platform == "win32":
        print("  Запуск                    start.bat")
        print("  Резервная копия базы     backup.bat")
    else:
        print("  Запуск                    ./start.sh")
        print("  Служба (автозапуск)      deploy/systemd/README.md")
    print("  Проверка готовности      python -m backend.smoke")
    print("  Пароли подразделений     python -m backend.users list")
    print("  Новые документы          загрузка на странице /upload.html")
    print("                            либо файлы в папке knowledge")
    print(line())
    pause()
    return 0


if __name__ == "__main__":
    sys.exit(main())