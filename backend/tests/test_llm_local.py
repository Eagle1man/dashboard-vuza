# -*- coding: utf-8 -*-
"""
Проверка локальной языковой модели.

Зачем. Модель на компьютере ректора может быть не скачана, может быть
выключена, может отвечать мусором. Все три случая должны быть видны
и не приводить к тихой неправде: если модель недоступна, система должна
честно сказать «сводный ответ недоступен», а не изобразить работу.

Тест работает и без модели: он проверяет, что при выключенной Ollama
система деградирует честно, а не падает. Если модель доступна —
дополнительно проверяется, что она отвечает по-русски.

Запуск: python -m backend.tests.test_llm_local
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from backend import autoconfig, crew, db, embed, llm  # noqa: E402

FAILURES = []
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def check(name, condition, detail=""):
    if condition:
        print(f"  ОК     {name}")
    else:
        print(f"  ПРОВАЛ {name}  {detail}")
        FAILURES.append(name)


def test_model_configured():
    """Выбранная модель должна быть установлена, иначе ИИ молчит."""
    conn = db.connect()
    llm_m, emb_m, profile, _hw = autoconfig.load_models()
    conn.close()
    installed = embed.installed_models()
    print(f"  модель из базы: {llm_m} | эмбеддинги: {emb_m} | профиль: {profile}")
    print(f"  установлено в Ollama: {', '.join(installed) or 'ничего'}")
    check("выбрана языковая модель", bool(llm_m), "в базе пусто")
    check("выбрана модель эмбеддингов", bool(emb_m), "в базе пусто")
    if llm_m and installed:
        check("выбранная модель установлена", llm.model_installed(llm_m, installed),
              f"{llm_m} не найдена среди {installed}")
    if emb_m and installed:
        check("выбранная модель эмбеддингов установлена",
              llm.model_installed(emb_m, installed),
              f"{emb_m} не найдена среди {installed}")


def test_offline_is_honest():
    """Без модели система говорит об отказе, а не притворяется."""
    if embed.ollama_available():
        print("  ОК     Ollama отвечает — проверка отказа в офлайне не выполняется")
        return
    text, meta = llm.chat("система", "вопрос", num_predict=20)
    check("без модели ответ пуст", not text, f"получено {len(text)} символов")
    check("без модели указана причина", bool(meta.get("reason")),
          f"reason={meta.get('reason')!r}")
    check("без модели помечено как сбой", meta.get("ok") is False)


def test_model_answers():
    """Модель отвечает по-русски и укладывается в разумное время."""
    if not embed.ollama_available():
        print("  ОК     Ollama выключена — проверка ответа не выполняется")
        return
    started = time.time()
    text, meta = llm.chat(
        "Ты — аналитик. Отвечай по-русски, одним предложением.",
        "Назови столицу Франции.",
        temperature=0.0, num_predict=40)
    dt = time.time() - started
    check("модель ответила", meta.get("ok") and bool(text.strip()),
          f"meta={meta} ответ={text[:80]!r}")
    if text:
        letters = [c for c in text if c.isalpha()]
        share = sum(1 for c in letters if CYRILLIC.match(c)) / max(len(letters), 1)
        check("ответ по-русски", share > 0.5, f"доля кириллицы {share:.0%}")
    check("ответ не слишком долгий", dt < 120, f"{dt:.0f} с")


def test_crew_degrades():
    """Экипаж при недоступной модели честно помечает сбой."""
    conn = db.connect()
    db.init_db(conn)
    out = crew.run("сколько обучающихся в университете", conn, mode="auto")
    check("экипаж ответил структурой", bool(out.get("answer")),
          "ответ пуст")
    if not embed.ollama_available():
        check("без модели помечено, что это сбой", out.get("degraded") is True,
              f"degraded={out.get('degraded')}")
    conn.close()


def main():
    print("Проверка локальной модели")
    test_model_configured()
    test_offline_is_honest()
    test_model_answers()
    test_crew_degrades()
    if FAILURES:
        print(f"\nПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1
    print("\nВсе проверки пройдены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
