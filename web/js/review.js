/* ============================================================
   review.js — экран проверки загрузки (ТЗ «Дашборд ректора», §10)

   Здесь заканчивается машина и начинается человек: карточка разбора,
   автоматические проверки, конфликты с уже подтверждёнными числами,
   объяснение большой модели и три решения — «сохранить правки»,
   «принять и опубликовать», «отклонить».

   Публикация возможна только через кнопку «Принять» (POST
   /api/upload/{id}/decide): приём файла на витрину чисел больше не
   влияет — см. web/upload.html.

   Без сессии эндпоинты отвечают 401, поэтому вход обязателен.
   ============================================================ */
(function () {
  "use strict";

  var RAW_PAGE = 30;              // строк сырья за один показ
  var state = {
    id: null,
    data: null,                   // ответ GET /api/upload/{id}/review
    card: null,                   // рабочая копия card (правится здесь)
    raw: [],
    rawTotal: 0,
    rawShown: 0,
    conflictRetried: false,       // авто-повтор после 409 conflict — один раз
    decideMsg: null,              // {color, text} — живёт между перерисовками
    comment: ""
  };

  // ---------- мелкие помощники -------------------------------------
  function el(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function sv(v) { return v == null ? "" : String(v); }
  function short(s, n) { s = sv(s); return s.length > n ? s.slice(0, n) + "…" : s; }

  function fmtSize(n) {
    if (n == null || isNaN(n)) return "—";
    n = Number(n);
    if (n < 1024) return n + " Б";
    if (n < 1048576) return (n / 1024).toFixed(1).replace(".", ",") + " КБ";
    return (n / 1048576).toFixed(1).replace(".", ",") + " МБ";
  }

  // Статусы загрузки — теми же словами, что в списке загрузок.
  var STATUS = {
    needs_review: { label: "Ждёт проверки", cls: "status-yellow" },
    accepted:     { label: "Опубликовано",  cls: "status-green" },
    rejected:     { label: "Отклонено",     cls: "gray" },
    failed:       { label: "Ошибка приёма", cls: "status-red" },
    duplicate:    { label: "Дубликат",      cls: "gray" },
    ok:           { label: "Принято",       cls: "status-green" }
  };
  var ACCESS = { open: "Открыто", internal: "Служебное", confidential: "Конфиденциальное" };
  var CARD_STATUS = { draft: "черновик", corrected: "исправлен вручную" };

  function statusHtml(status) {
    var s = STATUS[status] || { label: sv(status) || "—", cls: "gray" };
    if (s.cls === "gray") {
      return '<span class="status-pill" style="background:var(--surface-2);color:var(--muted)">' +
        esc(s.label) + "</span>";
    }
    return '<span class="status-pill ' + s.cls + '">' + esc(s.label) + "</span>";
  }

  function row(label, valueHtml) {
    return '<div class="form-group"><label>' + label +
      '</label><div style="font-size:14px">' + valueHtml + "</div></div>";
  }

  // ---------- сеть --------------------------------------------------
  function getJSON(path) {
    return fetch(path, { headers: { "Accept": "application/json" } })
      .then(function (r) {
        return r.json().catch(function () { return null; })
          .then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
      })
      .catch(function () { return { ok: false, status: 0, data: null }; });
  }

  function postJSON(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return null; })
        .then(function (d) { return { ok: r.ok, status: r.status, data: d }; });
    }).catch(function () { return { ok: false, status: 0, data: null }; });
  }

  // ---------- загрузка состояния ------------------------------------
  function load() {
    return getJSON("/api/upload/" + state.id + "/review").then(function (res) {
      var m = el("msg");
      if (res.status === 401) { showAuth(); return; }
      if (res.status === 403) {
        m.innerHTML = '<span style="color:var(--red)">Это загрузка другого подразделения</span>';
        el("reviewBox").style.display = "none";
        return;
      }
      if (res.status === 404) {
        m.innerHTML = '<span style="color:var(--red)">Загрузка не найдена</span>';
        return;
      }
      if (!res.ok || !res.data || !res.data.upload) {
        m.innerHTML = '<span style="color:var(--red)">' +
          (res.status === 0 ? "Ошибка связи с сервером"
                            : "Не удалось получить состояние проверки (HTTP " + res.status + ")") +
          "</span>";
        return;
      }
      m.textContent = "";
      var d = res.data;
      state.data = d;
      state.card = JSON.parse(JSON.stringify(d.card || {}));
      if (!Array.isArray(state.card.mapping)) state.card.mapping = [];
      if (!Array.isArray(state.card.unmapped)) state.card.unmapped = [];
      state.raw = (d.raw || []).slice();
      state.rawTotal = Number(d.rawTotal) || state.raw.length;
      state.rawShown = Math.min(RAW_PAGE, state.raw.length);
      state.conflictRetried = false;
      render();
    });
  }

  function render() {
    renderUpload();
    renderQuestionnaire();
    renderCardBlock();
    renderChecks();
    renderExplain();
    renderConflicts();
    renderRaw();
    renderDecide();
  }

  // ---------- 1. Загрузка -------------------------------------------
  function renderUpload() {
    var u = state.data.upload, box = el("uploadCard");
    box.style.display = "block";
    var h = '<div class="card-head"><h3>Загрузка</h3><span>' + statusHtml(u.status) + "</span></div>";
    h += '<div class="form-grid">' +
      row("Файл", esc(u.filename)) +
      row("Размер", esc(fmtSize(u.sizeBytes))) +
      row("Загружено", esc(u.uploadedAt || "—")) +
      row("Кто загрузил", esc(u.uploadedBy || "—")) +
      row("Подразделение", esc(u.divisionTitle || u.divisionId || "—")) +
      row("Тип файла", esc(u.extension || "—") +
        (u.mime ? ' <span class="muted" style="font-size:12px">' + esc(u.mime) + "</span>" : "")) +
      '<div class="form-group"><label>SHA-256</label>' +
      '<code title="' + esc(u.sha256) + '" style="font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-all">' +
      esc(short(u.sha256, 18)) + "</code></div>" +
      "</div>";
    if (u.error) {
      h += '<p style="color:var(--red);font-size:13px;margin-top:8px">Ошибка приёма: ' +
        esc(u.error) + "</p>";
    }
    box.innerHTML = h;
  }

  // ---------- 2. Анкета ---------------------------------------------
  function renderQuestionnaire() {
    var q = state.data.questionnaire || {}, box = el("qnCard");
    box.style.display = "block";
    var titles = {};
    (state.data.metrics || []).forEach(function (m) { titles[m.id] = m.title; });
    var ids = (q.metricIds || []).map(function (id) {
      return titles[id] || id;
    });

    var h = '<div class="card-head"><h3>Анкета</h3>' +
      '<span class="muted" style="font-size:12px">' +
      (q.confirmed ? "числа подтверждены" : "числа ещё не подтверждены") + "</span></div>";
    h += '<div class="form-grid">' +
      row("Что это за данные", esc(q.title || "—")) +
      row("Период", esc(q.period || "—")) +
      row("Как часто обновляются", esc(q.frequency || "разово")) +
      row("Ответственный", esc(q.responsible || "—")) +
      row("Уровень доступа", esc(ACCESS[q.accessLevel] || q.accessLevel || "—")) +
      row("Следующее обновление", esc(q.nextExpected || "—")) +
      row("Карточки дашборда", ids.length
        ? '<div style="font-size:13px">' + esc(ids.join(", ")) + "</div>"
        : '<span class="muted">не выбраны</span>') +
      "</div>";
    if (q.purpose) {
      h += '<p class="sub" style="margin-top:10px"><b>Зачем данные:</b> ' + esc(q.purpose) + "</p>";
    }
    box.innerHTML = h;
  }

  // ---------- 3. Карточка разбора ------------------------------------
  function rawLabel(ref) {
    if (ref == null) return null;
    for (var i = 0; i < state.raw.length; i++) {
      if (state.raw[i].raw_id === ref) return state.raw[i].label;
    }
    return null;
  }

  function sourceHtml(m) {
    var base;
    if (m.source === "questionnaire") base = "из анкеты";
    else if (m.source === "model") base = "нашла модель";
    else base = "вручную";
    var extra = "";
    if (m.modelValue != null && sv(m.modelValue) !== "" &&
        sv(m.modelValue) !== sv(m.value)) {
      extra = ' <span class="muted" style="font-size:12px">модель: ' + esc(m.modelValue) + "</span>";
    }
    return esc(base) + extra;
  }

  function renderCardBlock() {
    var d = state.data, c = state.card, box = el("cardCard");
    box.style.display = "block";

    var meta = '<span class="muted" style="font-size:12px">' +
      esc(CARD_STATUS[c.status] || c.status || "черновик") +
      (c.model ? " · модель " + esc(c.model) : "") +
      (c.attempts != null ? " · попыток " + esc(c.attempts) : "") +
      (c.parseMs != null ? " · разбор " + esc(c.parseMs) + " мс" : "") +
      "</span>";
    var h = '<div class="card-head"><h3>Карточка разбора</h3>' + meta + "</div>";
    if (c.parseError) {
      h += '<p style="color:var(--red);font-size:13px">Ошибка разбора: ' + esc(c.parseError) + "</p>";
    }
    if (c.editedAt) {
      h += '<p class="hint">Правил ' + esc(c.editedBy || "—") + " · " + esc(c.editedAt) + "</p>";
    }

    var opts = (d.periods || []).map(function (p) {
      return '<option value="' + esc(p.label || p.period_id) + '"></option>';
    }).join("");

    h += '<div class="form-grid">' +
      '<div class="form-group"><label>Тип документа</label>' +
      '<input type="text" id="docTypeInput" maxlength="120" value="' + esc(c.docType || "") +
      '" placeholder="например, Отчёт за март" /></div>' +
      '<div class="form-group"><label>Период публикации</label>' +
      '<input type="text" id="periodInput" maxlength="80" list="periodList" value="' +
      esc(c.period || "") + '" placeholder="например, Март 2026" />' +
      '<datalist id="periodList">' + opts + "</datalist>" +
      '<p class="hint">Периодом витрины или своей строкой — по нему уйдут числа.</p></div>' +
      "</div>";

    // --- таблица сопоставлений ---
    var used = {};
    (c.mapping || []).forEach(function (m) { if (m.metricId) used[m.metricId] = true; });
    var rest = (d.metrics || []).filter(function (m) { return !used[m.id]; });

    h += '<div class="table-wrap" style="margin-top:14px"><table class="data">' +
      "<thead><tr><th>Показатель</th><th>Единица</th><th>Значение</th>" +
      "<th>Источник</th><th>Привязка к сырью</th><th></th></tr></thead>" +
      "<tbody id=\"mapTbody\">";

    (c.mapping || []).forEach(function (m, i) {
      var ref = rawLabel(m.rawRef);
      h += "<tr>" +
        "<td>" + esc(m.metricTitle || m.metricId || "—") +
        (m.metricId ? "" : ' <span class="muted">(без показателя)</span>') + "</td>" +
        "<td>" + esc(m.unit || "—") + "</td>" +
        "<td><input type=\"text\" class=\"map-value\" data-i=\"" + i +
        "\" value=\"" + esc(m.value) + "\" style=\"width:120px;padding:7px 9px;" +
        "border:1px solid var(--border);border-radius:8px;background:var(--surface);" +
        "color:var(--text);font-size:14px\" /></td>" +
        "<td>" + sourceHtml(m) + "</td>" +
        "<td>" + (ref ? '<code style="font-family:ui-monospace,Consolas,monospace;font-size:12px">' +
          esc(ref) + "</code>"
          : (m.rawRef != null ? '<span class="muted" style="font-size:12px">сырьё #' +
            esc(m.rawRef) + "</span>" : '<span class="muted">—</span>')) + "</td>" +
        "<td style=\"white-space:nowrap\"><button type=\"button\" class=\"btn btn-ghost btn-small row-del\" data-i=\"" +
        i + "\" title=\"Удалить строку\">✕</button></td>" +
        "</tr>";
    });
    h += "</tbody></table></div>";
    if (!(c.mapping || []).length) {
      h += '<p class="muted" style="font-size:13px;margin-top:8px">Показатели не сопоставлены — ' +
        "добавьте строки ниже или проверьте разбор файла.</p>";
    }

    // --- добавление строки ---
    h += '<div class="form-grid" style="margin-top:14px">' +
      '<div class="form-group"><label>Добавить строку</label>' +
      '<select id="addMetric">' +
      (rest.length
        ? rest.map(function (m) {
            return '<option value="' + esc(m.id) + '">' +
              esc(m.title) + (m.unit ? " (" + esc(m.unit) + ")" : "") + "</option>";
          }).join("")
        : '<option value="">все показатели уже в таблице</option>') +
      "</select></div>" +
      '<div class="form-group"><label>Значение</label>' +
      '<input type="text" id="addValue" placeholder="например, 412" /></div>' +
      '<div class="form-group"><label>&nbsp;</label>' +
      '<button type="button" class="btn btn-outline" id="btnAddRow"' +
      (rest.length ? "" : " disabled") + ">Добавить строку</button></div>" +
      "</div>";

    // --- не сопоставлено ---
    if ((c.unmapped || []).length) {
      h += '<div style="margin-top:14px;padding:12px 14px;border:1px solid var(--border);' +
        'border-radius:var(--radius-sm);background:var(--surface-2)">' +
        '<strong style="font-size:13px">Не сопоставлено (' + c.unmapped.length + ")</strong>" +
        '<p class="hint" style="margin-top:2px">Строки, в которых не нашли показатель витрины: ' +
        "они останутся в документе, но в числа дашборда не попадут.</p>" +
        '<ul style="list-style:none;margin-top:8px;display:flex;flex-direction:column;gap:5px">';
      c.unmapped.forEach(function (u) {
        h += '<li style="font-size:13px"><b>' + esc(u.label) + "</b> — " + esc(u.value) +
          ' <span class="muted" style="font-size:12px">(' + esc(u.reason || "без показателя") +
          ")</span></li>";
      });
      h += "</ul></div>";
    }

    box.innerHTML = h;

    var dt = el("docTypeInput");
    dt.addEventListener("input", function () { state.card.docType = this.value; });
    var pi = el("periodInput");
    pi.addEventListener("input", function () {
      // Текст периода изменён: прежний periodId мог указывать на другой
      // период, чем видит человек — пусть сервер определит по тексту.
      if (this.value !== (state.data.card.period || "")) state.card.periodId = null;
      state.card.period = this.value;
    });

    var tb = el("mapTbody");
    tb.addEventListener("input", function (e) {
      var t = e.target;
      if (t && t.classList && t.classList.contains("map-value")) {
        var i = Number(t.dataset.i);
        if (state.card.mapping[i]) state.card.mapping[i].value = t.value;
      }
    });
    tb.addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.classList && t.classList.contains("row-del")) {
        state.card.mapping.splice(Number(t.dataset.i), 1);
        renderCardBlock();
      }
    });

    var add = el("btnAddRow");
    if (add) add.addEventListener("click", function () {
      var sel = el("addMetric");
      if (!sel || !sel.value) return;
      var found = null;
      (d.metrics || []).forEach(function (m) { if (m.id === sel.value) found = m; });
      if (!found) return;
      var v = el("addValue");
      state.card.mapping.push({
        metricId: found.id, metricTitle: found.title, unit: found.unit,
        value: (v && v.value.trim()) || "", source: "manual",
        confidence: null, rawRef: null, label: found.title, modelValue: null
      });
      renderCardBlock();
    });
  }

  // ---------- 4. Проверки -------------------------------------------
  function renderChecks() {
    var d = state.data, box = el("checksCard");
    box.style.display = "block";
    var s = d.summary || {};
    var list = d.checks || [];

    var h = '<div class="card-head"><h3>Проверки</h3><span style="font-size:13px">' +
      '<b style="color:var(--red)">Ошибок: ' + (s.errors || 0) + "</b>" +
      '<span class="muted"> · </span>' +
      '<b style="color:var(--yellow)">Предупреждений: ' + (s.warnings || 0) + "</b></span></div>";

    if (!list.length) {
      h += '<p class="muted" style="font-size:13px">Проверок нет — файл прошёл все сверки без замечаний.</p>';
    } else {
      h += '<ul style="list-style:none;display:flex;flex-direction:column;gap:8px;margin-top:4px">';
      list.forEach(function (c) {
        var err = c.severity === "error";
        h += '<li style="border:1px solid var(--border);border-left:4px solid ' +
          (err ? "var(--red)" : "var(--amber)") +
          ";border-radius:8px;padding:8px 10px;background:var(--surface-2)\">" +
          '<span class="status-pill ' + (err ? "status-red" : "status-yellow") + '">' +
          (err ? "блокирует" : "предупреждение") + "</span> " +
          '<span style="font-size:13px">' + esc(c.message || c.check_code) + "</span>";
        if (c.field || c.value_a != null || c.value_b != null) {
          h += '<div class="muted" style="font-size:12px;margin-top:4px">' +
            (c.field ? "поле: " + esc(c.field) : "") +
            (c.value_a != null || c.value_b != null
              ? (c.field ? " · " : "") + esc(sv(c.value_a)) + " ≠ " + esc(sv(c.value_b))
              : "") +
            "</div>";
        }
        h += "</li>";
      });
      h += "</ul>";
    }
    box.innerHTML = h;
  }

  // ---------- 5. Объяснение ИИ --------------------------------------
  function renderExplain() {
    var d = state.data, box = el("explCard");
    box.style.display = "block";
    var e = d.explanation || {};
    var st = d.explanationStatus || {};

    var h = '<div class="card-head"><h3>Объяснение ИИ</h3>' +
      '<span class="muted" style="font-size:12px">' +
      esc(e.model || st.model || "большая модель") + "</span></div>";

    if (e.available) {
      h += '<div style="white-space:pre-wrap;font-size:13.5px;line-height:1.55">' +
        esc(e.text) + "</div>";
      h += '<p class="hint">модель ' + esc(e.model || "—") +
        (e.ms ? " · " + esc(e.ms) + " мс" : "") +
        (e.explainedAt ? " · " + esc(e.explainedAt) : "") + "</p>";
    } else {
      h += '<p class="muted" style="font-size:13px">Причина: ' +
        esc(e.reason || st.note || "объяснение недоступно") + "</p>";
      h += '<div class="page-actions" style="margin-top:10px">' +
        '<button type="button" class="btn btn-outline" id="btnExplain">Запросить объяснение</button>' +
        '<span id="explainMsg" class="muted" style="align-self:center;font-size:13px"></span></div>';
    }
    box.innerHTML = h;

    var b = el("btnExplain");
    if (b) b.addEventListener("click", function () {
      b.disabled = true;
      b.textContent = "Запрашиваю…";
      var m = el("explainMsg");
      if (m) m.textContent = "Большая модель отвечает — может занять минуту.";
      postJSON("/api/upload/" + state.id + "/explain", {}).then(function (res) {
        var d2 = res.data || {};
        if (res.ok && d2.available) { state.data.explanation = d2; renderExplain(); return; }
        renderExplain();
        var m2 = el("explainMsg");
        if (m2) {
          m2.style.color = "var(--red)";
          m2.textContent = res.ok
            ? (d2.reason || "объяснение недоступно")
            : (res.status === 0 ? "Ошибка связи с сервером"
                                : "Сервер не ответил (HTTP " + res.status + ")");
        }
      });
    });
  }

  // ---------- 6. Конфликты ------------------------------------------
  function renderConflicts() {
    var d = state.data, box = el("conflictCard");
    var cf = d.conflicts || [], rp = d.replaced || [];
    if (!cf.length && !rp.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "block";

    var h = '<div class="card-head"><h3>Конфликты</h3>' +
      '<span class="muted" style="font-size:12px">перезапись: ' + cf.length + "</span></div>";
    if (cf.length) {
      h += '<p class="sub">Эти значения уже подтверждены другой загрузкой. ' +
        "Отметьте, какие можно перезаписать — они уходят в подтверждение при «Принять».</p>";
      h += '<div class="table-wrap"><table class="data"><thead><tr>' +
        "<th></th><th>Показатель</th><th>Было</th><th>Станет</th><th>Откуда прежнее</th>" +
        "</tr></thead><tbody>";
      cf.forEach(function (c) {
        h += '<tr><td style="width:34px"><input type="checkbox" class="conflict-chk" checked data-metric="' +
          esc(c.metricId) + '" /></td>' +
          "<td>" + esc(c.title || c.metricId) + "</td>" +
          "<td>" + esc(sv(c.existingValue)) + "</td>" +
          '<td><b style="color:var(--primary)">' + esc(sv(c.proposed)) + "</b></td>" +
          '<td class="muted" style="font-size:12px">' +
          esc(c.fromFile || (c.fromUpload != null ? "загрузка #" + c.fromUpload : "—")) + "</td></tr>";
      });
      h += "</tbody></table></div>";
    }
    if (rp.length) {
      h += '<p class="hint" style="margin-top:10px">Будут заменены: ' +
        rp.map(function (r) {
          return esc(r.title || r.metricId) + " (" + esc(sv(r.existingValue)) + " → " +
            esc(sv(r.proposed)) + (r.reason ? ", " + esc(r.reason) : "") + ")";
        }).join("; ") + ".</p>";
    }
    box.innerHTML = h;
  }

  // ---------- 7. Сырьё документа -------------------------------------
  function renderRaw() {
    var box = el("rawCard");
    if (!state.raw.length) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "block";
    var shown = state.raw.slice(0, state.rawShown);

    var h = '<div class="card-head"><h3>Сырьё документа</h3>' +
      '<span class="muted" style="font-size:12px">показано ' + shown.length +
      " из " + state.rawTotal + "</span></div>";
    h += '<div style="display:flex;flex-direction:column">';
    shown.forEach(function (r) {
      h += '<div style="display:flex;gap:10px;padding:5px 0;border-bottom:1px dashed var(--border);font-size:12px">' +
        '<span style="flex:none;min-width:170px;color:var(--muted);font-family:ui-monospace,Consolas,monospace">' +
        esc(r.label || r.kind || "—") + "</span>" +
        '<span style="font-family:ui-monospace,Consolas,monospace;word-break:break-all">' +
        esc(r.text) + "</span></div>";
    });
    h += "</div>";
    if (state.rawShown < state.raw.length || state.raw.length < state.rawTotal) {
      h += '<div class="page-actions" style="margin-top:10px">' +
        '<button type="button" class="btn btn-outline" id="btnRawMore">Показать ещё</button></div>';
    }
    box.innerHTML = h;

    var b = el("btnRawMore");
    if (b) b.addEventListener("click", showMoreRaw);
  }

  function showMoreRaw() {
    if (state.rawShown < state.raw.length) {
      state.rawShown = Math.min(state.rawShown + RAW_PAGE, state.raw.length);
      renderRaw();
      return;
    }
    if (state.raw.length >= state.rawTotal) return;
    var b = el("btnRawMore");
    if (b) { b.disabled = true; b.textContent = "Загружаю…"; }
    getJSON("/api/upload/" + state.id + "/raw?offset=" + state.raw.length + "&limit=300")
      .then(function (res) {
        if (res.ok && res.data && Array.isArray(res.data.items)) {
          state.raw = state.raw.concat(res.data.items);
          if (res.data.total) state.rawTotal = res.data.total;
        }
        state.rawShown = Math.min(state.rawShown + RAW_PAGE, state.raw.length);
        renderRaw();
      });
  }

  // ---------- 8. Решение --------------------------------------------
  function cardPayload() {
    var c = state.card;
    return {
      docType: c.docType,
      period: c.period,
      periodId: c.periodId,
      mapping: c.mapping || [],
      unmapped: c.unmapped || []
    };
  }

  function overwriteIds() {
    return Array.prototype.slice
      .call(document.querySelectorAll(".conflict-chk"))
      .filter(function (cb) { return cb.checked; })
      .map(function (cb) { return cb.getAttribute("data-metric"); })
      .filter(function (id) { return !!id; });
  }

  function renderDecide() {
    var d = state.data, box = el("decideCard");
    box.style.display = "block";
    var st = d.upload.status;
    var can = !!(d.summary && d.summary.canAccept);
    var active = st === "needs_review";

    var h = '<div class="card-head"><h3>Решение</h3><span>' + statusHtml(st) + "</span></div>";
    if (!active) {
      h += '<p class="hint">Загрузка уже решена — новых решений по ней не принимается.</p>';
    }
    h += '<div class="form-group" style="margin-top:6px"><label>Комментарий к решению</label>' +
      '<textarea id="commentInput" rows="2" maxlength="500" ' +
      'placeholder="например, сверено с бухгалтерской выпиской"></textarea></div>';
    h += '<div class="page-actions" style="margin-top:14px">' +
      '<button type="button" class="btn btn-outline" id="btnFix"' + (active ? "" : " disabled") +
      ">Сохранить правки</button>" +
      '<button type="button" class="btn btn-primary" id="btnAccept"' +
      (active && can ? "" : " disabled") + ">Принять и опубликовать</button>" +
      '<button type="button" class="btn btn-outline" id="btnReject"' + (active ? "" : " disabled") +
      ' style="color:var(--red);border-color:var(--red)">Отклонить</button>' +
      '<a href="uploads.html" class="btn btn-ghost">К списку загрузок</a>' +
      "</div>";
    if (active && !can) {
      h += '<p class="hint" style="color:var(--red)">«Принять» выключено: есть блокирующие ' +
        "ошибки. Сначала исправьте карточку или сами ошибки — см. блок «Проверки».</p>";
    }
    if (state.decideMsg) {
      h += '<p style="margin-top:10px;font-size:13px;color:' + state.decideMsg.color + '">' +
        esc(state.decideMsg.text) + "</p>";
    }
    h += '<p class="hint">Комментарий уходит в журнал решений. Числа попадут в витрину ' +
      "после кнопки «Принять».</p>";
    box.innerHTML = h;

    var ci = el("commentInput");
    ci.value = state.comment || "";
    ci.addEventListener("input", function () { state.comment = this.value; });
    el("btnFix").addEventListener("click", function () { decide("fix"); });
    el("btnAccept").addEventListener("click", function () { decide("accept"); });
    el("btnReject").addEventListener("click", function () { decide("reject"); });
  }

  var BTN_IDS = ["btnFix", "btnAccept", "btnReject"];
  var BUSY_LABEL = { accept: "Публикуется…", fix: "Сохраняю…", reject: "Отклоняю…" };

  function setBusy(on, action) {
    BTN_IDS.forEach(function (id) {
      var b = el(id);
      if (!b) return;
      b.disabled = on;
      if (on && id === "btn" + action.charAt(0).toUpperCase() + action.slice(1)) {
        b.textContent = BUSY_LABEL[action] || "Выполняется…";
      } else if (!on) {
        // Кнопки перерисовываются из renderDecide — здесь только вернуть текст.
        b.textContent = { btnFix: "Сохранить правки", btnAccept: "Принять и опубликовать",
                          btnReject: "Отклонить" }[id];
      }
    });
  }

  function decide(action) {
    if (state.busy) return;
    if (action === "reject" &&
        !window.confirm("Отклонить загрузку? Числа из неё не будут опубликованы.")) return;

    var body = { action: action, comment: (state.comment || "").trim() };
    if (action === "accept") { body.card = cardPayload(); body.overwrite = overwriteIds(); }
    if (action === "fix") body.card = cardPayload();

    state.busy = true;
    setBusy(true, action);

    postJSON("/api/upload/" + state.id + "/decide", body).then(function (res) {
      state.busy = false;
      setBusy(false, action);          // на случай, если ветка ниже не перерисует
      var d = res.data || {};
      var detail = (typeof d.detail === "object" && d.detail) ? d.detail : null;

      if (res.status === 401) { showAuth(); return; }

      if (res.status === 409 && detail) {
        if (detail.code === "blocking") {
          state.decideMsg = {
            color: "var(--red)",
            text: detail.message + " — ошибок: " + (detail.checks || []).length + "."
          };
          load();                       // перечитать: canAccept станет false
          return;
        }
        if (detail.code === "conflict") {
          state.data.conflicts = detail.conflicts || [];
          renderConflicts();
          if (!state.conflictRetried) {
            state.conflictRetried = true;
            state.decideMsg = {
              color: "var(--muted)",
              text: "Нужно подтвердить перезапись уже подтверждённых значений — " +
                    "повторяю запрос с отмеченными."
            };
            renderDecide();
            decide("accept");
            return;
          }
          state.decideMsg = {
            color: "var(--red)",
            text: detail.message + " Отметьте нужные строки в блоке «Конфликты» и повторите «Принять»."
          };
          renderDecide();
          return;
        }
        if (detail.code === "no_period") {
          state.decideMsg = { color: "var(--red)", text: detail.message };
          renderDecide();
          var p = el("periodInput");
          if (p) { p.focus(); p.scrollIntoView({ block: "center" }); }
          return;
        }
      }

      if (!res.ok) {
        state.decideMsg = {
          color: "var(--red)",
          text: res.status === 0 ? "Ошибка связи с сервером"
            : (detail && detail.message ? detail.message
              : (d.detail ? String(d.detail) : "Сервер не ответил (HTTP " + res.status + ")"))
        };
        if (action === "fix" || action === "accept") load();   // состояние могло измениться
        else renderDecide();
        return;
      }

      // --- успех ---
      if (action === "fix") {
        state.decideMsg = {
          color: "var(--green)",
          text: "Правки сохранены. Ошибок: " + (d.errors || 0) +
                " · предупреждений: " + (d.warnings || 0) + "."
        };
        load();
        return;
      }
      if (action === "reject") {
        state.decideMsg = { color: "var(--muted)", text: "Загрузка отклонена. Числа не опубликованы." };
        load();
        return;
      }
      // accept
      state.decideMsg = {
        color: "var(--green)",
        text: "Опубликовано значений: " + (d.published || 0) +
              (d.periodId ? " за период " + d.periodId : "") +
              (d.warnings ? ". Предупреждений: " + d.warnings : "") + "."
      };
      load();
    });
  }

  // ---------- вход ---------------------------------------------------
  function showAuth() {
    el("reviewBox").style.display = "none";
    el("authBox").style.display = "block";
    el("btnLogout").hidden = true;
  }
  function start() {
    el("authBox").style.display = "none";
    el("reviewBox").style.display = "block";
    el("btnLogout").hidden = false;
    load();
  }

  document.getElementById("btnLogin").addEventListener("click", async function () {
    var login = document.getElementById("loginInput").value.trim();
    var pass = document.getElementById("passInput").value;
    var err = document.getElementById("loginErr");
    if (!login || !pass) { err.textContent = "Введите логин и пароль"; return; }
    try {
      var res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login: login, password: pass })
      });
      if (!res.ok) {
        var d = await res.json().catch(function () { return {}; });
        var detail = d && d.detail ? String(d.detail) : "";
        if (res.status === 429) {
          // Сервер сам говорит, сколько ждать — не выдумываем своё.
          err.textContent = detail || "Слишком много попыток входа — попробуйте позже";
        } else if (res.status === 401 || res.status === 403) {
          err.textContent = detail || "Неверный логин или пароль";
        } else {
          err.textContent = "Сервер не ответил (HTTP " + res.status + ") — попробуйте позже";
        }
        return;
      }
      err.textContent = "";
      start();
    } catch (e) {
      err.textContent = "Ошибка связи с сервером";
    }
  });
  ["loginInput", "passInput"].forEach(function (id) {
    document.getElementById(id).addEventListener("keydown", function (e) {
      if (e.key === "Enter") document.getElementById("btnLogin").click();
    });
  });

  document.getElementById("btnLogout").addEventListener("click", async function () {
    await fetch("/api/logout", { method: "POST" });
    location.reload();
  });

  // ---------- старт --------------------------------------------------
  (function init() {
    var raw = new URLSearchParams(location.search).get("id");
    state.id = parseInt(raw, 10);
    if (!state.id) {
      el("msg").innerHTML = '<span style="color:var(--red)">Не указан идентификатор загрузки ' +
        '(?id=N). <a href="uploads.html">Выберите загрузку в списке</a>.</span>';
      return;
    }
    fetch("/api/me").then(function (r) {
      if (!r.ok) throw new Error("no session");
      return r.json();
    }).then(function () {
      start();
    }).catch(function () {
      showAuth();
    });
  })();
})();
