# -*- coding: utf-8 -*-
"""
Эмбеддинги. Основной режим — локальная модель через Ollama.

Запасной режим (hash) нужен не для красоты: он позволяет установить
систему на компьютер, где Ollama ещё не скачана, и проверить, что
поиск, API и интерфейс работают. Семантики он не даёт — это честно
записано в поле backend, чтобы в выдаче и в /api/health было видно,
какой режим сейчас живёт. молчаливая подмена модели на заглушку —
это как раз тот случай, когда «всё зелёное», а система бессмысленна.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request

import numpy as np

from . import config


def _post(path: str, payload: dict, timeout: float = None):
    url = config.OLLAMA_URL.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout or config.OLLAMA_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


_TAGS_CACHE = {"at": 0.0, "ok": False, "models": []}
# Окно кэша. /api/health дёргается при каждой загрузке страницы, и без
# кэша это десятки секунд ожидания на компьютере, где Ollama выключена.
_TAGS_TTL = 5.0


def _tags(force=False):
    """Один запрос /api/tags с коротким кэшем: (доступна, список моделей).

    Раньше доступность и список моделей проверялись отдельно в четырёх
    местах. При выключенной Ollama соединение к 127.0.0.1:11434
    отвергается на Windows примерно через 2 секунды (быстрый RST на
    loopback не отправляется), и четыре проверки подряд давали
    16 секунд на каждый /api/health.
    """
    now = time.time()
    if not force and now - _TAGS_CACHE["at"] < _TAGS_TTL:
        return _TAGS_CACHE["ok"], _TAGS_CACHE["models"]
    ok, models = False, []
    try:
        # Короткий таймаут: раз мы и так знаем, что порт закрыт медленно,
        # ждать дольше двух с половиной секунд нет смысла.
        with urllib.request.urlopen(
                config.OLLAMA_URL.rstrip("/") + "/api/tags", timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        ok = bool(resp.status == 200)
    except Exception:
        ok = False
    _TAGS_CACHE.update({"at": now, "ok": ok, "models": models})
    return ok, models


def ollama_available() -> bool:
    return _tags()[0]


def installed_models():
    return list(_tags()[1])


def _embed_ollama(texts, model):
    """Сначала /api/embed (батч), при 404 — /api/embeddings по одному."""
    try:
        out = _post("/api/embed", {"model": model, "input": texts})
        vecs = out.get("embeddings")
        if vecs:
            return np.asarray(vecs, dtype=np.float32)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    except Exception:
        pass

    vecs = []
    for t in texts:
        out = _post("/api/embeddings", {"model": model, "prompt": t})
        v = out.get("embedding")
        if not v:
            raise RuntimeError("Ollama вернула пустой эмбеддинг")
        vecs.append(v)
    return np.asarray(vecs, dtype=np.float32)


def _embed_hash(texts, dim=768):
    """Запасной режим: хэширование символьных троек.

    Семантики нет. Даёт только лексическое сходство — но этого
    достаточно, чтобы поиск, API и интерфейс были проверяемыми
    до установки Ollama.
    """
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for row, text in enumerate(texts):
        s = " " + "".join(c.lower() if c.isalnum() else " " for c in text) + " "
        padded = "   " + s + "   "
        for i in range(len(padded) - 2):
            gram = padded[i:i + 3]
            h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] & 1 else -1.0
            out[row, idx] += sign
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return out / norms


def embed_texts(texts, model=None, allow_fallback=True):
    """Возвращает (матрица float32, имя бэкенда)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32), "none"
    model = model or _stored_model()
    if model and ollama_available():
        try:
            vecs = _embed_ollama(texts, model)
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return vecs / norms, f"ollama:{model}"
        except Exception:
            if not allow_fallback:
                raise
    if not allow_fallback:
        raise RuntimeError("Ollama недоступна или модель не задана")
    return _embed_hash(texts), "hash-fallback"


def _stored_model():
    """Модель из meta, иначе из настроек автоконфигуратора."""
    from . import db
    try:
        conn = db.connect()
        m = db.get_meta(conn, "embed_model")
        conn.close()
        if m:
            return m
    except Exception:
        pass
    return (config.EMBED_MEDIUM if config.EMBED_MEDIUM else config.EMBED_TINY)[0]


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim)