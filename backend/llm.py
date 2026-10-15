# -*- coding: utf-8 -*-
"""
Слой LLM: локальная модель через Ollama.

Обработка отказа намеренно подробная. На компьютере ректора модель
может быть не скачана, может не запуститься после перезагрузки,
может занять больше времени на ответе, чем таймаут. Все эти случаи
различаются в сообщении, потому что «ИИ не работает» бесполезно —
нужно знать, что именно чинить.
"""
import json
import time
import urllib.error
import urllib.request

from . import config
from . import embed
from . import prompts


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


def model_installed(model: str, models=None) -> bool:
    names = models if models is not None else embed.installed_models()
    base = model.split(":")[0]
    return any(n == model or n.split(":")[0] == base for n in names)


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
        parts.append(head + "\n" + ch["text"])
    return "\n\n".join(parts)


def chat(system: str, user: str, model: str = None, temperature: float = 0.15,
         num_predict: int = 600):
    """Одиночный вызов языковой модели.

    Низкоуровневый примитив для агентов: каждый агент ведёт свой
    разговор со своим системным промптом, и обойтись тут общей
    подсистемой RAG нельзя — иначе все агенты получали бы один и тот
    же контекст и отличались бы только именем.

    Возвращает (текст, meta). meta['ok'] = False означает, что модель
    недоступна, и вызывающий обязан это обработать, а не подставлять
    пустую строку в ответ.
    """
    model = model or chat_model()
    meta = {"model": model, "ok": False, "reason": None}

    if not embed.ollama_available():
        meta["reason"] = "Ollama не отвечает на " + config.OLLAMA_URL
        return "", meta
    if not model_installed(model):
        meta["reason"] = f"модель {model} не скачана"
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
        return "", meta
    except Exception as exc:
        meta["reason"] = f"ошибка обращения к Ollama: {exc}"
        return "", meta

    content = (out.get("message") or {}).get("content")
    if not content or not content.strip():
        meta["reason"] = "модель вернула пустой ответ"
        return "", meta

    meta["ok"] = True
    return content.strip(), meta


def answer(question: str, chunks, model: str = None):
    """Возвращает (текст_ответа, метаданные)."""
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
    return intro + "\n\n" + "\n".join(lines) + (
        "\n\nКак включить сводные ответы: запустите start.bat и выполните"
        "\nв терминале  ollama pull " + (meta.get("model") or "qwen2.5:1.5b"))