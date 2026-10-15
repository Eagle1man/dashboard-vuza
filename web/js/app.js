/* ============================================================
   app.js — контроллер дашборда ректора
   Рендер, фильтры, навигация, drill-down, экспорт,
   оповещения, роли, тема, поиск, ИИ-чат.
   ============================================================ */
(function () {
  "use strict";
  var D = window.DashboardData;
  var C = window.Charts;
  var AI = window.AIAssistant;
  var API = window.DashboardAPI;

  // ---------- Состояние ----------
  var state = {
    data: D.initialData,
    role: "rector",
    period: "year",
    dept: "all",
    status: "all",
    search: "",
    view: "dashboard",
    blockId: null,
    drill: null,
    widgetOrder: [],
    alerts: {},
    seed: 2026,
    server: false,     // поднят ли локальный сервер
    health: null       // его /api/health
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtFull(v) {
    if (v >= 1000000) return (v / 1000000).toFixed(1).replace(".", ",") + " млн";
    if (v >= 10000) return (v / 1000).toFixed(1).replace(".", ",") + " тыс";
    return String(parseFloat(v.toFixed(2))).replace(".", ",");
  }
  function deltaStr(m) {
    var s = (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%";
    return { s: s, up: m.delta >= 0 };
  }
  function statusClass(s) { return "status-" + s; }
  function statusTxt(s) { return s === "green" ? "OK" : s === "yellow" ? "внимание" : "критично"; }
  function colorForStatus(s) { return s === "green" ? "#16a34a" : s === "yellow" ? "#d97706" : "#dc2626"; }
  function periodName() { return { month: "месяц", quarter: "квартал", year: "год" }[state.period] || "год"; }
  function shortName(name) { return name.replace(/^(факультет|институт)\s+/i, "").split(" ").slice(0, 2).join(" "); }
  function sourceLabel(blockId) {
    var map = { academic: "Moodle, 1С:Университет", results: "Moodle, 1С:Университет", finance: "1С:Бухгалтерия / ERP", employ: "База карьерного центра", science: "Научная часть, Scopus/WoS", ops: "Учётная система деканатов", reputation: "LimeSurvey, Медиалогия" };
    return map[blockId] || "витрина данных";
  }
  function periodLabels(period) {
    switch (period) {
      case "month": return { labels: D.MONTHS.slice(-8), count: 8 };
      case "quarter": return { labels: ["Q1", "Q2", "Q3", "Q4", "Q1", "Q2", "Q3", "Q4"], count: 8 };
      case "year": return { labels: D.YEARS, count: 5 };
      default: return { labels: D.MONTHS, count: 12 };
    }
  }

  function showToast(msg, type) {
    var box = $("toasts");
    var el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, 3800);
  }

  function showView(name) {
    ["dashboard", "detail", "drilldown"].forEach(function (v) {
      var el = $("view-" + v);
      if (v === name) el.classList.add("active");
      else el.classList.remove("active");
    });
    state.view = name;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
function updateStamp() {
    var now = new Date();
    var pad = function (n) { return n < 10 ? "0" + n : n; };
    $("updateStamp").textContent = "Обновлено: " + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds()) + " · сид " + state.seed;
  }

  // ---------- Рендер ленты показателей ----------
  function visibleBlocks() {
    var blocks = D.blocksForRole(state.data, state.role).slice();
    if (state.widgetOrder.length) {
      blocks.sort(function (a, b) {
        var ia = state.widgetOrder.indexOf(a.id), ib = state.widgetOrder.indexOf(b.id);
        ia = ia < 0 ? 999 : ia; ib = ib < 0 ? 999 : ib;
        return ia - ib;
      });
    }
    return blocks;
  }

  function metricMatches(metric) {
    if (state.status !== "all" && metric.status !== state.status) return false;
    if (state.search) {
      var q = state.search.toLowerCase();
      if (metric.title.toLowerCase().indexOf(q) === -1) return false;
    }
    return true;
  }

  function renderDashboard() {
    var grid = $("widgetGrid");
    grid.innerHTML = "";
    var blocks = visibleBlocks();
    blocks.forEach(function (block) {
      var metrics = D.metricsOfBlock(state.data, block.id);
      if (state.search || state.status !== "all") metrics = metrics.filter(metricMatches);
      var card = document.createElement("article");
      card.className = "widget-block";
      card.setAttribute("draggable", "true");
      card.dataset.block = block.id;
      card.innerHTML = widgetBlockInner(block, metrics, periodLabels(state.period));
      grid.appendChild(card);
    });
    enableDrag();
  }

  function widgetBlockInner(block, metrics, pl) {
    var h = '<div class="widget-block-head">' +
      '<span class="widget-block-icon">' + block.icon + "</span>" +
      '<div><h3>' + esc(block.title) + "</h3><p class='sub'>" + esc(block.desc) + "</p></div>" +
      '<button class="mini-btn open-block" data-block="' + block.id + '">Открыть →</button>' +
      "</div><div class='widget-mini-grid'>";
    metrics.forEach(function (m) { h += widgetHTML(m, pl); });
    if (metrics.length === 0) h += "<p class='muted'>Нет показателей по заданным фильтрам.</p>";
    h += "</div>";
    return h;
  }

  function widgetHTML(m, pl) {
    var ds = deltaStr(m);
    var good = (m.dir === "higher") ? ds.up : !ds.up;
    var deltaClass = good ? "up" : "down";
    var arrow = m.delta >= 0 ? "▲" : "▼";
    var spark = C.sparkline(m.series, colorForStatus(m.status));
    return '<div class="metric-widget" data-metric="' + m.id + '" title="Клик — детализация">' +
      '<div class="widget-head"><span class="widget-title">' + esc(m.title) + '</span>' +
      '<span class="status-pill ' + statusClass(m.status) + '">' + statusTxt(m.status) + "</span></div>" +
      '<div class="widget-value">' + fmtFull(m.current) + ' <span class="widget-unit">' + esc(m.unit) + "</span></div>" +
      '<div class="widget-footer"><span class="trend">' +
      '<span class="delta ' + deltaClass + '">' + arrow + " " + ds.s + "</span>" +
      '<span class="muted" style="font-size:11px">к пред. периоду</span></span>' +
      '<span class="mini-actions">' +
      '<button class="mini-btn drill-now" data-metric="' + m.id + '">детали</button></span></div>' +
      '<div class="spark-wrap">' + spark + "</div></div>";
  }

  // ---------- Drag & drop ----------
  function enableDrag() {
    var grid = $("widgetGrid");
    var dragEl = null;
    Array.prototype.forEach.call(grid.querySelectorAll(".widget-block"), function (card) {
      card.addEventListener("dragstart", function (e) {
        dragEl = card; card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });
      card.addEventListener("dragend", function () { card.classList.remove("dragging"); });
      card.addEventListener("dragover", function (e) { e.preventDefault(); });
      card.addEventListener("drop", function (e) {
        e.preventDefault();
        if (!dragEl || dragEl === card) return;
        grid.insertBefore(dragEl, card);
        persistOrder();
      });
    });
  }

  function persistOrder() {
    var order = [];
    Array.prototype.forEach.call($("widgetGrid").querySelectorAll(".widget-block"), function (c) { order.push(c.dataset.block); });
    state.widgetOrder = order;
    showToast("Порядок блоков сохранён", "ok");
  }
// ---------- Страница детализации блока ----------
  function renderDetail(blockId) {
    state.blockId = blockId;
    var block = state.data.blocks.find(function (b) { return b.id === blockId; });
    if (!block) return;
    var mets = D.metricsOfBlock(state.data, blockId);
    $("detailTitle").textContent = block.icon + " " + block.title;
    $("detailSubtitle").textContent = block.desc + " · период: " + periodName();
    var pl = periodLabels(state.period);
    var h = "";

    h += '<div class="metric-row">';
    mets.forEach(function (m) {
      var ds = deltaStr(m);
      var good = (m.dir === "higher") ? ds.up : !ds.up;
      var dc = good ? "up" : "down";
      h += '<div class="metric-cell clickable-cell" data-metric="' + m.id + '" title="Клик — детализация записей">' +
        '<div class="label">' + esc(m.title) + '</div>' +
        '<div class="value">' + fmtFull(m.current) + ' <small>' + esc(m.unit) + "</small></div>" +
        '<div><span class="delta ' + dc + '" style="font-size:12px">' + ds.s + "</span> " +
        '<span class="status-pill ' + statusClass(m.status) + '" style="font-size:10px">' + statusTxt(m.status) + "</span></div></div>";
    });
    h += "</div>";

    var main = mets[0];
    h += '<div class="section-card"><div class="card-head"><h3>Динамика: ' + esc(main.title) + "</h3>" +
      '<span class="sub">цель: ' + fmtFull(main.target) + " " + esc(main.unit) + " · текущее: " + fmtFull(main.current) + "</span></div>" +
      '<div class="chart-wrap">' + C.lineChart(pl.labels, [{ name: main.title, data: main.series.slice(-pl.count), color: colorForStatus(main.status) }]) + "</div></div>";

    var depL = main.dept.map(function (d) { return shortName(d.deptName); });
    var depV = main.dept.map(function (d) { return d.value; });
    h += '<div class="section-card"><h3>По подразделениям: ' + esc(main.title) + "</h3>" +
      '<div class="chart-wrap">' + C.barChart(depL, depV, {}) + '</div><p class="muted">Клик по столбцу — записи подразделения.</p></div>';
    h += '<div class="section-card"><h3>Доля по подразделениям: ' + esc(main.title) + "</h3>" +
      '<div class="chart-wrap">' + C.donutChart(depL, depV) + "</div></div>";

    h += '<div class="section-card"><h3>Показатели блока (' + mets.length + ")</h3><div class='table-wrap'>" +
      '<table class="data"><thead><tr><th>Показатель</th><th>Значение</th><th>Ед.</th><th>Динамика</th><th>Цель</th><th>Статус</th><th></th></tr></thead><tbody>';
    mets.forEach(function (m) {
      var ds = deltaStr(m);
      var good = (m.dir === "higher") ? ds.up : !ds.up;
      var dc = good ? "up" : "down";
      h += "<tr><td>" + esc(m.title) + "</td><td><b>" + fmtFull(m.current) + "</b></td><td>" + esc(m.unit) + "</td>" +
        '<td class="delta ' + dc + '">' + ds.s + "</td><td>" + fmtFull(m.target) + "</td>" +
        '<td><span class="status-pill ' + statusClass(m.status) + '">' + statusTxt(m.status) + "</span></td>" +
        '<td><button class="mini-btn drill-now" data-metric="' + m.id + '">детали →</button></td></tr>';
    });
    h += "</tbody></table></div></div>";

    $("detailBody").innerHTML = h;
    showView("detail");
  }

  // ---------- Страница drill-down ----------
  function renderDrilldown(metricId, deptId) {
    var m = state.data.metrics.find(function (x) { return x.id === metricId; });
    if (!m) return;
    state.drill = { metricId: metricId, deptId: deptId || "all" };
    var block = state.data.blocks.find(function (b) { return b.id === m.block; });
    $("drillTitle").textContent = "Детализация: " + m.title;
    $("drillSubtitle").textContent = (block ? block.title : "") + " · целевой порог " + fmtFull(m.target) + " " + m.unit;

    var rows = [];
    if (deptId && deptId !== "all") rows = (m.drill[deptId] || []).slice();
    else state.data.dept.forEach(function (d) { rows = rows.concat((m.drill[d.id] || []).slice()); });

    var h = '<div class="section-card"><div class="metric-row">' +
      '<div class="metric-cell"><div class="label">Текущее</div><div class="value">' + fmtFull(m.current) + " <small>" + esc(m.unit) + "</small></div></div>" +
      '<div class="metric-cell"><div class="label">Динамика</div><div class="value">' + (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%</div></div>" +
      '<div class="metric-cell"><div class="label">Записей-источников</div><div class="value">' + rows.length + "</div></div>" +
      '<div class="metric-cell"><div class="label">Статус</div><div class="value"><span class="status-pill ' + statusClass(m.status) + '">' + statusTxt(m.status) + "</span></div></div>" +
      "</div>";
    h += "<h3 style='margin:6px 0'>Записи-источники (" + rows.length + ")</h3>";
    if (rows.length) {
      h += "<div class='table-wrap'><table class='data'><thead><tr><th>Студент</th><th>Подразделение</th><th>Значение</th><th>Контекст</th></tr></thead><tbody>";
      rows.forEach(function (r) {
        h += "<tr><td>" + esc(r.fio) + "</td><td>" + esc(r.dept) + "</td><td><b>" + fmtFull(r.value) + " " + esc(m.unit) + "</b></td><td>" + esc(r.reason || "—") + "</td></tr>";
      });
      h += "</tbody></table></div>";
    } else h += "<p class='muted'>Записей не найдено.</p>";
    h += '<p class="muted" style="margin-top:10px">Источник витрины: ' + esc(sourceLabel(m.block)) + " · данные обезличены (152-ФЗ).</p>";
    h += "</div>";

    $("drillBody").innerHTML = h;
    showView("drilldown");
  }
// ---------- Обработчики ----------
  function bindGridClicks() {
    var grid = $("widgetGrid");
    grid.addEventListener("click", function (e) {
      var btn = e.target.closest(".open-block");
      if (btn) { renderDetail(btn.dataset.block); return; }
      var drillBtn = e.target.closest(".drill-now");
      if (drillBtn) { renderDrilldown(drillBtn.dataset.metric, "all"); return; }
      var w = e.target.closest(".metric-widget");
      if (w && !e.target.closest("button")) {
        var block = w.closest(".widget-block");
        if (block) renderDetail(block.dataset.block);
      }
    });
  }

  function bindDetailClicks() {
    var body = $("detailBody");
    body.addEventListener("click", function (e) {
      var drillBtn = e.target.closest(".drill-now");
      if (drillBtn) { renderDrilldown(drillBtn.dataset.metric, "all"); return; }
      var cell = e.target.closest(".clickable-cell");
      if (cell) { renderDrilldown(cell.dataset.metric, "all"); return; }
      var bar = e.target.closest(".bar");
      if (bar && bar.dataset.idx !== undefined) {
        var idx = +bar.dataset.idx;
        var main = currentMainMetric();
        if (main && state.data.dept[idx]) renderDrilldown(main.id, state.data.dept[idx].id);
      }
    });
  }

  function currentMainMetric() {
    if (!state.blockId) return null;
    return D.metricsOfBlock(state.data, state.blockId)[0] || null;
  }

  function bindControls() {
    $("btnRefresh").addEventListener("click", async function () {
      var btn = this;
      btn.disabled = true;
      try {
        if (state.server) {
          // Сервер поднят: обновление = перечитать витрину из базы,
          // а НЕ сгенерировать новый срез. Иначе цифры на экране и в
          // базе разъезжаются, и ответы ИИ начинают спорить с экраном.
          var fresh = await API.metrics();
          state.data = fresh;
          showToast("Данные перечитаны из базы", "ok");
        } else {
          state.seed = (state.seed + 1) % 100000;
          state.data = D.build(state.seed);
          showToast("Демо-режим: сгенерирован новый срез витрины", "warn");
        }
        applyAlertsIndicators();
        renderDashboard();
        updateStamp();
      } catch (err) {
        showToast("Не удалось обновить: " + err.message, "warn");
      } finally {
        btn.disabled = false;
      }
    });
    $("btnBack").addEventListener("click", function () { showView("dashboard"); });
    $("btnBackDrill").addEventListener("click", function () {
      if (state.blockId) renderDetail(state.blockId);
      else showView("dashboard");
    });
    $("btnExportCsv").addEventListener("click", exportSummaryCsv);
    $("btnDetailCsv").addEventListener("click", exportBlockCsv);
    $("btnDrillCsv").addEventListener("click", exportDrillCsv);
    $("btnExportPdf").addEventListener("click", function () { showToast("PDF-экспорт (в production подключится генератор отчётов)", "warn"); });
    $("btnExportXlsx").addEventListener("click", function () { showToast("Excel-экспорт (в production подключится openpyxl)", "warn"); });

    $("filterPeriod").addEventListener("change", function () {
      state.period = this.value;
      if (state.view === "dashboard") renderDashboard();
      else if (state.view === "detail" && state.blockId) renderDetail(state.blockId);
    });
    $("filterDepartment").addEventListener("change", function () { state.dept = this.value; renderDashboard(); });
    $("filterStatus").addEventListener("change", function () { state.status = this.value; renderDashboard(); });

    $("globalSearch").addEventListener("input", function () {
      state.search = this.value.trim();
      var badge = $("searchBadge");
      var active = 0;
      visibleBlocks().forEach(function (b) { active += D.metricsOfBlock(state.data, b.id).filter(metricMatches).length; });
      badge.hidden = state.search.length === 0;
      badge.textContent = active + "";
      renderDashboard();
    });

    $("themeToggle").addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("dash_theme", next); } catch (e) {}
    });

    $("roleSelect").addEventListener("change", function () { state.role = this.value; applyRole(); });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { toggleAI(false); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault(); $("globalSearch").focus(); $("globalSearch").select();
      }
    });
  }
// ---------- Роли ----------
  function applyRole() {
    var role = state.role;
    var hint = role === "rector" ? "Ректор — полный доступ"
      : role === "prorector" ? "Проректор — финансовый блок скрыт (зона ответственности)"
      : "Администратор — полный доступ + настройка порогов";
    var el = document.createElement("div");
    el.className = "toast";
    el.textContent = "Роль: " + hint;
    $("toasts").appendChild(el);
    setTimeout(function () { el.remove(); }, 3000);
    state.widgetOrder = [];
    renderDashboard();
  }

  // ---------- Тема ----------
  function applyTheme() {
    var saved = null;
    try { saved = localStorage.getItem("dash_theme"); } catch (e) {}
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  }

  // ---------- Оповещения ----------
  function applyAlertsIndicators() {
    Object.keys(state.alerts).forEach(function (metricId) {
      var cfg = state.alerts[metricId];
      if (!cfg.enabled) return;
      var m = state.data.metrics.find(function (x) { return x.id === metricId; });
      if (!m) return;
      var breach = null;
      if (m.dir === "higher" && m.current < cfg.threshold) breach = "ниже критичного порога";
      if (m.dir === "lower" && m.current > cfg.threshold) breach = "превысил критичный порог";
      if (breach && !cfg.notified) {
        cfg.notified = true;
        showToast("🔔 «" + m.title + "» " + breach + " (" + fmtFull(m.current) + " " + m.unit + ")", "err");
      }
      if (!breach) cfg.notified = false;
    });
  }

  // ---------- Экспорт CSV ----------
  function downloadCsv(filename, rows) {
    var csv = rows.map(function (r) {
      return r.map(function (c) { return '"' + String(c).replace(/"/g, '""') + '"'; }).join(";");
    }).join("\r\n");
    var blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
    var a = document.createElement("a");
    var url = URL.createObjectURL(blob);
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("CSV-файл выгружен", "ok");
  }

  function exportSummaryCsv() {
    var rows = [["Дашборд ректора — Южный университет (ИУБиП)"], ["Блок", "Показатель", "Значение", "Ед.", "Динам.,%", "Цель", "Статус"]];
    visibleBlocks().forEach(function (b) {
      D.metricsOfBlock(state.data, b.id).forEach(function (m) {
        rows.push([b.title, m.title, m.current, m.unit, m.delta.toFixed(1), m.target, m.status]);
      });
    });
    downloadCsv("dashbort-svodka.csv", rows);
  }

  function exportBlockCsv() {
    if (!state.blockId) return;
    var block = state.data.blocks.find(function (b) { return b.id === state.blockId; });
    var rows = [["Блок", "Показатель", "Значение", "Ед.", "Динам.,%", "Цель", "Статус"]];
    D.metricsOfBlock(state.data, state.blockId).forEach(function (m) {
      rows.push([block.title, m.title, m.current, m.unit, m.delta.toFixed(1), m.target, m.status]);
    });
    downloadCsv("blok-" + state.blockId + ".csv", rows);
  }

  function exportDrillCsv() {
    if (!state.drill) { showToast("Нет данных для экспорта", "warn"); return; }
    var m = state.data.metrics.find(function (x) { return x.id === state.drill.metricId; });
    if (!m) return;
    var list = [];
    if (state.drill.deptId && state.drill.deptId !== "all") list = m.drill[state.drill.deptId] || [];
    else state.data.dept.forEach(function (d) { list = list.concat(m.drill[d.id] || []); });
    var rows = [["Показатель", "Студент", "Подразделение", "Значение", "Ед.", "Контекст"]];
    list.forEach(function (r) { rows.push([m.title, r.fio, r.dept, r.value, m.unit, r.reason || "—"]); });
    downloadCsv("detalizaciya-" + m.id + ".csv", rows);
  }
// ---------- ИИ-ассистент ----------
  var SUGGESTIONS = [
    "Почему выросло отчисление на 1 курсе?",
    "Подготовь сводку по финансам",
    "Покажи трудоустройство по факультетам",
    "Подготовь еженедельный отчёт по науке",
    "Какой блок требует внимания?"
  ];

  function initAI() {
    $("aiOpen").addEventListener("click", function () { toggleAI(true); });
    $("aiClose").addEventListener("click", function () { toggleAI(false); });
    $("aiForm").addEventListener("submit", async function (e) {
      e.preventDefault();
      var text = $("aiInput").value.trim();
      if (!text) return;
      addAiMessage("user", esc(text));
      $("aiInput").value = "";
      setAiTyping(true);
      try {
        var ctx = { periodLabels: periodLabels(state.period).labels, currentBlockId: state.blockId };
        var res = await AI.ask(text, state.data, ctx);
        renderAiAnswer(res, text);
      } catch (err) {
        addAiMessage("ai", "⚠️ Ошибка: " + esc(err.message));
      } finally { setAiTyping(false); }
    });
    setSuggestions();
  }

  function toggleAI(open) {
    $("aiPanel").hidden = !open;
    $("aiOpen").hidden = open;
    if (open) $("aiInput").focus();
  }

  function setSuggestions() {
    var box = $("aiSuggestions");
    box.innerHTML = "";
    SUGGESTIONS.forEach(function (s) {
      var chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.textContent = "💬 " + s;
      chip.addEventListener("click", function () {
        $("aiInput").value = s;
        $("aiForm").requestSubmit();
      });
      box.appendChild(chip);
    });
  }

  function addAiMessage(role, html) {
    var box = $("aiMessages");
    var div = document.createElement("div");
    div.className = "msg " + role;
    div.innerHTML = html;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function setAiTyping(on) {
    if (on) {
      var t = document.createElement("div");
      t.className = "msg ai typing";
      t.id = "aiTyping";
      t.textContent = "Анализирую данные...";
      $("aiMessages").appendChild(t);
    } else {
      var el = $("aiTyping");
      if (el) el.remove();
    }
  }

  function simpleMarkdown(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/^•/gm, "&bull;");
  }

  function renderAiAnswer(res, userText) {
    var h = "";
    if (res && res.text) h += simpleMarkdown(res.text).replace(/\n/g, "<br/>");
    else h += esc("Не удалось сформировать ответ.");

    // Источники обязательны: ответ, который нельзя открыть и проверить,
    // для ректора хуже, чем честное "в документах не найдено".
    if (res.sources && res.sources.length) {
      h += '<details class="src-list"><summary>Источники (' +
        res.sources.length + ")</summary>";
      res.sources.forEach(function (s) {
        var tag = s.is_demo ? " <span class='tag-demo'>демо</span>" : "";
        h += '<div class="src-item"><div class="src-head">' +
          esc("[" + s.n + "] ") + esc(s.title) + tag +
          (s.heading ? '<span class="src-sec"> — ' + esc(s.heading) + "</span>" : "") +
          '</div><div class="src-snip">' + esc(s.snippet) + "</div></div>";
      });
      h += "</details>";
    }

    if (res.drill && res.drill.length) {
      h += "<br/><br/><b>Источники (записи):</b><br/>" +
        res.drill.map(function (r) {
          var unit = res.metricId ? ((state.data.metrics.find(function (x) { return x.id === res.metricId; }) || {}).unit || "") : "";
          return "• " + esc(r.fio) + " · " + esc(r.dept) + " · " + AI.fmtVal(r.value, unit);
        }).join("<br/>");
    }

    if (res.charts && res.charts.length) {
      h += '<br/><br/><b>Предлагаемая визуализация:</b>';
      res.charts.slice(0, 1).forEach(function (ch) {
        h += '<div style="margin-top:6px">' + C.lineChart(ch.labels, [{ name: ch.title, data: ch.data, color: "#2758c2" }]) + "</div>";
      });
    }

    if (res.source) h += '<div class="src">📄 ' + esc(res.source) + "</div>";
    addAiMessage("ai", h);
  }

  // ---------- Подключение к локальному серверу ----------
  // Сервер поднимает start.bat. Если его нет (например, index.html
  // открыли двойным кликом), дашборд продолжает работать на демо-данных
  // и честно пишет об этом в шапке.
  async function connectServer() {
    if (!API) { setStatus({ demo: true }); return; }
    try {
      var h = await API.health();
      state.server = true;
      state.health = h;
      var fresh = await API.metrics();
      if (fresh && fresh.metrics && fresh.metrics.length) {
        state.data = fresh;
        applyAlertsIndicators();
        renderDashboard();
        updateStamp();
      }
      setStatus(h);
    } catch (err) {
      // Раньше здесь стоял молчаливый catch, и любая ошибка выглядела
      // как «сервер не запущен»: и недоступность сервера, и сбой
      // отрисовки после подстановки данных давали одну и ту же надпись
      // в шапке. Теперь причина попадает и в консоль, и в подсказку.
      console.error("[dashboard] не удалось подключиться к серверу:", err);
      state.server = false;
      setStatus({ demo: true, error: err && err.message });
    }
  }

  function setStatus(h) {
    var el = $("sysStatus");
    if (!el) return;
    el.className = "sys-status";
    if (h && h.demo) {
      el.classList.add("is-demo");
      if (h.error) {
        // Сервер ответил, но с данными что-то не так — это не то же
        // самое, что «сервер не запущен».
        el.textContent = "ошибка данных: " + h.error;
        el.title = "Подробности в консоли браузера (F12 → Console)";
      } else {
        el.textContent = "демо-режим · сервер не запущен";
        el.title = "Откройте дашборд через start.bat — тогда заработает поиск по документам";
      }
      return;
    }
    var ollama = (h && h.ollama) || {};
    var docs = (h && h.totals && h.totals.docs) || 0;
    if (ollama.available && ollama.llm_installed) {
      el.classList.add("is-ok");
      el.textContent = "документов: " + docs + " · " + (ollama.llm_model || "");
    } else if (docs > 0) {
      el.classList.add("is-warn");
      el.textContent = "документов: " + docs + " · модель не скачана";
      el.title = "Поиск по документам работает. Чтобы получать сводные ответы, выполните: ollama pull " +
        (ollama.llm_model || "qwen2.5:1.5b");
    } else {
      el.classList.add("is-warn");
      el.textContent = "документы не загружены · положите файлы в папку knowledge";
    }
  }

  // ---------- Инициализация ----------
  function init() {
    var deptSel = $("filterDepartment");
    deptSel.innerHTML = "";
    var opt0 = document.createElement("option");
    opt0.value = "all"; opt0.textContent = "Все";
    deptSel.appendChild(opt0);
    var deptList = (state.data.dept && state.data.dept.length)
      ? state.data.dept
      : D.DEPARTMENTS.map(function (d) { return { id: d.id, name: d.name }; });
    deptList.forEach(function (d) {
      var o = document.createElement("option");
      o.value = d.id; o.textContent = d.name;
      deptSel.appendChild(o);
    });
    applyTheme();
    applyRole();
    renderDashboard();
    bindGridClicks();
    bindDetailClicks();
    bindControls();
    initAI();
    updateStamp();
    connectServer();
  }

  document.addEventListener("DOMContentLoaded", init);
})();