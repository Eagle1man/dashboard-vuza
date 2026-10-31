# -*- coding: utf-8 -*-
"""Переключение моделей ролей без правки кода и без установщика.

Зачем отдельная команда. install.bat подбирает модель по железу один
раз, при установке. Когда машина меняется — переехали на ВМ с 24 ГБ
или докупили видеокарту — выбор надо пересмотреть, не запуская
установку заново: она заново соберёт индекс и перезапишет настройки.

Приоритет источников остаётся прежним (см. llm.resolve_role):
переменная окружения → запись в базе → кандидаты config. Эта команда
меняет именно запись в базе. Переменная окружения по-прежнему сильнее,
и в отчёте это видно — иначе человек искал бы, почему модель «не
переключилась».

    python -m backend.models                  состояние ролей
    python -m backend.models list             какие модели скачаны
    python -m backend.models small vikhr-7b:latest
    python -m backend.models large qwen2.5:14b
    python -m backend.models auto small       вернуть автовыбор

Смена языковой модели не требует ни перезапуска сервера, ни пересчёта
индекса: llm читает meta на каждом вызове, а индекс зависит от модели
эмбеддингов, а не от языковой.
"""
import os
import sys

from . import config
from . import db
from . import embed
from . import llm
from .console import set_console_utf8

ROLE_KEY = {"small": "llm_model", "large": "llm_model_large"}

RESOLVED_TEXT = {
    "env": "переменная окружения",
    "meta": "база (meta)",
    "auto": "автовыбор по config",
}


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


def show() -> int:
    names = embed.installed_models()
    if names:
        print(f"Ollama отвечает: {config.OLLAMA_URL}, моделей {len(names)}")
    else:
        print(f"Ollama не отвечает: {config.OLLAMA_URL}")
        print("  Список скачанных моделей неизвестен — проверка «скачана ли» "
              "ничего не значит.")

    for role in ("small", "large"):
        st = llm.role_status(role)
        env_name = llm.ROLE_ENV[role]
        env_value = os.environ.get(env_name, "")
        print()
        print(f"  роль {role}: {st['model']}")
        print(f"    скачана:  {'да' if st['installed'] else 'НЕТ'}")
        print(f"    источник: {RESOLVED_TEXT.get(st['resolved'], st['resolved'])}"
              f" ({ROLE_KEY[role]})")
        if env_value:
            print(f"    ВНИМАНИЕ: {env_name}={env_value} — переменная окружения "
                  f"перекрывает базу")
    print()
    print("Сменить:  python -m backend.models small <модель>")
    print("Список:   python -m backend.models list")
    return 0


def list_models() -> int:
    names = embed.installed_models()
    if not names:
        print(f"Ollama не отвечает ({config.OLLAMA_URL}) — список пуст.")
        return 2
    for name in sorted(names):
        print("  " + name)
    return 0


def set_role(role: str, model: str, force: bool = False) -> int:
    if role not in ROLE_KEY:
        print(f"Неизвестная роль: {role}. Есть: small, large.")
        return 2
    if not model:
        print("Не указана модель. Пример: python -m backend.models small "
              "qwen2.5:1.5b")
        return 2

    names = embed.installed_models()
    if not names:
        print(f"Ollama не отвечает ({config.OLLAMA_URL}).")
        print("  Проверить, скачана ли модель, нельзя. Сначала запустите "
              "Ollama, потом повторите.")
        if not force:
            return 2
        print("  --force: записываю без проверки.")
    elif not llm.model_available(model, names):
        print(f"Модели «{model}» нет среди скачанных.")
        print(f"  Скачать:  ollama pull {model}")
        print("  Похожие из скачанных:")
        base = model.split(":")[0]
        for name in names:
            if name.split(":")[0] == base:
                print("    " + name)
        if not force:
            print("  Если модель точно нужна — повторите с --force.")
            return 2
        print("  --force: записываю без проверки.")

    conn = _conn()
    db.set_meta(conn, ROLE_KEY[role], model)
    conn.close()

    print(f"Роль {role}: записано «{model}» ({ROLE_KEY[role]}).")
    print("  Сервер подхватит при следующем вопросе — перезапуск не нужен.")
    print("  Пересчитывать индекс не нужно: он зависит от модели эмбеддингов.")

    env_name = llm.ROLE_ENV[role]
    env_value = os.environ.get(env_name, "")
    if env_value:
        print(f"  ВНИМАНИЕ: задана {env_name}={env_value}. Она сильнее базы, "
              f"и работать будет она.")

    print(f"  Таймаут ответа сейчас {config.OLLAMA_TIMEOUT:.0f} с "
          f"(RECTOR_OLLAMA_TIMEOUT).")
    print("  Модели 7B и больше на процессоре отвечают минутами: если "
          "появляется «ошибка обращения к Ollama: timed out» — поднимите "
          "таймаут, например 900.")
    return 0


def auto_role(role: str) -> int:
    if role not in ROLE_KEY:
        print(f"Неизвестная роль: {role}. Есть: small, large.")
        return 2
    conn = _conn()
    conn.execute("DELETE FROM meta WHERE key = ?", (ROLE_KEY[role],))
    conn.commit()
    conn.close()
    st = llm.role_status(role)
    print(f"Роль {role}: выбор из базы убран, вернулся автовыбор.")
    print(f"  Сейчас получилась: {st['model']}")
    return 0


def main(argv=None) -> int:
    set_console_utf8()
    argv = list(sys.argv[1:] if argv is None else argv)
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]

    if not argv or argv[0] in ("show", "status"):
        return show()
    if argv[0] in ("list", "ls"):
        return list_models()
    if argv[0] == "auto":
        if len(argv) < 2:
            print("Укажите роль: python -m backend.models auto small")
            return 2
        return auto_role(argv[1])
    if argv[0] in ROLE_KEY:
        if len(argv) < 2:
            print(f"Укажите модель: python -m backend.models {argv[0]} <модель>")
            return 2
        return set_role(argv[0], argv[1], force=force)

    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
