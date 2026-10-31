# -*- coding: utf-8 -*-
"""
Слой LLM: локальная модель через Ollama.

Обработка отказа намеренно подробная. На компьютере ректора модель
может быть не скачана, может не запуститься после перезагрузки,
может занять больше времени на ответе, чем таймаут. Все эти случаи
различаются в сообщении, потому что «ИИ не работает» бесполезно —
нужно знать, что именно чинить.

Две роли (ТЗ §13):
  small — разбор документов (карточка), быстрые структурированные задачи;
  large — объяснение проверок и сложные рассуждения.

Модель роли выбирается явно: MODEL_SMALL / MODEL_LARGE (переменные
окружения) → meta('llm_model' | 'llm_model_large') → кандидаты из config.
Подмена недоступной модели НЕ происходит молча: если нужного тега нет
среди скачанных, вызов возвращает ok=False и причину. Каждый вызов
пишется в llm_trace (роль, модель, длительность, статус) — тексты
документов и ответы в трассу не попадают.
"""
import json
import os
import time
import urllib.error
import urllib.request

from . import config
from . import embed
from . import prompts

# Переменные окружения, которыми владелец подключает модель на ВМ.
ROLE_ENV = {"small": "MODEL_SMALL", "large": "MODEL_LARGE"}


def chat_model():
    from . import db
    try:
        conn = db.connect()
        m = db.get_meta(conn, "llm_model")
        conn.close()
        if m:
            return m
    except Exception:
        pass
    return config.MODELS_TINY[0]


def installed_models() -> list[str]:
    try:
        return embed.installed_models()
    except Exception:
        return []


def model_available(model: str, models=None) -> bool:
    """Доступна ли РОВНО эта модель.

    Старая model_installed сравнивала базовое имя без тега: «qwen2.5:14b»
    считалась установленной, потому что есть «qwen2.5:1.5b». Для ролей
    это недопустимо — Ollama вернёт 404, а система должна сказать об
    отсутствии большой модели заранее и честно (ТЗ §13.8).
    """
    names = models if models is not None else installed_models()
    if not names:
        return False
    if ":" in model:
        return model in names
    base = model.split(":")[0]
    return any(n == model or n.split(":")[0] == base for n in names)


def model_installed(model: str, models=None) -> bool:
    """Оставлено для прежних вызовов (сравнение по базовому имени)."""
    names = models if models is not None else embed.installed_models()
    base = model.split(":")[0]
    return any(n == model or n.split(":")[0] == base for n in names)


def resolve_role(role: str) -> tuple[str, str]:
    """(имя_модели, откуда взяли) для роли small|large.

    Порядок: переменная окружения → настройка в базе → кандидаты config.
    Последний шаг возвращает первого кандидата, даже если он не скачан:
    chat() потом честно сообщит «модель не скачана», а не подставит другую.
    """
    if role == "large":
        env = os.environ.get(ROLE_ENV["large"], "").strip()
        if env:
            return env, "env"
        try:
            from . import db
            conn = db.connect()
            m = db.get_meta(conn, "llm_model_large")
            conn.close()
            if m:
                return m, "meta"
        except Exception:
            pass
        names = installed_models()
        for cand in config.MODELS_LARGE:
            if model_available(cand, names):
                return cand, "auto"
        return config.MODELS_LARGE[0], "auto"

    env = os.environ.get(ROLE_ENV["small"], "").strip()
    if env:
        return env, "env"
    try:
        from . import db
        conn = db.connect()
        m = db.get_meta(conn, "llm_model")
        conn.close()
        if m:
            return m, "meta"
    except Exception:
        pass
    names = installed_models()
    for cand in config.MODELS_SMALL + config.MODELS_TINY:
        if model_available(cand, names):
            return cand, "auto"
    return config.MODELS_TINY[0], "auto"


def role_status(role: str) -> dict:
    """Состояние роли для /api/health: имя, скачана ли, откуда имя."""
    model, resolved = resolve_role(role)
    # Явное разрешение владельца «работать на малой, пока большой нет».
    if role == "large" and not model_available(model) \
            and os.environ.get("RECTOR_LARGE_FALLBACK") == "1":
        fb, fb_res = resolve_role("small")
        return {"model": model, "installed": False, "resolved": resolved,
                "active": fb, "fallback": True,
                "note": "большая модель не подключена — работает малая "
                        "(RECTOR_LARGE_FALLBACK=1)"}
    return {"model": model, "installed": model_available(model),
            "resolved": resolved, "fallback": False}


def trace(purpose: str, role: str, model: str, resolved: str,
          started: float, ok: bool, error_code: str | None = None,
          prompt_chars: int = 0, response_chars: int = 0):
    """Пишет вызов модели в llm_trace. Ошибка записи не роняет вызывающего."""
    try:
        from . import db
        conn = db.connect()
        conn.execute(
            "INSERT INTO llm_trace(ts, purpose, role, model, resolved,"
            " duration_ms, ok, error_code, prompt_chars, response_chars)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), purpose, role, model,
             resolved, int((time.time() - started) * 1000), int(ok),
             error_code, prompt_chars, response_chars))
        conn.commit()
        conn.close()
    except Exception:
        pass


def chat(system: str, user: str, model: str = None, temperature: float = 0.15,
         num_predict: int = 600, role: str = "small",
         purpose: str = "chat") -> tuple[str, dict]:
    """Одиночный вызов языковой модели с ролью и трассировкой.

    Низкоуровневый примитив для агентов: каждый агент ведёт свой
    разговор со своим системным промптом, и обойтись тут общей
    подсистемой RAG нельзя — иначе все агенты получали бы один и тот
    же контекст и отличались бы только именем.

    Возвращает (текст, meta). meta['ok'] = False означает, что модель
    недоступна, и вызывающий обязан это обработать, а не подставлять
    пустую строку в ответ.
    """
    resolved = "fixed"
    if model is None:
        model, resolved = resolve_role(role)
    meta = {"model": model, "ok": False, "reason": None,
            "role": role, "resolved": resolved, "purpose": purpose}
    started = time.time()

    if not embed.ollama_available():
        meta["reason"] = "Ollama не отвечает на " + config.OLLAMA_URL
        trace(purpose, role, model, resolved, started, False, "ollama_down",
              len(system) + len(user), 0)
        return "", meta
    if not model_available(model):
        meta["reason"] = (f"модель {model} не скачана"
                          + (" — большая модель подключается на ВМ"
                             if role == "large" else ""))
        trace(purpose, role, model, resolved, started, False, "model_missing",
              len(system) + len(user), 0)
        return "", meta

    try:
        out = _post_chat({
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": temperature, "num_predict": num_predict},
        }, timeout=config.OLLAMA_TIMEOUT)
    except urllib.error.HTTPError as exc:
        meta["reason"] = f"Ollama вернула HTTP {exc.code}"
        trace(purpose, role, model, resolved, started, False,
              f"http_{exc.code}", len(system) + len(user), 0)
        return "", meta
    except Exception as exc:
        meta["reason"] = f"ошибка обращения к Ollama: {exc}"
        trace(purpose, role, model, resolved, started, False, "transport",
              len(system) + len(user), 0)
        return "", meta

    content = (out.get("message") or {}).get("content")
    if not content or not content.strip():
        meta["reason"] = "модель вернула пустой ответ"
        trace(purpose, role, model, resolved, started, False, "empty",
              len(system) + len(user), 0)
        return "", meta

    meta["ok"] = True
    meta["duration_ms"] = int((time.time() - started) * 1000)
    trace(purpose, role, model, resolved, started, True, None,
          len(system) + len(user), len(content))
    return content.strip(), meta


def _post_chat(body: dict, timeout: float):
    url = config.OLLAMA_URL.rstrip("/") + "/api/chat"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_context(chunks, limit: int = None) -> str:
    limit = limit or config.CONTEXT_CHUNKS
    parts = []
    for i, ch in enumerate(chunks[:limit], start=1):
        head = f"[{i}] Документ: «{ch['doc_title']}»"
        if ch.get("heading"):
            head += f", раздел: {ch['heading']}"
        # Демонстрационный документ помечается прямо в тексте, а не
        # только в списке источников. На списке источников человек
        # посмотрит потом, а в цитате из демо-документа уже написано
        # «студентов 20 800» — и это прочтут как число университета.
        if ch.get("is_demo"):
            head = ("[ДОКУМЕНТ-ДЕМО. Его числа — вымышленные, это НЕ данные "
                    "университета] " + head)
        parts.append(head + "\n" + ch["text"])
    return "\n\n".join(parts)


def answer(question: str, chunks, model: str = None):
    """Возвращает (текст_ответа, метаданные) + запись в трассу (purpose='ask')."""
    started = time.time()
    text, meta = _answer_inner(question, chunks, model)
    trace("ask", "small", meta.get("model") or "", "meta", started,
          bool(meta.get("llm")), None if meta.get("llm") else "no_llm",
          len(question), len(text))
    return text, meta


def _answer_inner(question: str, chunks, model: str = None):
    model = model or chat_model()
    meta = {"model": model, "llm": False, "reason": None}

    if not embed.ollama_available():
        meta["reason"] = "Ollama не отвечает на " + config.OLLAMA_URL
        return fallback_answer(chunks, meta), meta

    if not model_installed(model):
        meta["reason"] = f"модель {model} не скачана (ollama pull {model})"
        return fallback_answer(chunks, meta), meta

    if not chunks:
        meta["llm"] = True
        return prompts.NO_HIT, meta

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": prompts.SYSTEM},
            {"role": "user", "content": prompts.USER_TMPL.format(
                context=build_context(chunks), question=question)},
        ],
        "options": {"temperature": 0.15, "num_predict": 600},
    }

    started = time.time()
    try:
        out = _post_chat(payload, timeout=config.OLLAMA_TIMEOUT)
    except urllib.error.HTTPError as exc:
        meta["reason"] = f"Ollama вернула HTTP {exc.code}"
        return fallback_answer(chunks, meta), meta
    except Exception as exc:
        meta["reason"] = f"ошибка обращения к Ollama: {exc}"
        return fallback_answer(chunks, meta), meta

    content = (out.get("message") or {}).get("content")
    if not content or not content.strip():
        meta["reason"] = "модель вернула пустой ответ"
        return fallback_answer(chunks, meta), meta

    meta["llm"] = True
    meta["latency_ms"] = int((time.time() - started) * 1000)
    return content.strip(), meta


def fallback_answer(chunks, meta) -> str:
    """Показывает найденное, когда сводный ответ получить нельзя.

    Здесь нельзя писать «ответ не найден»: фрагменты найдены и идут
    ниже. Правильная формулировка — «сводный ответ недоступен,
    потому что ...».
    """
    if not chunks:
        return prompts.NO_HIT
    lines = [prompts.SOURCES_HEADER]
    for i, ch in enumerate(chunks[:5], start=1):
        head = f"[{i}] {ch['doc_title']}"
        if ch.get("heading"):
            head += f" — {ch['heading']}"
        snippet = " ".join(ch["text"].split())
        if len(snippet) > 320:
            snippet = snippet[:320].rsplit(" ", 1)[0] + "…"
        lines.append(f"{head}\n    {snippet}")
    intro = prompts.NO_LLM.format(reason=meta.get("reason") or "причина неизвестна")
    return intro + "\n\n" + "\n".join(lines) + _how_to_enable(meta)


def _how_to_enable(meta) -> str:
    """Подсказка по причине отказа, а не по одной причине на все случаи.

    Раньше здесь всегда стояло «выполните ollama pull <модель>». После
    переключения на модель побольше это превращалось в ложный совет:
    модель скачана, ответ не пришёл из-за таймаута, а человек шёл
    скачивать то, что уже лежит на диске.
    """
    reason = (meta.get("reason") or "").lower()
    model = meta.get("model") or chat_model()
    if "timed out" in reason or "таймаут" in reason:
        return ("\n\nМодель не успела ответить за "
                f"{config.OLLAMA_TIMEOUT:.0f} с. На процессоре большие модели "
                "отвечают минутами: поднимите RECTOR_OLLAMA_TIMEOUT или "
                "верните модель поменьше — python -m backend.models small "
                f"{config.MODELS_TINY[0]}")
    if "не скачана" in reason:
        return ("\n\nСкачайте модель и повторите вопрос:\n  ollama pull "
                + model)
    if "не отвечает" in reason:
        return ("\n\nЗапустите Ollama (start.bat делает это сам) и повторите "
                "вопрос.")
    return ("\n\nСостояние моделей: python -m backend.models — там видно, "
            "какая модель выбрана и скачана ли она.")