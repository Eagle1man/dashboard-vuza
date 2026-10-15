# -*- coding: utf-8 -*-
"""
Проверка готовности модели — вызывается из start.bat.

Нужен, чтобы при запуске человек сразу увидел не «всё хорошо»,
а конкретное состояние: какая модель выбрана, скачана ли она.
Формат вывода — одна строка на строку, чтобы .bat мог их напечатать.
"""
import sys

from . import autoconfig
from . import db
from . import embed
from . import llm


def main():
    llm_model, emb_model, profile, hw = autoconfig.load_models()
    if not llm_model:
        print("Модель не выбрана. Запустите install.bat ещё раз.")
        return 1

    if not embed.ollama_available():
        print("Ollama не запущена: ИИ будет показывать документы без пересказа.")
        return 0

    installed = embed.installed_models()

    def present(name):
        base = name.split(":")[0]
        return any(n == name or n.split(":")[0] == base for n in installed)

    if not present(llm_model):
        print(f"Нужно скачать языковую модель. Выполните:  ollama pull {llm_model}")
    else:
        print(f"Языковая модель готова: {llm_model}")

    if not present(emb_model):
        print(f"Нужно скачать модель эмбеддингов. Выполните:  ollama pull {emb_model}")
    else:
        print(f"Модель эмбеддингов готова: {emb_model}")

    try:
        conn = db.connect()
        backends = {r[0] for r in conn.execute(
            "SELECT DISTINCT backend FROM chunk_vec").fetchall()}
        conn.close()
    except Exception:
        backends = set()

    if backends and backends <= {"hash-fallback"}:
        print("ВНИМАНИЕ: индекс построен в аварийном режиме, поиск по смыслу")
        print("          отключён. После скачивания модели выполните:")
        print("          .venv\\Scripts\\python.exe -m backend.ingest --force")
    return 0


if __name__ == "__main__":
    sys.exit(main())