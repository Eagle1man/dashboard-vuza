# -*- coding: utf-8 -*-
"""
HTTP-сервер дашборда.

Раздаёт и интерфейс (папка web/), и API. Открывать дашборд надо
именно через http://127.0.0.1:8765/, а не двойным кликом по index.html:
в режиме file:// браузер не пустит страницу обращаться к /api
из-за политики происхождения, и ИИ молча вернёт «нет связи» —
выглядит как поломка, хотя ломается только способ открытия.
"""
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel

from . import autoconfig
from . import assets
from . import auth
from . import config
from . import monitoring
from . import consistency
from . import crew
from . import db
from . import embed
from . import ingest as ingest_mod
from . import intake
from . import journal
from . import llm
from . import retrieve
from . import review
from . import uploads

_conn = None


def conn():
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = db.connect()
        db.init_db(_conn)
        # Витрина идемпотентна: вызов на старте заодно убирает
        # демонстрационные метрики, оставшиеся от прежних версий.
        db.seed_metrics(_conn)
        db.seed_divisions(_conn)
    return _conn


@asynccontextmanager
async def lifespan(_app):
    conn()
    # Версия стилей проставляется при старте, а не «когда вспомнят».
    # Если правили style.css и забыли команду, браузер покажет старую
    # копию — человек нажмёт F5 и решит, что ничего не изменилось.
    try:
        assets.stamp()
    except Exception as exc:  # версия статики не повод не поднимать сервис
        print("не удалось обновить версию стилей:", exc)
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


@app.middleware("http")
async def csrf_guard(request, call_next):
    """CSRF: изменяющий запрос с нашей cookie приходит только с нашего источника.

    SameSite=Lax режет большинство кросс-сайтовых отправок, но этого
    мало для форм: сверяем Origin/Referer с Host для всех
    cookie-сессионных POST/PUT/PATCH/DELETE. Если Origin и Referer
    нет вовсе — запрос не браузерный (curl), куку туда не подкладывают.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.cookies.get("dashboard_session")
        if token and auth.validate_session(token):
            host = (request.headers.get("host") or "").lower()
            origin = (request.headers.get("origin") or "").lower()
            referer = (request.headers.get("referer") or "").lower()
            src = origin or referer
            if src and host and host not in src:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Запрос отклонён: недопустимый "
                                       "источник (CSRF)"})
    return await call_next(request)


@app.middleware("http")
async def require_login_guard(request, call_next):
    """Публикация в сеть: данные только после входа.

    Включается RECTOR_REQUIRE_LOGIN=1 (см. docs/DEPLOY.md). Пока сервер
    слушает 127.0.0.1, это не нужно: до него не дотянуться снаружи.
    Как только порт открыт в сеть, витрина, документы и чат без входа
    означают, что любой сотрудник сети читает всю базу.

    Что остаётся открытым и почему:
      - /api/health — по нему страница входа показывает «сервер онлайн»,
        мониторинг и backend.smoke проверяют готовность;
      - /api/login, /api/logout — иначе войти неоткуда;
      - статика (css/js/img) — без неё страница входа не отрисуется.
    """
    if not config.REQUIRE_LOGIN:
        return await call_next(request)

    path = request.url.path
    if (path in ("/", "/index.html", "/api/health", "/api/login", "/api/logout")
            or path.startswith(("/css/", "/js/", "/img/"))):
        return await call_next(request)

    if auth.validate_session(request.cookies.get("dashboard_session")):
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(status_code=401,
                            content={"detail": "Необходимо войти"})
    if path.endswith(".html"):
        return RedirectResponse("/index.html", status_code=302)
    return await call_next(request)


@app.middleware("http")
async def no_stale_assets(request, call_next):
    """Запрещать браузеру держать устаревшие файлы интерфейса.

    Без Cache-Control браузер применяет эвристическое кэширование и
    после обновления продолжает отдавать пользователю старые css и js.
    Для локальной витрины это выглядит как «обновил, а интерфейс
    прежний», и разбираться приходится вручную — обычно через Ctrl+F5,
    о котором обычный пользователь не знает.

    Здесь нельзя ставить max-age, зато можно no-cache: браузер будет
    уточнять у сервера по ETag и получать 304, то есть почти без трафика.
    """
    response = await call_next(request)
    path = request.url.path
    # .html тоже сюда: страницы дашборда без no-cache отдавали старую
    # разметку после правки — проверяющий видел прежний интерфейс и
    # считал, что правка не применилась.
    if path.startswith(("/css/", "/js/", "/img/")) or path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


_NOT_FOUND_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>Страница не найдена · Дашборд ректора ИУБиП</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body { font-family: "Segoe UI", Arial, sans-serif; background: #ebf7ff;
        color: #06172d; display: flex; align-items: center; justify-content: center;
        min-height: 100vh; margin: 0; }
 .box { background: #fff; border: 1px solid #cfe0ed; border-radius: 16px;
        padding: 32px 36px; max-width: 460px; text-align: center;
        box-shadow: 0 6px 24px rgba(6,23,45,.08); }
 h1 { font-family: Georgia, serif; font-size: 26px; margin: 0 0 8px; color: #0c5f97; }
 p { color: #4b688b; font-size: 14px; line-height: 1.5; margin: 0 0 18px; }
 a { display: inline-block; background: #0c5f97; color: #fff; text-decoration: none;
     padding: 11px 22px; border-radius: 999px; font-weight: 600; font-size: 14px; }
 a:hover { background: #063d6b; }
</style></head>
<body><div class="box">
 <h1>404 · Страница не найдена</h1>
 <p>Возможно, ссылка устарела или в адресе опечатка. Откройте главную —
    там вход, лента показателей и загрузка данных.</p>
 <a href="/">← На главную</a>
</div></body></html>"""


@app.exception_handler(StarletteHTTPException)
async def page_not_found(request, exc):
    """404: человеку — страница с кнопкой «на главную», клиентам API — JSON.

    Раньше оба получали JSON {"detail":"Not Found"}: для браузера это
    белый экран без единой понятной кнопки, а старая ссылка на дашборд
    выглядела как поломка системы, а не как 404.
    """
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return HTMLResponse(_NOT_FOUND_PAGE, status_code=404)
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail},
                        headers=getattr(exc, "headers", None))


class AskRequest(BaseModel):
    question: str
    top_k: int = 0
    # "crew" — вопрос проходит роутер, специалиста и критика;
    # "fast" — без критика, для слабых машин.
    mode: str = "crew"
    # "crew" — экипаж агентов, "single" — прежний одиночный путь
    # (его оставили: он быстрее и служит запасным при отказе экипажа)
    pipeline: str = "crew"


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
        # Пути файловой системы наружу не отдаются (ТЗ §11): раньше
        # здесь стояли database и knowledge_dir, и любой мог прочитать
        # структуру сервера. Размер базы — без пути, он и так нужен.
        "db_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
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
        # Роли конвейера (ТЗ §13): какая модель на какую роль назначена
        # и скачана ли она. «installed: false» у large — это и есть
        # состояние «большую подключим на ВМ».
        "roles": {
            "small": llm.role_status("small"),
            "large": llm.role_status("large"),
        },
        "profile": profile,
        "hardware": hw,
        "last_ingest": db.get_meta(c, "last_ingest"),
    }


@app.get("/api/agents")
def agents():
    """Состав экипажа: кто есть, за что отвечает, какие блоки видит."""
    out = crew.describe()
    c = conn()
    # Сколько документов реально попадает в поле зрения каждого
    # специалиста. Агент, у которого ноль документов, — не специалист,
    # а пустая обёртка, и это надо показывать.
    for a in out["agents"]:
        if a["blocks"]:
            marks = ",".join("?" * len(a["blocks"]))
            a["documents"] = c.execute(
                f"SELECT COUNT(DISTINCT doc_id) FROM doc_block"
                f" WHERE block_id IN ({marks})", a["blocks"]).fetchone()[0]
        else:
            a["documents"] = c.execute("SELECT COUNT(*) FROM doc").fetchone()[0]
    return out


# Модель одна, а ответ на этой машине идёт минутами. Без заслонки второй
# вопрос встаёт в очередь к Ollama (OLLAMA_NUM_PARALLEL=1), браузер
# сдаётся по таймауту, а человек видит «сбой», не понимая, что его вопрос
# вообще не начинался. Честнее сразу сказать «занято» и показать, сколько
# ждать. Замок берётся без ожидания: очередь из вопросов — это не помощь.
_ASK_LOCK = threading.Lock()


@contextmanager
def _one_question_at_a_time():
    if not _ASK_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Ассистент уже отвечает на другой вопрос. "
                   "Дождитесь ответа или нажмите «Стоп» в панели.")
    try:
        yield
    finally:
        _ASK_LOCK.release()


@app.post("/api/ask/crew")
def ask_crew(req: AskRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Пустой вопрос")
    question = question[:2000]
    mode = req.mode if req.mode in crew.MODES else "auto"

    with _one_question_at_a_time():
        return _answer_with_crew(question, mode, req.top_k)


def _answer_with_crew(question: str, mode: str, top_k):
    c = conn()
    try:
        out = crew.run(question, c, mode=mode, top_k=top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка агента: {exc}")

    c.execute(
        "INSERT INTO ask_log(ts, question, answer, chunk_ids, model, hits, latency_ms)"
        " VALUES(?,?,?,?,?,?,?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), question, out["answer"],
         ",".join(str(s["n"]) for s in out["sources"]),
         (out.get("search") or {}).get("vector_backend"),
         len(out["sources"]), out["latency_ms"]))
    c.commit()

    return out


@app.get("/api/metrics")
def metrics():
    return db.load_vitrine(conn())


@app.get("/api/monitoring")
def monitoring_view():
    """Место вуза среди вузов Ростовской области за 2024 и 2025 годы.

    Источник — открытые материалы monitoring.miccedu.ru; файл
    data/monitoring.json собирается скриптом (см. backend/monitoring.py).
    Если файла нет, отдаём available=false, а не пустые графики.
    """
    return monitoring.load()


@app.get("/api/monitoring/detail")
def monitoring_detail():
    """Все показатели карточки вуза за 2024 и 2025 с изменением.

    Нужно для раздела «Мониторинг»: там два больших блока — динамика по
    вузу и сравнение с вузами области. Здесь первый.
    """
    return monitoring.detail()


@app.get("/api/monitoring/spo")
def monitoring_spo():
    """СПО: показатели организации за 2024 и 2025, область и РФ, место."""
    return monitoring.spo()


@app.get("/api/monitoring/directions")
def monitoring_directions():
    """Раздел III карточки: 57 показателей по направлениям деятельности.

    Медианы на сайте показаны отношением «значение / медиана», поэтому
    точное число восстановлено только там, где шкала не обрезана.
    """
    return monitoring.directions()


@app.get("/api/monitoring/card")
def monitoring_card():
    """Разделы I и IV: общие сведения, контингент и УГН(С)."""
    return monitoring.card()


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
    journal.log(conn(), "reindex", detail=f"force={force}, docs={rep.get('docs')}")
    return rep


@app.post("/api/ask")
def ask(req: AskRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Пустой вопрос")
    if len(question) > 2000:
        question = question[:2000]
    with _one_question_at_a_time():
        return _answer_question(question, req.top_k)


def _answer_question(question: str, top_k):
    c = conn()
    t0 = time.time()
    try:
        chunks, stats = retrieve.search(
            c, question, top_k=top_k or config.RETRIEVE_TOP_K)
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
    }


@app.get("/api/asklog")
def asklog(limit: int = 20):
    rows = conn().execute(
        "SELECT ts, question, model, hits, latency_ms FROM ask_log"
        " ORDER BY id DESC LIMIT ?", (min(limit, 200),)).fetchall()
    return {"entries": [dict(r) for r in rows]}


# ------------------------------------------------------------------
# Аутентификация подразделений
# ------------------------------------------------------------------
class LoginRequest(BaseModel):
    login: str
    password: str


@app.post("/api/login")
def login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else None

    # Лимит неудачных попыток (ТЗ §12): считается до проверки пароля,
    # иначе перебор не замедлялся бы.
    reason = auth.login_blocked(req.login)
    if reason:
        journal.log(conn(), "login_block", actor=str(req.login)[:80],
                    detail=reason)
        raise HTTPException(status_code=429, detail=reason)

    division_id = auth.verify_login(req.login, req.password)
    auth.record_login(req.login, ip, bool(division_id))
    if not division_id:
        journal.log(conn(), "login_fail", actor=str(req.login)[:80],
                    detail="неверный логин или пароль")
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    auth.clear_attempts(req.login)
    token = auth.create_session(division_id)
    journal.log(conn(), "login", actor=str(req.login)[:80], role=division_id)
    c = db.connect()
    div = c.execute(
        "SELECT division_id, title FROM division WHERE division_id = ?",
        (division_id,)).fetchone()
    response = JSONResponse(content={
        "ok": True,
        "division_id": division_id,
        "division_title": div["title"],
        "is_admin": division_id == "admin",
    })
    response.set_cookie(
        key="dashboard_session", value=token,
        httponly=True, max_age=auth.SESSION_TTL, path="/",
        samesite="lax")
    return response


def _session_division(request: Request) -> str:
    token = request.cookies.get("dashboard_session")
    division_id = auth.validate_session(token)
    return auth.require_auth(division_id)


@app.post("/api/logout")
def logout(request: Request):
    token = request.cookies.get("dashboard_session")
    division_id = auth.validate_session(token)
    if token:
        auth.delete_session(token)
    if division_id:
        journal.log(conn(), "logout", role=division_id)
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(key="dashboard_session", path="/")
    return response


@app.get("/api/me")
def me(request: Request):
    division_id = _session_division(request)
    c = db.connect()
    div = c.execute(
        "SELECT division_id, title, contact FROM division WHERE division_id = ?",
        (division_id,)).fetchone()
    return {"division_id": division_id, "division_title": div["title"],
            "contact": div["contact"], "is_admin": division_id == "admin"}


# ------------------------------------------------------------------
# Справочник подразделений (для выпадающего списка в анкете)
# ------------------------------------------------------------------
@app.get("/api/divisions")
def divisions(request: Request):
    division_id = _session_division(request)
    c = db.connect()
    rows = c.execute(
        "SELECT division_id, title, login, contact FROM division"
        " ORDER BY division_id").fetchall()
    return {"divisions": [dict(r) for r in rows]}


# ------------------------------------------------------------------
# Загрузка файлов подразделениями
# ------------------------------------------------------------------
class UploadResponse(BaseModel):
    pass


@app.post("/api/upload")
async def upload(request: Request,
                 file: UploadFile = File(...),
                 division_id: str | None = Form(default=None),
                 title: str = Form(default=""),
                 purpose: str = Form(default=""),
                 period: str = Form(default=""),
                 frequency: str = Form(default="разово"),
                 responsible: str = Form(default=""),
                 access_level: str = Form(default="open"),
                 metric_ids: str = Form(default="[]"),
                 confirmed: str = Form(default="false"),
                 metric_values: str = Form(default="{}")):

    did = _session_division(request)
    if division_id and division_id != did:
        raise HTTPException(
            status_code=403,
            detail="Нельзя загружать от имени другого подразделения")
    division_id = did

    # Пустая анкета отклоняется внутри handle_upload — там же, где
    # остальные проверки, чтобы правило не зависело от роута.
    form = {
        "title": title, "purpose": purpose, "period": period,
        "frequency": frequency, "responsible": responsible,
        "access_level": access_level, "metric_ids": metric_ids,
        "confirmed": confirmed, "metric_values": metric_values,
    }
    try:
        return uploads.handle_upload(division_id, file, form)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/uploads")
def uploads_list(request: Request, division_id: str | None = None):
    did = _session_division(request)
    if did == "admin":
        # Администратор может и посмотреть чужое — но только явно запросив.
        pass
    else:
        # Не-админ всегда видит только своё подразделение: параметр
        # клиента не может расширить выборку (иначе подзаголовок
        # «файлы вашего подразделения» врал бы).
        division_id = did
    return {"uploads": uploads.list_uploads(division_id)}


@app.get("/api/uploads/{upload_id}")
def upload_detail(request: Request, upload_id: int):
    did = _session_division(request)
    detail = uploads.get_upload_detail(upload_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Загрузка не найдена")
    if did != "admin" and detail["division_id"] != did:
        raise HTTPException(status_code=403,
                            detail="Нельзя смотреть загрузку другого подразделения")
    return detail


# ------------------------------------------------------------------
# Экран проверки: карточка, проверки, решение человека (ТЗ §10)
# ------------------------------------------------------------------
@app.get("/api/upload/{upload_id}/review")
def upload_review(request: Request, upload_id: int):
    """Состояние загрузки для экрана проверки (своё подразделение или admin)."""
    did = _session_division(request)
    return review.payload(conn(), upload_id, did)


@app.get("/api/upload/{upload_id}/raw")
def upload_raw(request: Request, upload_id: int,
               offset: int = 0, limit: int = 300):
    """Сырые строки файла: страница либо лист/строка/адрес ячейки."""
    did = _session_division(request)
    review.load_upload(conn(), upload_id, did)
    limit = max(1, min(int(limit), 2000))
    offset = max(0, int(offset))
    rows = [dict(r) for r in conn().execute(
        "SELECT raw_id, kind, label, text, page_no, sheet, row_no, col_no,"
        " cell_ref FROM upload_raw WHERE upload_id = ? ORDER BY seq"
        " LIMIT ? OFFSET ?", (upload_id, limit, offset)).fetchall()]
    total = conn().execute(
        "SELECT COUNT(*) FROM upload_raw WHERE upload_id = ?",
        (upload_id,)).fetchone()[0]
    return {"items": rows, "total": total, "offset": offset, "limit": limit}


@app.post("/api/upload/{upload_id}/decide")
async def upload_decide(request: Request, upload_id: int):
    """Решение человека: accept (опубликовать), fix (исправить), reject."""
    did = _session_division(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400,
                            detail="Ожидается JSON с полем action")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400,
                            detail="Тело запроса должно быть объектом")
    return review.decide(conn(), upload_id, did, did, body)


@app.post("/api/upload/{upload_id}/explain")
def upload_explain(request: Request, upload_id: int):
    """Объяснение проверок большой моделью; если её нет — честная причина."""
    did = _session_division(request)
    review.load_upload(conn(), upload_id, did)
    return consistency.explain_sync(upload_id)


# ------------------------------------------------------------------
# Журнал событий (ТЗ §11) и провенанс показателей (ТЗ §5.5)
# ------------------------------------------------------------------
@app.get("/api/journal")
def journal_view(request: Request, limit: int = 50, offset: int = 0,
                 action: str | None = None, actor: str | None = None):
    """Журнал аудита. Читается только под сессией: видно, кто входил."""
    _session_division(request)
    return journal.query(limit=limit, offset=offset, action=action,
                         actor=actor)


@app.get("/api/journal/export/csv")
def journal_export(request: Request, limit: int = 1000):
    """CSV-выгрузка журнала: ячейки-формулы экранированы (ТЗ §11)."""
    did = _session_division(request)
    rows = journal.query(limit=min(int(limit), 5000), offset=0)
    data = journal.to_csv(rows["items"])
    journal.log(conn(), "export", actor=did, role=did,
                detail=f"журнал CSV, {len(rows['items'])} строк")
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="journal.csv"'})


@app.get("/api/metrics/{metric_id}/sources")
def metric_sources(metric_id: str):
    """Провенанс значений показателя: от числа — к документу (ТЗ §5.5)."""
    c = conn()
    met = c.execute(
        "SELECT metric_id, title, unit, source, src_note FROM dim_metric"
        " WHERE metric_id = ?", (metric_id,)).fetchone()
    if not met:
        raise HTTPException(status_code=404, detail="Показатель не найден")
    rows = c.execute(
        """SELECT f.period_id, f.value, f.value_exact, p.label AS period,
                  prov.source_upload_id AS upload_id, prov.source_file_name,
                  prov.extraction_method, prov.parser_model,
                  prov.confirmed_by, prov.confirmed_at, prov.note,
                  d.title AS division_title
             FROM fact_metric f
             JOIN dim_period p ON p.period_id = f.period_id
             LEFT JOIN fact_provenance prov ON prov.prov_id = (
                 SELECT MAX(p2.prov_id) FROM fact_provenance p2
                  WHERE p2.period_id = f.period_id
                    AND p2.metric_id = f.metric_id
                    AND p2.org_id = f.org_id)
             LEFT JOIN upload u ON u.upload_id = prov.source_upload_id
             LEFT JOIN division d ON d.division_id = u.division_id
            WHERE f.metric_id = ?
            ORDER BY p.ord""", (metric_id,)).fetchall()
    return {"metric": dict(met), "values": [dict(r) for r in rows]}


@app.get("/api/export/xlsx")
def export_xlsx():
    """Выгрузка витрины показателей в Excel.

    Кнопка «XLSX» на ленте раньше отдавала тост-заглушку («подключится
    в production»). Строится из той же витрины, что и карточки, вместе
    с провенансом: источник, подразделение, период и пометка «демо».
    """
    from io import BytesIO

    try:
        from openpyxl import Workbook
    except ImportError:
        raise HTTPException(
            status_code=500, detail="openpyxl не установлен — экспорт недоступен")

    status_ru = {"none": "нет цели", "green": "зелёный",
                 "yellow": "жёлтый", "red": "красный"}
    c = conn()
    vit = db.load_vitrine(c)
    blocks = {b["id"]: b["title"] for b in vit["blocks"]}

    wb = Workbook()
    ws = wb.active
    ws.title = "Показатели"
    ws.append(["Блок", "Показатель", "Значение", "Единица", "Статус",
               "Цель", "Природа", "Подразделение", "Период", "Документ",
               "Обновлено"])
    for m in vit["metrics"]:
        p0 = (m["prov"] or [{}])[0]
        ws.append([
            blocks.get(m["block"], m["block"]),
            m["title"],
            m["current"],
            m["unit"] or "",
            status_ru.get(m["status"], m["status"]),
            m["target"] if m["target"] is not None else "",
            "документ университета",
            p0.get("division", ""),
            p0.get("period", ""),
            p0.get("title", ""),
            p0.get("uploaded_at", ""),
        ])
    ws.freeze_panes = "A2"
    journal.log(conn(), "export", detail=f"XLSX, {len(vit['metrics'])} показателей")

    buf = BytesIO()
    wb.save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="pokazateli.xlsx"'})


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