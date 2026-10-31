# -*- coding: utf-8 -*-
"""Перевод консоли в UTF-8 и мелкие помощники для .bat-скриптов.

Почему не chcp в .bat: смена кодовой страницы внутри пакетного файла
ломает чтение самого файла cmd.exe — команды начинают рваться на
куски («.venv is not recognized», «exe is not recognized»). Поэтому
кодовую страницу ставим из Python вызовом Windows API: файл .bat
остаётся чистым ASCII, а русский текст выводится корректно.
"""
import ctypes
import sys


def set_console_utf8():
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    # Иначе Python на Windows отдаёт в консоль cp1251, и кириллица
    # превращается в вопросительные знаки или обрезается.
    #
    # line_buffering=True — не косметика. Без него вывод этого скрипта
    # копится в буфере, пока дочерние процессы (pip, автоконфигуратор)
    # пишут напрямую — и весь их вывод оказывается ВЫШЕ заголовка,
    # хотя шёл после него.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace",
                               line_buffering=True)
    except Exception:
        pass


def pause():
    """Аналог pause в .bat — чтобы окно не закрылось, не спросив.

    RECTOR_NO_PAUSE=1 отключает ожидание: установка с сервера или из
    скрипта не должна зависнуть на вопросе, на который никто не ответит.
    """
    import os
    if os.environ.get("RECTOR_NO_PAUSE") == "1":
        return
    try:
        input("\n  Нажмите Enter, чтобы закрыть окно...")
    except (EOFError, KeyboardInterrupt):
        pass