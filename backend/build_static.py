"""Сборка статической витрины для GitHub Pages.

Запуск::

    .\\.venv\\Scripts\\python.exe -m backend.build_static

Зачем это отдельный скрипт, а не «просто открыть web/index.html»: у
дашборда четыре страницы и десяток обращений к /api/... . Чтобы ссылку
можно было отправить человеку, у которого нет ни сервера, ни Python,
ни модели, данные вшиваются в страницу, а сетевые вызовы перехватываются
шимом. Нейросети для просмотра не нужны: ассистент в этой версии честно
отвечает заготовкой и говорит, что работает только в полной версии.

Что делает сборка:
  1. Копирует web/ в publish/, кроме серверных страниц (загрузка файлов,
     «мои загрузки», журнал, проверка загрузки) — им нужен сервер.
  2. Переписывает абсолютные пути /js, /css, /img, /xxx.html в
     относительные: GitHub Pages отдаёт сайт из подкаталога /<repo>/, и
     «/js/app.js» там указывает в пустоту.
  3. Вшивает снимок API в publish/js/static-data.js.
  4. Кладёт publish/js/static-api.js — шим window.fetch, который отвечает
     на /api/... из снимка и не ходит в сеть.
  5. Отключает серверные кнопки (XLSX, «обновить»), логин и загрузку,
     оставляя видимую пометку «статическая витрина».

Каталог publish/ коммитится в репозиторий: GitHub Pages публикует его
как артефакт (см. .github/workflows/pages.yml). Сборка в CI невозможна —
база data/dashboard.db в git не хранится.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WEB = ROOT / "web"
OUT = ROOT / "publish"

# Страницы, которым нужен сервер: вход подразделения, загрузка файла,
# список загрузок, журнал событий и проверка загруженного документа.
SERVER_PAGES = {"upload.html", "uploads.html", "journal.html", "review.html"}

# Скрипты серверных страниц: без самой страницы они не нужны, а логин
# и загрузку в публичной копии держать незачем.
SERVER_ASSETS = {"review.js"}

# Страницы, которые публикуются.
PUBLIC_PAGES = ["index.html", "overview.html", "monitoring.html", "dashboard.html"]

NOTE_ID = "staticNote"


def fail(message: str) -> "NoReturn":  # type: ignore[name-defined]
    raise SystemExit("build_static: " + message)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    """Заменить ровно один фрагмент. Если его нет — сборка падает.

    Молча пропущенная замена — это страница, которая «собралась», но
    осталась с серверной кнопкой. Лучше громкий отказ, чем тихая поломка.
    """
    if text.count(old) != 1:
        fail(
            "фрагмент встречается %d раз вместо одного (%s): %r"
            % (text.count(old), label, old[:80])
        )
    return text.replace(old, new, 1)


# ---------------------------------------------------------------- данные


def build_snapshot() -> dict:
    """Снимок всех /api-ответов, которые нужны опубликованным страницам."""
    from backend import db, monitoring, science

    conn = db.connect()
    db.init_db(conn)
    try:
        db.seed_metrics(conn)
        db.seed_divisions(conn)
    except Exception:
        # База уже наполнена — повторный посев не обязателен.
        pass

    vitrine = db.load_vitrine(conn)

    def safe(label, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — снимок не должен падать целиком
            print("  ! %s: %s" % (label, exc))
            return {"available": False, "reason": str(exc)}

    health = safe("health", lambda: _health(conn))
    data = {
        "GET /api/metrics": vitrine,
        "GET /api/health": health,
        "GET /api/monitoring": safe("monitoring", monitoring.load),
        "GET /api/monitoring/card": safe("monitoring/card", monitoring.card),
        "GET /api/monitoring/directions": safe(
            "monitoring/directions", monitoring.directions
        ),
        "GET /api/monitoring/detail": safe("monitoring/detail", monitoring.detail),
        "GET /api/monitoring/spo": safe("monitoring/spo", monitoring.spo),
        "GET /api/science": safe("science", science.report),
        "POST /api/ask": _static_answer(),
        "POST /api/ask/crew": _static_answer(),
        "sources": _sources(conn, vitrine),
    }
    return data


def _health(conn) -> dict:
    """Состояние системы без путей к файлам и без моделей.

    На публичной странице незачем показывать список установленных моделей
    и железо машины ректора. Оставляем только счётчики и пометку, что
    версия статическая.
    """
    totals = {}
    for key, sql in (
        ("docs", "SELECT COUNT(*) FROM doc"),
        ("chunks", "SELECT COUNT(*) FROM doc_chunk"),
        ("vectors", "SELECT COUNT(*) FROM chunk_vec"),
        ("metrics", "SELECT COUNT(*) FROM dim_metric"),
        ("facts", "SELECT COUNT(*) FROM fact_metric"),
    ):
        try:
            totals[key] = conn.execute(sql).fetchone()[0]
        except Exception:
            totals[key] = 0
    return {
        "ok": True,
        "static": True,
        "generated_at": datetime.now().strftime("%d.%m.%Y"),
        "totals": totals,
        "ollama": {
            "available": False,
            "llm_installed": False,
            "llm_model": "статическая витрина",
        },
    }


def _static_answer() -> dict:
    return {
        "answer": (
            "Это публичная статическая витрина. Ассистент работает только "
            "в полной версии дашборда, где подключены документы вуза и "
            "языковая модель. Здесь доступны все показатели, графики и "
            "карточка мониторинга — откройте разделы «Обзор», «Мониторинг» "
            "и «Лента показателей»."
        ),
        "sources": [],
        "meta": {"model": "статическая витрина", "latency_ms": 0},
    }


def _sources(conn, vitrine: dict) -> dict:
    """Провенанс каждого показателя: тот же запрос, что в backend/app.py."""
    out = {}
    for metric in vitrine.get("metrics", []):
        metric_id = metric.get("id")
        if not metric_id:
            continue
        try:
            met = conn.execute(
                "SELECT metric_id, title, unit, source, src_note FROM dim_metric"
                " WHERE metric_id = ?",
                (metric_id,),
            ).fetchone()
            if not met:
                continue
            rows = conn.execute(
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
                    ORDER BY p.ord""",
                (metric_id,),
            ).fetchall()
            out[metric_id] = {"metric": dict(met), "values": [dict(r) for r in rows]}
        except Exception as exc:  # noqa: BLE001
            print("  ! sources %s: %s" % (metric_id, exc))
    return out


# ---------------------------------------------------------------- страницы


def strip_server_links(html: str) -> str:
    """Убрать ссылки на страницы, которых в статике нет."""
    pattern = re.compile(
        r'<a\b[^>]*href="/?(?:upload|uploads|journal|review)\.html"[^>]*>.*?</a>\s*',
        re.S,
    )
    return pattern.sub("", html)


def make_relative(html: str) -> str:
    """Абсолютные пути -> относительные (сайт живёт в подкаталоге)."""
    html = html.replace('href="/"', 'href="index.html"')
    return re.sub(r'(href|src)="/', r'\1="', html)


def inject_static_scripts(html: str) -> str:
    """Снимок и шим должны загрузиться раньше страничных скриптов."""
    head = (
        "\n  <!-- Статическая витрина: снимок API и перехват fetch -->"
        '\n  <script src="js/static-data.js"></script>'
        '\n  <script src="js/static-api.js"></script>'
    )
    return replace_once(html, "<head>", "<head>" + head, "head")


def inject_note(html: str, date_ru: str) -> str:
    note = (
        '<div id="%s" style="position:fixed;left:50%%;transform:translateX(-50%%);'
        "bottom:14px;z-index:9999;padding:7px 14px;border-radius:999px;"
        "background:rgba(15,23,42,.82);color:#e2e8f0;"
        "font:12px/1.4 system-ui,sans-serif;backdrop-filter:blur(8px);"
        'box-shadow:0 6px 18px rgba(0,0,0,.18);pointer-events:none">'
        "Статическая витрина · данные на %s · ассистент и загрузка данных "
        "отключены</div>\n" % (NOTE_ID, date_ru)
    )
    return replace_once(html, "</body>", note + "</body>", "body")


# ---------------------------------------------------------------- правки


def patch_index(html: str) -> str:
    """Лендинг: убрать вход подразделения, оставить вход в витрину."""
    new_card = (
        "      <!-- Карточка 2: мониторинг -->\n"
        '      <section class="land-card">\n'
        '        <div class="icon">\U0001F4CA</div>\n'
        "        <h2>Мониторинг</h2>\n"
        "        <p class=\"desc\">Карточка вуза как на monitoring.miccedu.ru: "
        "E-показатели, места и перцентили, сравнение с медианами региона и РФ, "
        "СПО.</p>\n"
        '        <a class="land-btn call" href="monitoring.html">'
        "Открыть мониторинг \u2192</a>\n"
        "      </section>\n"
    )
    html = re.sub(
        r"      <!-- Карточка 2:.*?</section>\n", new_card, html, count=1, flags=re.S
    )
    # Скрипт входа и проверки «уже вошли» относится к серверу.
    html = re.sub(
        r"      // ---------- Уже вошли\? ----------.*?(?=      function esc\(s\))",
        "",
        html,
        flags=re.S,
    )
    html = replace_once(
        html,
        'txt.textContent = "сервер онлайн";',
        'txt.textContent = "статическая витрина";',
        "index: статус онлайн",
    )
    html = replace_once(
        html,
        'txt.textContent = "сервер офлайн — запустите start.bat";',
        'txt.textContent = "статическая витрина";',
        "index: статус офлайн",
    )
    html = replace_once(
        html,
        "· данные из документов университета и загрузок подразделений",
        "· статическая витрина: данные вшиты в страницу, загрузка файлов "
        "и ассистент доступны в полной версии",
        "index: подпись в подвале",
    )
    return html


def patch_dashboard(html: str) -> str:
    html = replace_once(
        html,
        " · документы в ответах ИИ — настоящие, из папки knowledge",
        " · статическая витрина: данные вшиты в страницу",
        "dashboard: подпись в подвале",
    )
    return html


def patch_js(files: dict[str, str]) -> dict[str, str]:
    """Правки в копиях скриптов: честный статус и отключение серверных кнопок."""
    app = files["js/app.js"]
    app = replace_once(
        app,
        '    el.className = "sys-status";\n    if (h && h.offline) {',
        '    el.className = "sys-status";\n'
        "    if (h && h.static) {\n"
        '      el.classList.add("is-warn");\n'
        '      el.textContent = "статическая витрина";\n'
        '      el.title = "Публичная версия без сервера: данные вшиты в страницу, '
        'ассистент отвечает заготовкой";\n'
        "      return;\n"
        "    }\n"
        "    if (h && h.offline) {",
        "app: статус системы",
    )
    app = replace_once(
        app,
        '    if (!el) return;\n    var o = (h && h.ollama) || {};',
        "    if (!el) return;\n"
        "    if (h && h.static) {\n"
        '      el.textContent = "Статическая витрина";\n'
        '      el.title = "Публичная версия без сервера: ассистент отвечает заготовкой";\n'
        '      el.style.color = "var(--amber, #b45309)";\n'
        "      return;\n"
        "    }\n"
        "    var o = (h && h.ollama) || {};",
        "app: статус ассистента",
    )
    app = replace_once(
        app,
        'window.location.href = "/api/export/xlsx";',
        'showToast("Excel-экспорт доступен только в полной версии с сервером", "warn");',
        "app: кнопка XLSX",
    )
    app = replace_once(
        app,
        'showToast("Excel собирается на сервере — начнётся загрузка файла", "ok");',
        "",
        "app: тост XLSX",
    )
    app = replace_once(
        app,
        'showToast("Данные перечитаны из базы", "ok");',
        'showToast("Данные перечитаны из вшитого снимка", "ok");',
        "app: тост обновления",
    )
    files["js/app.js"] = app

    overview = files["js/overview.js"]
    overview = replace_once(
        overview,
        '["Сервер", h && h.totals ? "онлайн" : "не отвечает", '
        'h && h.totals ? "text-emerald-600" : "text-rose-600"],',
        '["Режим", h && h.static ? "статическая витрина" : '
        '(h && h.totals ? "онлайн" : "не отвечает"), '
        'h && h.static ? "text-amber-600" : '
        '(h && h.totals ? "text-emerald-600" : "text-rose-600")],',
        "overview: строка статуса",
    )
    overview = replace_once(
        overview,
        'dot.className = "h-2 w-2 rounded-full " + '
        '(h && h.totals ? "bg-emerald-500" : "bg-rose-500");',
        'dot.className = "h-2 w-2 rounded-full " + '
        '(h && h.static ? "bg-amber-400" : '
        '(h && h.totals ? "bg-emerald-500" : "bg-rose-500"));',
        "overview: индикатор в рейле",
    )
    files["js/overview.js"] = overview

    mon = files["js/monitoring.js"]
    mon = replace_once(
        mon,
        '$("railStatus").className = "h-2 w-2 rounded-full " + '
        '(h && h.totals ? "bg-emerald-500" : "bg-rose-500");',
        '$("railStatus").className = "h-2 w-2 rounded-full " + '
        '(h && h.static ? "bg-amber-400" : '
        '(h && h.totals ? "bg-emerald-500" : "bg-rose-500"));',
        "monitoring: индикатор в рейле",
    )
    files["js/monitoring.js"] = mon
    return files


# ---------------------------------------------------------------- шим


STATIC_API_JS = r"""/* static-api.js — ответы на /api/... из вшитого снимка.
 *
 * Зачем: страницы дашборда написаны под сервер и зовут /api/metrics,
 * /api/monitoring и другие адреса. На GitHub Pages сервера нет, и без
 * перехвата каждый такой вызов давал бы 404, а экран — пустоту.
 *
 * Шим подменяет window.fetch только для путей /api/ и отдаёт данные из
 * static-data.js. Всё остальное уходит в настоящий fetch.
 */
(function () {
  "use strict";

  var DATA = window.__STATIC_API__ || {};
  var realFetch = window.fetch ? window.fetch.bind(window) : null;

  function jsonResponse(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  function pathOf(url) {
    var q = url.indexOf("?");
    var path = q >= 0 ? url.slice(0, q) : url;
    var m = path.match(/^https?:\/\/[^/]+(\/.*)?$/);
    if (m) path = m[1] || "/";
    return path;
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = pathOf(url);

    if (path.indexOf("/api/") !== 0) {
      if (realFetch) return realFetch(input, init);
      return Promise.reject(new Error("fetch недоступен"));
    }

    var method = String(
      (init && init.method) || (input && input.method) || "GET"
    ).toUpperCase();

    var body;
    if (DATA[method + " " + path] !== undefined) {
      body = DATA[method + " " + path];
    } else if (method === "GET" && DATA[path] !== undefined) {
      body = DATA[path];
    } else {
      var src = path.match(/^\/api\/metrics\/([^/]+)\/sources$/);
      var id = src ? decodeURIComponent(src[1]) : null;
      if (id && DATA.sources && DATA.sources[id]) {
        body = DATA.sources[id];
      }
    }

    if (body === undefined) {
      return Promise.resolve(jsonResponse({
        detail: "Статическая витрина: этот раздел доступен только в полной версии"
      }, 404));
    }
    return Promise.resolve(jsonResponse(body, 200));
  };
})();
"""


# ---------------------------------------------------------------- сборка


def main() -> None:
    date_ru = datetime.now().strftime("%d.%m.%Y")

    if not WEB.is_dir():
        fail("нет каталога web/")

    print("1/6 снимок API")
    snapshot = build_snapshot()

    print("2/6 очистка publish/")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    print("3/6 копирование web/")
    for src in WEB.rglob("*"):
        rel = src.relative_to(WEB)
        if src.is_dir():
            continue
        if rel.parts[0] in ("knowledge",):
            continue
        if src.name in SERVER_PAGES or src.name in SERVER_ASSETS:
            continue
        dst = OUT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    print("4/6 снимок и шим")
    (OUT / "js").mkdir(exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    (OUT / "js" / "static-data.js").write_text(
        "window.__STATIC_API__ = " + payload + ";\n", encoding="utf-8"
    )
    (OUT / "js" / "static-api.js").write_text(STATIC_API_JS, encoding="utf-8")

    print("5/6 правка страниц")
    for name in PUBLIC_PAGES:
        path = OUT / name
        if not path.exists():
            fail("страница не найдена: " + name)
        html = path.read_text(encoding="utf-8")
        html = strip_server_links(html)
        if name == "index.html":
            html = patch_index(html)
        elif name == "dashboard.html":
            html = patch_dashboard(html)
        html = make_relative(html)
        html = inject_static_scripts(html)
        html = inject_note(html, date_ru)
        path.write_text(html, encoding="utf-8")

    print("6/6 правка скриптов")
    js_files = {}
    for name in ("js/app.js", "js/overview.js", "js/monitoring.js"):
        js_files[name] = (OUT / name).read_text(encoding="utf-8")
    js_files = patch_js(js_files)
    for name, text in js_files.items():
        (OUT / name).write_text(text, encoding="utf-8")

    # .nojekyll: без него GitHub Pages прогоняет сборку через Jekyll и
    # выбрасывает файлы, начинающиеся с подчёркивания.
    (OUT / ".nojekyll").write_text("", encoding="utf-8")

    total = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print("\npublish/ готов: %d файлов, %.1f МБ" % (
        sum(1 for p in OUT.rglob("*") if p.is_file()), total / 1024 / 1024))
    print("Страницы: " + ", ".join(PUBLIC_PAGES))


if __name__ == "__main__":
    main()
