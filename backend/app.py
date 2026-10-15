# -*- coding: utf-8 -*-
"""
HTTP-сервер дашборда.

Раздаёт и интерфейс (папка web/), и API. Открывать дашборд надо
именно через http://127.0.0.1:8765/, а не двойным кликом по index.html:
в режиме file:// браузер не пустит страницу обращаться к /api
из-за политики происхождения, и ИИ молча вернёт «нет связи» —
выглядит как поломка, хотя ломается только способ открытия.
"""
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import autoconfig
from . import config
from . import db
from . import embed
from . import ingest as ingest_mod
from . import llm
from . import retrieve

_conn = None


def conn():
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = db.connect()
        db.init_db(_conn)
        if not db.get_meta(_conn, "demo_seeded"):
            db.seed_metrics(_conn)
    return _conn


@asynccontextmanager
async def lifespan(_app):
    conn()
    # Модель выбирается один раз и дальше живёт в meta: иначе каждый
    # перезапуск на компьютере, где ОЗУ занята, переключал бы модель
    # на слабую и обратно без участия человека.
    if not db.get_meta(conn(), "llm_model"):
        hw = autoconfig.detect()
        llm_m, emb_m, profile, _why = autoconfig.choose_models(hw)
        autoconfig.save_models(llm_m, emb_m, profile, hw)
    yield


app = FastAPI(title="Дашборд ректора — бэкенд", version="1.0",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # сервер слушает только 127.0.0.1
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str
    top_k: int = 0


@app.get("/api/health")
def health():
    c = conn()
    llm_m, emb_m, profile, hw = autoconfig.load_models()
    totals = {
        "docs": c.execute("SELECT COUNT(*) FROM doc").fetchone()[0],
        "chunks": c.execute("SELECT COUNT(*) FROM doc_chunk").fetchone()[0],
        "vectors": c.execute("SELECT COUNT(*) FROM chunk_vec").fetchone()[0],
        "metrics": c.execute("SELECT COUNT(*) FROM dim_metric").fetchone()[0],
        "facts": c.execute("SELECT COUNT(*) FROM fact_metric").fetchone()[0],
    }
    backends = [r[0] for r in c.execute(
        "SELECT DISTINCT backend FROM chunk_vec").fetchall()]
    # Один запрос к Ollama на весь ответ. Проверять доступность и список
    # моделей отдельно нельзя: при выключенной Ollama каждая попытка
    # стоит около двух секунд на Windows.
    ollama_up, installed = embed._tags()
    return {
        "ok": True,
        "database": str(config.DB_PATH),
        "db_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
        "knowledge_dir": str(config.KNOWLEDGE_DIR),
        "totals": totals,
        "vector_backend": backends[0] if len(backends) == 1 else backends,
        "ollama": {
            "url": config.OLLAMA_URL,
            "available": ollama_up,
            "llm_model": llm_m,
            "embed_model": emb_m,
            "llm_installed": llm.model_installed(llm_m, installed),
            "embed_installed": llm.model_installed(emb_m, installed),
            "installed_models": installed,
        },
        "profile": profile,
        "hardware": hw,
        "last_ingest": db.get_meta(c, "last_ingest"),
        "demo_notice": db.get_meta(c, "demo_data_notice"),
    }


@app.get("/api/metrics")
def metrics():
    return db.load_vitrine(conn())


@app.get("/api/docs")
def docs():
    rows = conn().execute(
        "SELECT doc_id, rel_path, title, doc_type, n_chunks, n_pages, is_demo,"
        " added_at FROM doc ORDER BY title").fetchall()
    return {"docs": [dict(r) for r in rows]}


@app.post("/api/reindex")
def reindex(force: bool = False, prune: bool = True):
    t0 = time.time()
    try:
        rep = ingest_mod.ingest(force=force, prune=prune)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    rep["seconds"] = round(time.time() - t0, 1)
    return rep


@app.post("/api/ask")
def ask(req: AskRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Пустой вопрос")
    if len(question) > 2000:
        question = question[:2000]

    c = conn()
    t0 = time.time()
    try:
        chunks, stats = retrieve.search(
            c, question, top_k=req.top_k or config.RETRIEVE_TOP_K)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка поиска: {exc}")

    text, meta = llm.answer(question, chunks)
    latency = int((time.time() - t0) * 1000)

    sources = [{
        "n": i + 1,
        "title": ch["doc_title"],
        "heading": ch["heading"],
        "rel_path": ch["rel_path"],
        "doc_type": ch["doc_type"],
        "is_demo": ch["is_demo"],
        "score": ch["score"],
        "found_by": ch["found_by"],
        "snippet": " ".join(ch["text"].split())[:400],
    } for i, ch in enumerate(chunks[:config.CONTEXT_CHUNKS])]

    c.execute(
        "INSERT INTO ask_log(ts, question, answer, chunk_ids, model, hits, latency_ms)"
        " VALUES(?,?,?,?,?,?,?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), question, text,
         ",".join(str(ch["chunk_id"]) for ch in chunks), meta.get("model"),
         len(chunks), latency))
    c.commit()

    return {
        "answer": text,
        "sources": sources,
        "meta": {
            "model": meta.get("model"),
            "llm": meta.get("llm"),
            "reason": meta.get("reason"),
            "latency_ms": latency,
            "retrieved": len(chunks),
            "search": stats,
        },
        "demo_notice": db.get_meta(c, "demo_data_notice"),
    }


@app.get("/api/asklog")
def asklog(limit: int = 20):
    rows = conn().execute(
        "SELECT ts, question, model, hits, latency_ms FROM ask_log"
        " ORDER BY id DESC LIMIT ?", (min(limit, 200),)).fetchall()
    return {"entries": [dict(r) for r in rows]}


@app.exception_handler(500)
def _err(request, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if config.WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")


def main():
    import uvicorn
    print(f"Дашборд ректора: http://{config.API_HOST}:{config.API_PORT}/")
    print(f"База данных:      {config.DB_PATH}")
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_level="info")


if __name__ == "__main__":
    main()