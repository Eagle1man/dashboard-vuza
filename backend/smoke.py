# -*- coding: utf-8 -*-
"""Проверка готовности: одна команда отвечает «можно пускать людей или нет».

Зачем. После установки легко получить состояние «сервер отвечает, а
ассистент молчит»: Ollama не запущена, модель не скачана, индекс пуст
или собран аварийной заглушкой. Каждая причина проверяется отдельно,
потому что лечатся они по-разному, а «что-то не работает» — не ответ.

    python -m backend.smoke                 проверки без обращения к модели
    python -m backend.smoke --ask "..."     плюс живой вопрос к ассистенту
    python -m backend.smoke --url http://127.0.0.1:8765

Код возврата: 0 — можно пускать людей, 1 — есть блокирующая проблема.
Предупреждения (WARN) код не меняют: они про безопасность и качество,
а не про работоспособность.
"""
import argparse
import hashlib
import json
import sys
import urllib.request

from . import autoconfig
from . import config
from . import db
from . import embed
from . import llm
from .console import set_console_utf8

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
_results = []


def check(status: str, name: str, detail: str = ""):
    _results.append((status, name, detail))
    line = f"[{status}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)


def _http_get(url: str, timeout: float = 10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post(url: str, body: dict, timeout: float = 900):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_python():
    v = sys.version_info
    if v >= (3, 10):
        check(OK, "Python", f"{v.major}.{v.minor}.{v.micro}")
    else:
        check(FAIL, "Python", f"{v.major}.{v.minor} — нужен 3.10 или новее")


def check_app():
    """Служба падает на импорте, если не хватает зависимости, которую
    FastAPI требует для Form/File (python-multipart). На чистой машине
    это выглядело как «служба активна 5 секунд и умерла», поэтому
    проверяем импортом сами, а не надеждой."""
    try:
        from . import app as _app  # noqa: F401
    except Exception as exc:
        check(FAIL, "Приложение", f"не импортируется: {type(exc).__name__}: {exc}")
        return
    check(OK, "Приложение", "модуль загружается")


def check_db():
    if not config.DB_PATH.exists():
        check(FAIL, "База данных", f"нет файла {config.DB_PATH}")
        return None
    try:
        conn = db.connect()
        db.init_db(conn)
    except Exception as exc:
        check(FAIL, "База данных", f"не открывается: {exc}")
        return None
    totals = {
        "docs": conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0],
        "chunks": conn.execute("SELECT COUNT(*) FROM doc_chunk").fetchone()[0],
        "vectors": conn.execute("SELECT COUNT(*) FROM chunk_vec").fetchone()[0],
        "facts": conn.execute("SELECT COUNT(*) FROM fact_metric").fetchone()[0],
        "divisions": conn.execute("SELECT COUNT(*) FROM division").fetchone()[0],
    }
    size_mb = config.DB_PATH.stat().st_size / (1024 * 1024)
    check(OK, "База данных", f"{size_mb:.1f} МБ, "
          f"документов {totals['docs']}, кусков {totals['chunks']}, "
          f"векторов {totals['vectors']}, подразделений {totals['divisions']}")
    if totals["docs"] == 0:
        check(FAIL, "Корпус", "ни одного документа — ассистенту не на чем "
                              "отвечать. Загрузите файлы или выполните "
                              "python -m backend.ingest")
    elif totals["vectors"] == 0:
        check(FAIL, "Индекс", "есть куски без векторов — выполните "
                              "python -m backend.ingest --force")
    backends = [r[0] for r in conn.execute(
        "SELECT DISTINCT backend FROM chunk_vec").fetchall()]
    if backends == ["hash-fallback"]:
        check(FAIL, "Векторы", "аварийная заглушка: скачайте модель "
                               "эмбеддингов и выполните ingest --force")
    elif backends:
        check(OK, "Векторы", ", ".join(backends))
    return conn


def check_divisions(conn):
    """Пароль, равный логину, — это не «удобно», а вход без пароля."""
    if conn is None:
        return
    rows = conn.execute(
        "SELECT login, pass_hash FROM division").fetchall()
    weak = []
    for r in rows:
        login = r["login"] or ""
        stored = r["pass_hash"] or ""
        # Старый формат PBKDF2 с солью dashboard-<id>: проверяем, не
        # совпадает ли хэш с хэшем логина. Argon2 так не проверить —
        # для него достаточно того, что пароль не был задан кодом.
        legacy = hashlib.pbkdf2_hmac(
            "sha256", login.encode(), f"dashboard-{login}".encode(),
            100_000).hex()
        if stored and stored == legacy:
            weak.append(login)
    if weak:
        check(WARN, "Пароли", "совпадают с логином у: " + ", ".join(weak)
              + ". Смените: python -m backend.users passwd <логин>")
    else:
        check(OK, "Пароли", f"{len(rows)} учётных записей, слабых не найдено")
    path = config.DB_PATH.parent / "initial_passwords.txt"
    if path.exists():
        check(WARN, "Файл паролей", f"{path.name} ещё лежит в data/. "
              "Раздайте пароли и удалите файл.")


def check_models():
    llm_model, emb_model, profile, _hw = autoconfig.load_models()
    names = embed.installed_models()
    if not names:
        check(FAIL, "Ollama", f"не отвечает на {config.OLLAMA_URL}. "
              "Запустите Ollama и повторите проверку.")
        return llm_model
    check(OK, "Ollama", f"отвечает, моделей {len(names)}")

    for role, model in (("small", llm.resolve_role("small")[0]),
                        ("large", llm.resolve_role("large")[0])):
        installed = llm.model_available(model, names)
        if role == "small":
            check(OK if installed else FAIL, f"Модель {role}", model +
                  ("" if installed else " — не скачана: ollama pull " + model))
        elif not installed:
            check(WARN, f"Модель {role}", model + " — не скачана. "
                  "Объяснения проверок будут недоступны, остальное работает.")

    if llm.model_available(emb_model, names):
        check(OK, "Модель эмбеддингов", emb_model)
    else:
        check(FAIL, "Модель эмбеддингов", emb_model + " — не скачана: "
              "ollama pull " + emb_model)

    if config.OLLAMA_TIMEOUT < 300 and "7b" in (llm_model or "").lower():
        check(WARN, "Таймаут", f"{config.OLLAMA_TIMEOUT:.0f} с для 7B на "
              "процессоре мало: поднимите RECTOR_OLLAMA_TIMEOUT")
    return llm_model


def check_server(url: str):
    try:
        h = _http_get(url.rstrip("/") + "/api/health")
    except Exception as exc:
        check(FAIL, "Сервер", f"{url} не отвечает: {exc}")
        return None
    check(OK, "Сервер", f"{url}, документов {h['totals']['docs']}")
    if config.API_HOST not in ("127.0.0.1", "localhost"):
        check(OK, "Доступ по сети", f"слушает {config.API_HOST}:"
              f"{config.API_PORT}")
        if not config.REQUIRE_LOGIN:
            check(WARN, "Вход", "порт открыт, а RECTOR_REQUIRE_LOGIN не "
                  "включён: витрину и чат видит любой в сети")
    return h


def check_ask(url: str, question: str):
    try:
        res = _http_post(url.rstrip("/") + "/api/ask", {"question": question})
    except Exception as exc:
        check(FAIL, "Живой вопрос", f"запрос не прошёл: {exc}")
        return
    answer = (res.get("answer") or "").strip()
    sources = res.get("sources") or []
    meta = res.get("meta") or {}
    if not answer:
        check(FAIL, "Живой вопрос", "пустой ответ")
        return
    if not meta.get("llm"):
        check(FAIL, "Живой вопрос", "модель не ответила: "
              + str(meta.get("reason")))
        return
    if not sources:
        check(FAIL, "Живой вопрос", "ответ без источников")
        return
    check(OK, "Живой вопрос", f"{len(answer)} символов, "
          f"источников {len(sources)}, {meta.get('latency_ms', 0) / 1000:.0f} с")


def main(argv=None) -> int:
    set_console_utf8()
    ap = argparse.ArgumentParser(description="Проверка готовности дашборда")
    ap.add_argument("--url", default=f"http://127.0.0.1:{config.API_PORT}",
                    help="адрес работающего сервера")
    ap.add_argument("--ask", default="", help="задать живой вопрос ассистенту")
    ap.add_argument("--no-server", action="store_true",
                    help="не проверять HTTP-сервер")
    args = ap.parse_args(argv)

    print("Проверка готовности: дашборд ректора")
    print("-" * 60)

    check_python()
    check_app()
    conn = check_db()
    check_divisions(conn)
    if conn is not None:
        conn.close()
    check_models()

    if not args.no_server:
        h = check_server(args.url)
        if h is not None and args.ask:
            check_ask(args.url, args.ask)

    print("-" * 60)
    fails = [r for r in _results if r[0] == FAIL]
    warns = [r for r in _results if r[0] == WARN]
    if fails:
        print(f"НЕ ГОТОВ: блокирующих проблем {len(fails)}"
              + (f", предупреждений {len(warns)}" if warns else ""))
        for _s, name, detail in fails:
            print(f"  - {name}: {detail}")
        return 1
    print("ГОТОВ: блокирующих проблем нет"
          + (f", предупреждений {len(warns)}" if warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
