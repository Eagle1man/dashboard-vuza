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
    health: null,      // его /api/health
    monitoring: null   // /api/monitoring: E-показатели и места
  };

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtFull(v) {
    // У официального показателя цель не задана, а у показателя без
    // истории нет предыдущей точки. Раньше здесь печаталось «NaN»,
    // а в deltaStr — падало с TypeError, из-за чего не отрисовывался
    // весь дашборд, а не одна его часть.
    //
    // Делить на 1000 здесь нельзя: единица показателя уже может быть
    // «тыс ₽», и тогда 221 475 превращалось в «221,5 тыс ₽» — число
    // в тысячу раз меньше настоящего. Показываем как есть, с пробелами
    // между разрядами: 2 946, 221 475.
    if (v === null || v === undefined || isNaN(v)) return "—";
    var n = Number(v);
    var digits = Math.abs(n) >= 100 ? 0 : 2;
    return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
  }
  // Изменение к прошлому периоду. У официальных показателей ряда нет,
  // поэтому изменения нет либо оно равно нулю — «0,0 %» в этом случае
  // вводит ректора в заблуждение, значит честнее «—».
  function deltaStr(m) {
    if (m.delta === null || m.delta === undefined || isNaN(m.delta)) {
      return { s: "—", up: true, none: true };
    }
    var s = (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1).replace(".", ",") + "%";
    return { s: s, up: m.delta >= 0, none: false };
  }
  function statusClass(s) { return "status-" + s; }
  // Статус «none» — не «красный». У официального показателя нет
  // целевого порога, и красить его красным значит выдумать тревогу.
  function statusTxt(s) {
    if (s === "green") return "OK";
    if (s === "yellow") return "внимание";
    if (s === "red") return "критично";
    return "без порога";
  }
  function colorForStatus(s) {
    if (s === "green") return "#0f7a38";
    if (s === "yellow") return "#a8801a";
    if (s === "red") return "#c8102e";
    return "#4b688b";
  }
  // Значение показателя по выбранному подразделению.
  //
  // Фильтр по подразделению стоял в интерфейсе, но ничего не фильтровал:
  // значение на карточке всегда было университетским. Для показателя с
  // разбивкой по кафедрам берётся значение этой кафедры, для официального
  // показателя разбивки нет — остаётся значение по университету, и об
  // этом сказано прямо в подписи под блоком.
  function metricValueForDept(m, deptId) {
    if (!deptId || deptId === "all") return m.current;
    if (!m.dept || !m.dept.length) return m.current;
    var d = null;
    for (var i = 0; i < m.dept.length; i++) {
      if (m.dept[i].deptId === deptId) { d = m.dept[i]; break; }
    }
    return d ? d.value : m.current;
  }
  // Статус показателя при выбранном подразделении: считается от значения
  // этого подразделения, а не от университетского. Иначе карточка
  // показывала бы зелёный статус рядом с красным по сути числом.
  function statusForValue(m, value) {
    if (m.target === null || m.target === undefined) return "none";
    var score;
    if (m.dir === "higher") score = m.target === 0 ? 1 : value / m.target;
    else score = m.target === 0 ? 1 : m.target / value;
    if (score >= 0.92) return "green";
    if (score >= 0.8) return "yellow";
    return "red";
  }
  // Название выбранного подразделения — для подписи «показано по …».
  function selectedDeptName() {
    if (!state.dept || state.dept === "all") return null;
    var list = state.data.dept || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === state.dept) return list[i].name;
    }
    return null;
  }
  function periodName() { return { month: "месяц", quarter: "квартал", year: "год" }[state.period] || "год"; }
  function shortName(name) { return name.replace(/^(факультет|институт)\s+/i, "").split(" ").slice(0, 2).join(" "); }
  function sourceLabel(blockId) {
    // Блоки в структуре 2026–2027: academic, results, finance, vnoko,
    // science, upr, digital. Коды employ, ops и reputation к ней не
    // относятся — их больше нет.
    var map = {
      academic: "приёмная кампания, 1С:Университет",
      results: "деканаты, Moodle",
      finance: "бухгалтерия, план ФХД",
      vnoko: "ВНОКО, результаты опросов",
      science: "научная часть, Scopus/WoS",
      upr: "управление по воспитательной работе",
      digital: "ИТ-служба, ЭИОС"
    };
    return map[blockId] || "витрина данных";
  }
  // Подпись под значением: показывает, откуда цифра пришла. Загрузка
  // через анкету важнее общего источника — это конкретное подразделение
  // подписало конкретный документ.
  function metricSourceNote(m) {
    if (!m) return "";
    if (m.prov && m.prov.length) {
      var p = m.prov[0];
      var d = (p.uploaded_at || "").slice(0, 10);
      return "загружено: " + p.division + (d ? " · " + d : "");
    }
    return m.srcNote || "документы университета";
  }
  // Перевод способа извлечения значения на человеческий язык (ТЗ §5.5):
  // провенанс обязан быть читаемым, а не набором кодов из базы.
  function extractMethodLabel(m) {
    var map = {
      questionnaire: "введено на форме анкеты",
      model_extract: "нашла модель (без адреса ячейки)",
      xlsx_cell: "ячейка Excel",
      xls_cell: "ячейка Excel, старый формат",
      csv_cell: "ячейка CSV",
      xlsx_row: "строка Excel",
      xls_row: "строка Excel, старый формат",
      csv_row: "строка CSV",
      docx_table: "таблица Word",
      docx_block: "абзац Word",
      pdf_page: "страница PDF",
      text_block: "фрагмент текста"
    };
    return map[m] || m || "—";
  }

  // Провенанс числа: /api/metrics/{id}/sources отдаёт, какое значение
  // за какой период пришло из какого файла, кем и каким способом
  // подтверждено. Тянем отдельным запросом и рисуем в drill-down —
  // в самой витрине этих полей нет, а обещать связь «число → документ»
  // без показа документов нельзя.
  function loadSources(metricId) {
    var box = document.getElementById("drillSources");
    if (!box) return;
    box.innerHTML = '<p class="muted">Загружаю источник числа…</p>';
    fetch("/api/metrics/" + encodeURIComponent(metricId) + "/sources")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        // Значения без загрузки — внесённые вручную или из документа,
        // который подключён без анкеты: цепочка до подразделения у них
        // честно отсутствует.
        var vals = (data.values || []).filter(function (v) {
          return v.upload_id || v.source_file_name;
        });
        if (!vals.length) {
          box.innerHTML = '<p class="muted">За этот показатель пока нет значений, ' +
            'опубликованных из документов: число внесено из документа напрямую ' +
            'или вручную. Цепочка «число → документ → подразделение» ' +
            'появится после загрузки файла и кнопки «Принять» на экране проверки.</p>';
          return;
        }
        var h = "<div class='table-wrap'><table class='data'><thead><tr>" +
          "<th>Период</th><th>Значение</th><th>Документ</th><th>Подразделение</th>" +
          "<th>Как извлечено</th><th>Подтвердил</th></tr></thead><tbody>";
        vals.forEach(function (v) {
          h += "<tr><td>" + esc(v.period || v.period_id) + "</td>" +
            "<td><b>" + esc(String(v.value_exact != null ? v.value_exact : v.value)) + "</b></td>" +
            "<td>" + esc(v.source_file_name || "—") + "</td>" +
            "<td>" + esc(v.division_title || "—") + "</td>" +
            "<td>" + esc(extractMethodLabel(v.extraction_method)) +
            (v.parser_model ? "<br><small>модель: " + esc(v.parser_model) + "</small>" : "") + "</td>" +
            "<td>" + esc(v.confirmed_by || "—") +
            (v.confirmed_at ? "<br><small>" + esc(v.confirmed_at) + "</small>" : "") + "</td></tr>";
        });
        h += "</tbody></table></div>";
        box.innerHTML = h;
      })
      .catch(function (e) {
        box.innerHTML = '<p class="muted">Не удалось получить источник числа: ' +
          esc(e.message) + "</p>";
      });
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
    // Статус считается от того значения, которое реально показано на
    // карточке: при выбранном подразделении это значение кафедры, а не
    // университетское. Иначе фильтр «Красный» отбирал бы показатели
    // по статусу всего университета, а на карточке стоял бы статус
    // кафедры — расхождение, которое сбивает с толку.
    if (state.status !== "all") {
      var st = statusForValue(metric, metricValueForDept(metric, state.dept));
      if (st !== state.status) return false;
    }
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
    updateFooterNote();
  }

  // Сколько в ленте чисел из документов. Демонстрационных значений
  // больше нет: каждое число подписано источником.
  function updateFooterNote() {
    var el = $("footerCounts");
    if (!el || !state.data || !state.data.metrics) return;
    var ms = state.data.metrics;
    var official = 0;
    for (var i = 0; i < ms.length; i++) {
      if (ms[i].source === "official") official++;
    }
    el.textContent = official + " из " + ms.length + " — из документов";
  }

  // ---------- Справочник образовательных программ ----------
  // Коды, уровни и годы — реальные (сайт университета). Это не метрика
  // с трендом, а справочник: по нему видно, сколько программ у
  // университета и где стандарт устарел.
  function renderStandards() {
    var list = (state.data && state.data.standards) || [];
    var tb = document.querySelector("#standardsTable tbody");
    if (!tb) return;
    tb.innerHTML = "";
    list.forEach(function (s) {
      var tr = document.createElement("tr");
      // Стандарт, который не менялся дольше трёх лет, выделяем:
      // это кандидат на пересмотр, а не ошибка в данных.
      var stale = s.updated && (new Date().getFullYear() - s.updated) > 3;
      tr.innerHTML = "<td>" + esc(s.code) + "</td>" +
        "<td>" + esc(s.title) + "</td>" +
        "<td>" + esc(s.level) + "</td>" +
        "<td>" + (s.approved || "—") + "</td>" +
        "<td" + (stale ? ' class="muted"' : "") + ">" +
        (s.updated || "—") + (stale ? " ⚠" : "") + "</td>";
      tb.appendChild(tr);
    });

    var byLevel = {};
    list.forEach(function (s) { byLevel[s.level] = (byLevel[s.level] || 0) + 1; });
    // Родительный падеж для уровня: «8 бакалавриата», а не «8 бакалавриат».
    var GENITIVE = { "Бакалавриат": "бакалавриата", "Магистратура": "магистратуры", "СПО": "СПО" };
    var parts = Object.keys(byLevel).map(function (k) {
      return byLevel[k] + " " + (GENITIVE[k] || k.toLowerCase());
    });
    var year = new Date().getFullYear();
    var fresh = list.filter(function (s) { return s.updated === year; }).length;
    var el = $("standardsSummary");
    if (el) {
      el.textContent = list.length + " программ: " + parts.join(", ") +
        ". Изменены в " + year + " году: " + fresh + ".";
    }
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

  // Сводка по академиям: кафедры складываются в свою академию.
  // Академия — верхний уровень сводки, кафедра — учебное ядро. Без
  // этого девять кафедр в одной плоской куче не дают увидеть, где
  // университет силён, а где проседает.
  function academyRollup(dept) {
    var academies = (state.data.academies && state.data.academies.length)
      ? state.data.academies : (D.ACADEMIES || []);
    var parent = {};
    (state.data.dept || []).forEach(function (d) { parent[d.id] = d.parentId; });
    var sums = {}, order = [];
    (dept || []).forEach(function (d) {
      var pid = parent[d.deptId] || "";
      if (!(pid in sums)) { sums[pid] = 0; order.push(pid); }
      sums[pid] += d.value;
    });
    return order.map(function (pid) {
      var name = "";
      for (var i = 0; i < academies.length; i++) {
        if (academies[i].id === pid) { name = academies[i].name; break; }
      }
      return {
        id: pid,
        name: name || "Без академии",
        value: Math.round(sums[pid] * 10) / 10
      };
    });
  }

  // Короткая подпись источника для карточки. Полный текст — в
  // детализации, а на карточке хватает, откуда взято число: решение,
  // принятое по цифре без источника, не подписать нельзя.
  function shortSource(note) {
    if (!note) return "";
    var s = String(note);
    var m = s.match(/^(.*?)(, стр\\.|, табл\\.|, раздел|, расчёт)/);
    var head = m ? m[1] : s;
    if (head.length > 46) head = head.slice(0, 45) + "…";
    return head;
  }
  // Русское окончание для числа: 1 шаг, 2 шага, 5 шагов.
  function plural(n, forms) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return forms[2];
    if (b > 1 && b < 5) return forms[1];
    if (b === 1) return forms[0];
    return forms[2];
  }
  function widgetHTML(m, pl) {
    // Выбранное подразделение меняет значение и статус: показывать
    // рядом с числом кафедры университетский статус — значит врать.
    var value = metricValueForDept(m, state.dept);
    var status = statusForValue(m, value);
    var ds = deltaStr(m);
    var good = (m.dir === "higher") ? ds.up : !ds.up;
    var deltaClass = good ? "up" : "down";
    var arrow = (m.delta === null || m.delta === undefined) ? "" : (m.delta >= 0 ? "▲" : "▼");
    var spark = C.sparkline(m.series, colorForStatus(status));
    // У официального показателя исторического ряда нет — спарклайн из
    // одной точки не значит ничего, и подпись «к пред. периоду» вводит
    // в заблуждение.
    var foot = ds.none
      ? '<span class="muted" style="font-size:11px">исторического ряда нет</span>'
      : '<span class="trend"><span class="delta ' + deltaClass + '">' + arrow + " " + ds.s + "</span>" +
        '<span class="muted" style="font-size:11px">к пред. периоду</span></span>';
    // У официального показателя источник показан прямо на карточке:
    // решение, принятое по цифре, должно быть подписано. Если значение
    // пришло загрузкой подразделения — важнее показать, кто и когда
    // загрузил документ.
    var srcLine = "";
    if (m.prov && m.prov.length) {
      var p0 = m.prov[0];
      // Свежесть: если подразделение обещало обновлять данные, а дата
      // прошла — карточка обязана это показать, иначе устаревшая цифра
      // выглядит свежей.
      var overdue = p0.next_expected && p0.next_expected < new Date().toISOString().slice(0, 10);
      var full0 = (p0.title ? p0.title + " · " : "") + p0.division +
        (p0.period ? " · период: " + p0.period : "") +
        ((p0.uploaded_at || "") ? " · загружено " + p0.uploaded_at : "") +
        (p0.responsible ? " · ответственный: " + p0.responsible : "") +
        (p0.next_expected ? " · обновить до " + p0.next_expected : "") +
        (overdue ? " · ПРОСРОЧЕНО" : "");
      var short0 = "📥 " + esc(p0.division) +
        ((p0.uploaded_at || "") ? " · " + esc(p0.uploaded_at.slice(0, 10)) : "") +
        (overdue ? ' · <span style="color:var(--red);font-weight:600">просрочено</span>' : "");
      srcLine = '<div class="widget-src" title="' + esc(full0) + '">' + short0 + "</div>";
    } else if (m.srcNote) {
      srcLine = '<div class="widget-src" title="' + esc(m.srcNote) + '">' +
        esc(shortSource(m.srcNote)) + "</div>";
    }
    // Связь с местом в мониторинге: бейдж, если показатель ленты влияет
    // на какой-то из E-показателей. Нет бейджа — не влияет на место.
    var monBadges = (window.MONITORING_LINKS && window.MONITORING_LINKS.byMetric[m.id] || []).map(function (code) {
      return '<span class="mon-badge" title="Влияет на показатели мониторинга: ' + esc(code) + '">' + esc(code) + "</span>";
    }).join("");
    return '<div class="metric-widget" data-metric="' + m.id + '" title="Клик — детализация">' +
      '<div class="widget-head"><span class="widget-title">' + esc(m.title) + '</span>' +
      '<span class="widget-badges">' +
      '<span class="status-pill ' + statusClass(status) + '">' + statusTxt(status) + "</span>" + monBadges + "</span></div>" +
      '<div class="widget-value">' + fmtFull(value) + ' <span class="widget-unit">' + esc(m.unit) + "</span></div>" +
      srcLine +
      '<div class="widget-footer">' + foot +
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
      var value = metricValueForDept(m, state.dept);
      var st = statusForValue(m, value);
      // Статус — плашкой в правом верхнем углу, как в макете. «Без порога»
      // плашкой не рисуем: у официальных показателей цели нет, и серая
      // капсула читалась бы как отдельное состояние тревоги.
      var pill = (st && st !== "none")
        ? '<span class="status-pill ' + statusClass(st) + '">' + statusTxt(st) + "</span>"
        : "";
      var foot = ds.none
        ? '<span class="metric-note">нет динамики</span>'
        : '<span class="delta ' + dc + '">' + ds.s + "</span>";
      if (st === "none") foot += ' <span class="metric-note">без порога</span>';
      h += '<div class="metric-cell clickable-cell" data-metric="' + m.id + '" title="Клик — детализация записей">' +
        '<div class="metric-head"><span class="label">' + esc(m.title) + "</span>" + pill + "</div>" +
        '<div class="value">' + fmtFull(value) + " <small>" + esc(m.unit) + "</small></div>" +
        '<div class="metric-foot">' + foot + "</div></div>";
    });
    h += "</div>";

    var deptName = selectedDeptName();
    if (deptName) {
      h += '<p class="muted">Показано по подразделению: ' + esc(deptName) +
        ". У официальных показателей разбивки по кафедрам в документах нет — " +
        "для них значение остаётся по университету.</p>";
    }

    var main = mets[0];
    // У официального показателя нет ни исторического ряда, ни разбивки
    // по кафедрам: в документах университета такой разбивки просто нет.
    // Раньше здесь строились график из одной точки и пустая диаграмма,
    // а оба выглядели как «данные не пришли».
    var hasHistory = main.hasHistory !== false && main.series && main.series.length > 1;
    var hasDept = main.dept && main.dept.length > 0;

    if (hasHistory) {
      h += '<div class="section-card"><div class="card-head"><h3>Динамика: ' + esc(main.title) + "</h3>" +
        '<span class="sub">цель: ' + fmtFull(main.target) + " " + esc(main.unit) + " · текущее: " + fmtFull(main.current) + "</span></div>" +
        '<div class="chart-wrap">' + C.lineChart(pl.labels, [{ name: main.title, data: main.series.slice(-pl.count), color: colorForStatus(main.status) }]) + "</div></div>";
    } else {
      h += '<div class="section-card"><div class="card-head"><h3>Динамика: ' + esc(main.title) + "</h3>" +
        '<span class="sub">текущее: ' + fmtFull(main.current) + " " + esc(main.unit) + "</span></div>" +
        '<p class="muted">Исторического ряда нет: показатель взят из документа на одну дату, '
        + 'сравнивать с прошлым периодом не с чем. Источник: ' + esc(metricSourceNote(main)) + ".</p></div>";
    }

    if (hasDept) {
      var depL = main.dept.map(function (d) { return shortName(d.deptName); });
      var depV = main.dept.map(function (d) { return d.value; });
      h += '<div class="section-card"><h3>По подразделениям: ' + esc(main.title) + "</h3>" +
        '<div class="chart-wrap">' + C.barChart(depL, depV, {}) + '</div><p class="muted">Клик по столбцу — записи подразделения.</p></div>';
      h += '<div class="section-card"><h3>Доля по подразделениям: ' + esc(main.title) + "</h3>" +
        '<div class="chart-wrap">' + C.donutChart(depL, depV) + "</div></div>";
      var acc = academyRollup(main.dept);
      if (acc.length) {
        h += '<div class="section-card"><h3>По академиям: ' + esc(main.title) + "</h3>" +
          '<div class="chart-wrap">' + C.barChart(acc.map(function (a) { return shortName(a.name); }),
            acc.map(function (a) { return a.value; }), {}) + "</div>" +
          '<p class="muted">Академия — верхний уровень сводки, кафедра — учебное ядро.</p></div>';
      }
    } else {
      h += '<div class="section-card"><h3>По подразделениям: ' + esc(main.title) + "</h3>" +
        '<p class="muted">Разбивки по подразделениям в документе нет.</p></div>';
    }

    h += '<div class="section-card"><h3>Показатели блока (' + mets.length + ")</h3><div class='table-wrap'>" +
      '<table class="data"><thead><tr><th>Показатель</th><th>Значение</th><th>Ед.</th><th>Динамика</th><th>Цель</th><th>Статус</th><th></th></tr></thead><tbody>';
    mets.forEach(function (m) {
      var ds = deltaStr(m);
      var good = (m.dir === "higher") ? ds.up : !ds.up;
      var dc = good ? "up" : "down";
      var value = metricValueForDept(m, state.dept);
      var st = statusForValue(m, value);
      h += "<tr><td>" + esc(m.title) + "</td><td><b>" + fmtFull(value) + "</b></td><td>" + esc(m.unit) + "</td>" +
        '<td class="delta ' + dc + '">' + ds.s + "</td><td>" + fmtFull(m.target) + "</td>" +
        '<td><span class="status-pill ' + statusClass(st) + '">' + statusTxt(st) + "</span></td>" +
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

    // Ответственный из анкеты — обязательное поле: без него непонятно,
    // с кем поговорить про цифру, а поле есть в каждой загрузке.
    var provCell = "";
    if (m.prov && m.prov.length) {
      var pp = m.prov[0];
      provCell = '<div class="metric-cell"><div class="label">Источник · ответственный</div>' +
        '<div class="value" style="font-size:14px;line-height:1.35">' + esc(pp.division) +
        (pp.responsible ? "<br><small>" + esc(pp.responsible) + "</small>" : "") +
        "</div></div>";
    }
    var h = '<div class="section-card"><div class="metric-row">' +
      '<div class="metric-cell"><div class="label">Текущее</div><div class="value">' + fmtFull(m.current) + " <small>" + esc(m.unit) + "</small></div></div>" +
      '<div class="metric-cell"><div class="label">Динамика</div><div class="value">' + deltaStr(m).s + "</div></div>" +
      '<div class="metric-cell"><div class="label">Записей-источников</div><div class="value">' + rows.length + "</div></div>" +
      '<div class="metric-cell"><div class="label">Статус</div><div class="value"><span class="status-pill ' + statusClass(m.status) + '">' + statusTxt(m.status) + "</span></div></div>" +
      provCell +
      "</div>";
    h += "<h3 style='margin:6px 0'>Записи-источники (" + rows.length + ")</h3>";
    if (rows.length) {
      h += "<div class='table-wrap'><table class='data'><thead><tr><th>Студент</th><th>Подразделение</th><th>Значение</th><th>Контекст</th></tr></thead><tbody>";
      rows.forEach(function (r) {
        h += "<tr><td>" + esc(r.fio) + "</td><td>" + esc(r.dept) + "</td><td><b>" + fmtFull(r.value) + " " + esc(m.unit) + "</b></td><td>" + esc(r.reason || "—") + "</td></tr>";
      });
      h += "</tbody></table></div>";
    } else if (m.source === "official") {
      // У официального показателя поимённых записей нет и быть не
      // может: в отчёте о самообследовании численность приводится
      // обобщённо, по уровням образования. Пустое состояние «записей
      // не найдено» читалось бы как сбой.
      h += "<p class='muted'>Поимённых записей по этому показателю нет: " +
        "в документах университета численность приводится обобщённо, " +
        "по уровням образования. Разбивки по кафедрам в документах тоже нет — " +
        "это можно увидеть на странице показателя.</p>";
    } else h += "<p class='muted'>Записей не найдено.</p>";
    // У каждого показателя назван конкретный документ. Обещание
    // «данные обезличены (152-ФЗ)» убрали: оно стояло в подвале и в
    // этой строке, подтверждения ему в проекте нет, а решение по такой
    // формулировке за владельцем.
    h += '<p class="muted" style="margin-top:10px">Источник: ' + esc(metricSourceNote(m)) + ".</p>";
    h += "<h3 style='margin:12px 0 6px'>Провенанс числа</h3>";
    h += '<div id="drillSources"></div>';
    h += "</div>";

    $("drillBody").innerHTML = h;
    loadSources(metricId);
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
          state.data = D.build();
          showToast("Сервер не запущен: показаны значения из документов", "warn");
        }
        applyAlertsIndicators();
        renderDashboard();
        renderCharts();
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
    // PDF делается печатью браузера: в style.css есть блок @media print,
// который собирает из дашборда отчёт на A4. Собственный генератор
// PDF нужен только если требуется настоящий файл без диалога печати —
// тогда его место здесь же.
$("btnExportPdf").addEventListener("click", function () {
  showToast("Открылся диалог печати. Выберите «Сохранить как PDF».", "ok");
  window.print();
});
    $("btnExportXlsx").addEventListener("click", function () {
      // Сервер собирает книгу из той же витрины, что и карточки,
      // включая провенанс каждого числа, и отдаёт файлом.
      window.location.href = "/api/export/xlsx";
      showToast("Excel собирается на сервере — начнётся загрузка файла", "ok");
    });

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
      // Пустая выдача: без надписи ноль совпадений выглядит как поломка —
      // блоки висят на месте, а внутри них ничего нет.
      var grid = $("widgetGrid");
      var empty = $("searchEmpty");
      if (!empty) {
        empty = document.createElement("div");
        empty.id = "searchEmpty";
        empty.className = "section-card";
        empty.style.display = "none";
        grid.parentNode.insertBefore(empty, grid);
      }
      var noMatch = state.search.length > 0 && active === 0;
      grid.style.display = noMatch ? "none" : "";
      empty.style.display = noMatch ? "block" : "none";
      if (noMatch) {
        empty.innerHTML = "<b>Ничего не найдено.</b> " +
          '<span class="muted">По запросу «' + esc(state.search) +
          '» показателей нет. </span><button class="mini-btn" id="clearSearch">Сбросить поиск</button>';
        $("clearSearch").addEventListener("click", function () {
          var s = $("globalSearch");
          s.value = "";
          s.dispatchEvent(new Event("input"));
          s.focus();
        });
      }
      renderDashboard();
    });

    $("themeToggle").addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      try { localStorage.setItem("dash_theme_v2", next); } catch (e) {}
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
    var hint = role === "rector" ? "Ректор — полный доступ (вид по роли, права не проверяются)"
      : role === "prorector" ? "Проректор — финансовый блок скрыт (зона ответственности; вид по роли)"
      : "Администратор — полный доступ (вид по роли)";
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
    // Ключ сменился на v2: старая сохранённая «тёмная» больше не
    // перебивает новый светлый вид. Тёмная тема остаётся доступной
    // кнопкой «Тема».
    try { saved = localStorage.getItem("dash_theme_v2"); } catch (e) {}
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
    // Ссылка с обзора: /dashboard.html#assistant должна открыть панель,
    // иначе человек попадает на ленту и ищет кнопку сам.
    if (location.hash === "#assistant") toggleAI(true);
    var aiCloseBtn = $("aiClose");
    if (aiCloseBtn) aiCloseBtn.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); toggleAI(false); });
    // Закрыть должно быть легко: Esc, клик вне панели или кнопка «Закрыть».
    document.addEventListener("keydown", function (e) {
      var p = $("aiPanel");
      if (e.key === "Escape" && p && !p.hidden) { e.preventDefault(); toggleAI(false); }
    });
    document.addEventListener("click", function (e) {
      if ($("aiPanel").hidden) return;
      if ($("aiPanel").contains(e.target)) return;
      // Кнопка открытия тоже исключается: её клик всплывает до document,
      // и без проверки панель открылась бы и тут же закрылась.
      if ($("aiOpen") && $("aiOpen").contains(e.target)) return;
      toggleAI(false);
    });
    $("aiForm").addEventListener("submit", async function (e) {
      e.preventDefault();
      var text = $("aiInput").value.trim();
      if (!text) {
        showToast("Напишите вопрос — отправлять нечего", "warn");
        return;
      }
      // Защита от двойной отправки: пока идёт запрос, второй Enter
      // подряд давал два параллельных запроса экипажу, два индикатора
      // и «перетирал» кнопку отмены — останавливался только последний.
      if (aiBusy) return;
      aiBusy = true;
      addAiMessage("user", esc(text));
      $("aiInput").value = "";
      setAiTyping(true, true);
      // Ответ модели идёт минутами: без кнопки отмены пауза выглядит как
      // зависание. AbortController рвёт сетевой вызов, а не только
      // «прячет» ответ.
      aiAbort = new AbortController();
      try {
        var ctx = { periodLabels: periodLabels(state.period).labels, currentBlockId: state.blockId };
        var res = await AI.ask(text, state.data, ctx, aiAbort.signal);
        renderAiAnswer(res, text);
      } catch (err) {
        if (err && err.name === "AbortError") {
          addAiMessage("ai", "⏹ Остановлено по вашей команде — ответ не дождался.");
        } else {
          addAiMessage("ai", "⚠️ Ошибка: " + esc(err.message));
        }
        // Набранное возвращается в поле: после отмены или сбоя писать
        // вопрос заново — самая неприятная часть такой ошибки.
        $("aiInput").value = text;
      } finally { setAiTyping(false); aiAbort = null; aiBusy = false; }
    });
    setSuggestions();
  }

  function toggleAI(open) {
    var panel = $("aiPanel");
    if (!panel) return;
    if (open) {
      panel.hidden = false;
      panel.style.display = "flex";
      var openBtn = $("aiOpen");
      if (openBtn) openBtn.hidden = true;
      setTimeout(function(){ var inp = $("aiInput"); if(inp) inp.focus(); }, 0);
    } else {
      panel.hidden = true;
      panel.style.display = "none";
      var openBtn2 = $("aiOpen");
      if (openBtn2) openBtn2.hidden = false;
    }
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

  // Числа из витрины показываются отдельным блоком, а не только текстом
  // ответа: их посчитал код по таблице, и человек должен видеть, откуда
  // они взялись.
  function renderMetrics(m) {
    var met = m.metric;
    var h = '<div class="metrics-box">' +
      '<div class="metrics-head">' +
      '<span class="metrics-title">' + esc(met.title) + "</span>" +
      '<span class="metrics-source">' + esc(m.label || "из документа") + "</span>" +
      "</div>";

    if (m.current !== null && m.current !== undefined) {
      h += '<div class="metrics-value">' + esc(String(m.current)) +
        (met.unit ? " " + esc(met.unit) : "") + "</div>";
    }
    h += '<div class="metrics-row">';
    if (m.delta !== null && m.delta !== undefined) {
      h += '<span class="' + (m.delta >= 0 ? "up" : "down") + '">изменение ' +
        (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + " %</span> · ";
    }
    if (met.target !== null && met.target !== undefined) {
      h += "цель " + esc(String(met.target)) +
        (met.unit ? " " + esc(met.unit) : "");
      if (met.status) {
        h += ' · <span class="st st-' + esc(met.status) + '">' +
          ({ green: "в норме", yellow: "на грани", red: "ниже нормы" }[met.status] || met.status) +
          "</span>";
      }
    }
    h += "</div>";

    if (m.by_org && m.by_org.length) {
      h += '<div class="metrics-depts">по подразделениям:</div><ul>';
      m.by_org.forEach(function (o) {
        h += "<li><span>" + esc(o.title) + "</span><b>" +
          esc(String(o.value)) + (met.unit ? " " + esc(met.unit) : "") +
          "</b></li>";
      });
      h += "</ul>";
    }

    if (m.periods && m.periods.length > 1) {
      h += '<div class="metrics-depts">по периодам:</div><div class="metrics-periods">';
      m.periods.forEach(function (pp) {
        h += "<span>" + esc(pp.label) + ": <b>" + esc(String(pp.value)) +
          "</b></span>";
      });
      h += "</div>";
    }

    h += '<div class="metrics-note">' + esc(m.notice || "") + "</div>";
    h += "</div>";
    return h;
  }

  function renderCrewTrace(c) {
    var h = '<details class="crew-trace"><summary>Ход работы · ' +
      c.trace.length + " " + plural(c.trace.length, ["шаг", "шага", "шагов"]) + " · " +
      Math.round((c.latency_ms || 0) / 100) / 10 + " сек</summary>";

    c.trace.forEach(function (s) {
      var cls = s.ok ? "ok" : "fail";
      h += '<div class="crew-step ' + cls + '">' +
        '<span class="crew-mark">' + (s.ok ? "✓" : "!") + "</span>" +
        '<span class="crew-agent">' + esc(s.agent) + "</span>" +
        '<span class="crew-what">' + esc(s.step) + "</span>" +
        '<span class="crew-ms">' + (s.ms || 0) + " мс</span></div>";
      if (s.problem) h += '<div class="crew-note">замечание: ' + esc(s.problem) + "</div>";
      if (s.fix) h += '<div class="crew-note">правка: ' + esc(s.fix) + "</div>";
      if (s.reason) h += '<div class="crew-note">причина: ' + esc(s.reason) + "</div>";
      if (s.note) h += '<div class="crew-note">' + esc(s.note) + "</div>";
    });

    if (c.critic && c.critic.reason) {
      h += '<div class="crew-note">критик: ' + esc(c.critic.reason) + "</div>";
    }
    if (c.notes && c.notes.length) {
      c.notes.forEach(function (n) {
        h += '<div class="crew-note">' + esc(n) + "</div>";
      });
    }
    if (c.degraded) {
      h += '<div class="crew-note crew-warn">Агент отработал не полностью, ' +
        "ответ собран запасным путём.</div>";
    }
    h += "</details>";
    return h;
  }

  // Ожидание ответа: сколько секунд идёт запрос и возможность его
  // остановить. Текст «обычно 1–2 минуты» без счётчика не отличается
  // от зависшей страницы, а вопрос к экипажу по таймауту живёт 600 с.
  var aiAbort = null;
  var aiTick = null;
  var aiBusy = false;

  function setAiTyping(on, crewMode) {
    if (on) {
      var t = document.createElement("div");
      t.className = "msg ai typing";
      t.id = "aiTyping";
      // Вопрос проходит несколько обращений к локальной модели подряд.
      // Без пояснения пауза в минуту выглядит как зависание, и человек
      // начинает отправлять вопрос ещё раз. Сколько агентов сработает —
      // решает сервер по типу вопроса, поэтому обещать конкретный
      // состав здесь нельзя: на простой факт критик не зовётся.
      var base = crewMode
        ? "Агент работает: подбираю специалиста и источники. Обычно 1–2 минуты."
        : "Анализирую данные...";
      t.textContent = base + " ";
      var span = document.createElement("span");
      span.className = "ai-elapsed";
      span.id = "aiElapsed";
      t.appendChild(span);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mini-btn ai-cancel";
      btn.id = "aiCancel";
      btn.textContent = "⏹ Остановить";
      btn.addEventListener("click", function () {
        if (aiAbort) aiAbort.abort();
      });
      t.appendChild(btn);
      $("aiMessages").appendChild(t);
      $("aiMessages").scrollTop = $("aiMessages").scrollHeight;
      var s = 0;
      aiTick = setInterval(function () {
        s += 1;
        var el = document.getElementById("aiElapsed");
        if (el) {
          el.textContent = "· " + s + " с" +
            (s >= 60 ? " · долго, можно остановить" : "");
        }
      }, 1000);
    } else {
      if (aiTick) { clearInterval(aiTick); aiTick = null; }
      var el2 = $("aiTyping");
      if (el2) el2.remove();
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
        h += '<div class="src-item"><div class="src-head">' +
          esc("[" + s.n + "] ") + esc(s.title) +
          (s.heading ? '<span class="src-sec"> — ' + esc(s.heading) + "</span>" : "") +
          '</div><div class="src-file">' + esc(s.rel_path || "") + "</div>" +
          '<div class="src-snip">' + esc(s.snippet) + "</div></div>";
      });
      h += "</details>";
    }

    if (res.crew && res.crew.metrics && res.crew.metrics.metric) {
      h += renderMetrics(res.crew.metrics);
    }

    // Ход работы экипажа. Показываем всегда, когда он был: иначе
    // утверждение «ответ проверен критиком» неотличимо от «никто не
    // проверял», а это ровно то, чего быть не должно.
    if (res.crew && res.crew.trace && res.crew.trace.length) {
      h += renderCrewTrace(res.crew);
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
        h += '<div style="margin-top:6px">' + C.lineChart(ch.labels, [{ name: ch.title, data: ch.data, color: "#097aae" }]) + "</div>";
      });
    }

    if (res.source) h += '<div class="src">📄 ' + esc(res.source) + "</div>";
    addAiMessage("ai", h);
  }

  // ---------- Графики и диаграммы ленты ----------
  // Рисуются только из реальных данных: структура контингента, план ФХД,
  // перцентили и места E-показателей, трудоустройство. Рядов нет —
  // документы дают одну точку, поэтому вместо трендов — доли, бары,
  // тепловая карта и шкала.
  function chartPanel(title, hint, body, cls) {
    return '<div class="chart-panel' + (cls ? " " + cls : "") + '"><h4>' + esc(title) + "</h4>" +
      '<p class="chart-hint">' + esc(hint) + "</p>" + body + "</div>";
  }

  function donutSVG(parts) {
    var total = parts.reduce(function (a, b) { return a + b.value; }, 0) || 1;
    var R = 78, r = 50, cx = 100, cy = 100;
    var acc = 0, segs = "";
    parts.forEach(function (p) {
      var frac = p.value / total;
      var a0 = acc * 2 * Math.PI - Math.PI / 2;
      var a1 = (acc + frac) * 2 * Math.PI - Math.PI / 2;
      var x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0);
      var x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
      var xi0 = cx + r * Math.cos(a1), yi0 = cy + r * Math.sin(a1);
      var xi1 = cx + r * Math.cos(a0), yi1 = cy + r * Math.sin(a0);
      var large = frac > 0.5 ? 1 : 0;
      segs += '<path class="seg" d="M ' + x0.toFixed(1) + " " + y0.toFixed(1) +
        " A " + R + " " + R + " 0 " + large + " 1 " + x1.toFixed(1) + " " + y1.toFixed(1) +
        " L " + xi0.toFixed(1) + " " + yi0.toFixed(1) +
        " A " + r + " " + r + " 0 " + large + " 0 " + xi1.toFixed(1) + " " + yi1.toFixed(1) + " Z" +
        '" fill="' + p.color + '"><title>' + esc(p.label) + ": " + fmtFull(p.value) +
        " (" + Math.round(frac * 100) + " %)</title></path>";
      acc += frac;
    });
    var legend = parts.map(function (p) {
      return '<div class="legend-row"><span class="dot" style="background:' + p.color + '"></span>' +
        '<span class="legend-label">' + esc(p.label) + "</span>" +
        '<span class="legend-value">' + fmtFull(p.value) + " · " + Math.round(p.value / total * 100) + " %</span></div>";
    }).join("");
    return '<div class="donut-wrap"><svg class="chart-donut" viewBox="0 0 200 200" role="img">' + segs +
      '<text x="100" y="94" text-anchor="middle" font-size="19" font-weight="800" fill="var(--text)">' +
      fmtFull(total) + "</text>" +
      '<text x="100" y="114" text-anchor="middle" font-size="10.5" fill="var(--muted)">итого</text></svg>' +
      '<div class="donut-legend">' + legend + "</div></div>";
  }

  function hbars(rows, max) {
    max = max || rows.reduce(function (a, b) { return Math.max(a, b.value); }, 0) || 1;
    return '<div class="hbars">' + rows.map(function (r) {
      var w = Math.max(2, Math.min(100, Math.round(r.value / max * 100)));
      return '<div class="hbar-row"><span class="hbar-label">' + esc(r.label) + "</span>" +
        '<span class="hbar-track"><span class="hbar-fill" data-w="' + w + '%" style="width:0;background:' +
        (r.color || "#2563eb") + '"></span></span>' +
        '<span class="hbar-value">' + esc(r.text) + "</span></div>";
    }).join("") + "</div>";
  }

  function gauge(value, label) {
    var R = 80, C = 2 * Math.PI * R;
    var arc = Math.round(C * 0.75);
    var used = Math.round(arc * Math.max(0, Math.min(100, value || 0)) / 100);
    return '<div class="gauge"><svg viewBox="0 0 200 200" role="img">' +
      '<circle cx="100" cy="100" r="80" fill="none" stroke="var(--border)" stroke-width="14" stroke-linecap="round" ' +
      'stroke-dasharray="' + arc + " " + C + '" transform="rotate(135 100 100)"/>' +
      '<circle class="gauge-arc" cx="100" cy="100" r="80" fill="none" stroke="#16a34a" stroke-width="14" stroke-linecap="round" ' +
      'stroke-dasharray="0 ' + C + '" data-arc="' + used + " " + C + '" transform="rotate(135 100 100)"><title>' +
      esc(label) + "</title></circle></svg>" +
      '<div class="gauge-value"><span>' + fmtFull(value) + " %</span></div></div>";
  }

  function toneColor(p) {
    return p >= 66 ? "#16a34a" : p >= 33 ? "#f59e0b" : "#ef4444";
  }

  var CHART_PALETTE = ["#2563eb", "#14b8a6", "#f59e0b", "#8b5cf6", "#ec4899"];

  function radarSVG(labels, values) {
    var cx = 110, cy = 112, R = 88, n = values.length;
    function pt(i, frac) {
      var ang = -Math.PI / 2 + (2 * Math.PI * i) / n;
      return [cx + R * frac * Math.cos(ang), cy + R * frac * Math.sin(ang)];
    }
    var rings = "";
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      var pts = values.map(function (_, i) {
        var p = pt(i, f);
        return p[0].toFixed(1) + "," + p[1].toFixed(1);
      }).join(" ");
      rings += '<polygon points="' + pts + '" fill="none" stroke="' +
        (Math.abs(f - 0.5) < 1e-9 ? "var(--border-strong)" : "var(--border)") + '" stroke-width="1"/>';
    });
    var axes = values.map(function (_, i) {
      var p = pt(i, 1);
      return '<line x1="' + cx + '" y1="' + cy + '" x2="' + p[0].toFixed(1) + '" y2="' + p[1].toFixed(1) +
        '" stroke="var(--border)" stroke-width="1"/>';
    }).join("");
    var poly = values.map(function (v, i) {
      var p = pt(i, (v || 0) / 100);
      return p[0].toFixed(1) + "," + p[1].toFixed(1);
    }).join(" ");
    var dots = values.map(function (v, i) {
      var p = pt(i, (v || 0) / 100);
      return '<circle cx="' + p[0].toFixed(1) + '" cy="' + p[1].toFixed(1) + '" r="3.2" fill="#2563eb" stroke="#fff" stroke-width="1.4"><title>' +
        esc(labels[i]) + ": " + v + "</title></circle>";
    }).join("");
    var lab = values.map(function (v, i) {
      var p = pt(i, 1.22);
      var anchor = Math.abs(p[0] - cx) < 8 ? "middle" : (p[0] < cx ? "end" : "start");
      return '<text x="' + p[0].toFixed(1) + '" y="' + (p[1] + 3).toFixed(1) + '" text-anchor="' + anchor +
        '" font-size="10.5" fill="var(--muted)">' + esc(labels[i]) + " · " + v + "</text>";
    }).join("");
    return '<svg class="radar-svg" viewBox="0 0 220 224" role="img">' + rings + axes +
      '<polygon points="' + poly + '" fill="rgba(37,99,235,.14)" stroke="#2563eb" stroke-width="2"/>' +
      dots + lab + "</svg>";
  }

  function renderCharts() {
    var grid = $("chartsGrid");
    if (!grid) return;
    var m = function (id) { return state.data.metrics.find(function (x) { return x.id === id; }); };
    var val = function (id) { var x = m(id); return x ? Number(x.current) || 0 : 0; };
    var html = "";

    var mon = state.monitoring;
    var years = mon && mon.available ? Object.keys(mon.years).sort() : [];
    var last = mon && mon.available ? mon.years[years[years.length - 1]] : null;
    var E_SHORT = {
      E1: "E.1 Образование", E2: "E.2 Наука", E3: "E.3 Международная",
      E4: "E.4 Финансы", E8: "E.8 НПР со степенями"
    };
    var codes = ["E1", "E2", "E3", "E4", "E8"];

    // Динамика — главный график раздела: полный ряд 2021–2025, во всю
    // ширину, первым на странице.
    if (last) {
      var firstInd = last.indicators.E1 || {};
      var yearsHist = Object.keys(firstInd.values || {}).sort();
      var series = codes.map(function (code, i) {
        var ind = last.indicators[code];
        var vals = ind && ind.values ? ind.values : {};
        var base = vals[yearsHist[0]];
        return {
          name: code,
          color: CHART_PALETTE[i % CHART_PALETTE.length],
          data: yearsHist.map(function (y) {
            var v = vals[y];
            if (v === null || v === undefined || !base) return 0;
            return Math.round(v / base * 100);
          })
        };
      });
      html += chartPanel("Динамика E-показателей, 2021–2025",
        "индекс: 2021 = 100 · E.1 образование, E.2 наука, E.3 международная, E.4 финансы, E.8 НПР со степенями",
        '<div class="chart-wrap">' + C.lineChart(yearsHist, series) + "</div>", "chart-panel-wide");
    } else {
      html += chartPanel("Динамика E-показателей, 2021–2025",
        "сервер недоступен — данные мониторинга не загружены",
        '<p class="muted">Запустите сервер, чтобы увидеть динамику E-показателей.</p>');
    }

    html += chartPanel("Структура контингента", "всего обучающихся на 01.04.2026 · отчёт о самообследовании",
      donutSVG([
        { label: "СПО", value: val("continuer-spo"), color: "#2563eb" },
        { label: "Бакалавриат", value: val("continuer-ba"), color: "#14b8a6" },
        { label: "Магистратура", value: val("continuer-ma"), color: "#f59e0b" },
        { label: "Аспиранты", value: val("continuer-aspir"), color: "#8b5cf6" }
      ]));

    var std = (state.data.standards || []);
    var byLevel = {};
    std.forEach(function (s) { byLevel[s.level] = (byLevel[s.level] || 0) + 1; });
    html += chartPanel("Образовательные программы по уровням", "из справочника ФГОС: " + (std.length || "—") + " программ",
      donutSVG([
        { label: "Бакалавриат", value: byLevel["Бакалавриат"] || 0, color: "#2563eb" },
        { label: "Магистратура", value: byLevel["Магистратура"] || 0, color: "#14b8a6" },
        { label: "СПО", value: byLevel["СПО"] || 0, color: "#f59e0b" }
      ]));

    var income = val("income");
    var edu = val("income-edu"), sub = val("subsidy");
    var otherInc = Math.max(0, Math.round(income - edu - sub));
    html += chartPanel("План ФХД 2026: доходы", "образовательные услуги, субсидия и прочие поступления",
      donutSVG([
        { label: "Образовательные услуги", value: edu, color: "#2563eb" },
        { label: "Прочие поступления", value: otherInc, color: "#8b5cf6" },
        { label: "Субсидия", value: sub, color: "#f59e0b" }
      ]));

    var fot = val("fot");
    var rent = Math.round(income * val("arenda-share") / 100);
    var otherExp = Math.max(0, Math.round(income - fot - rent));
    html += chartPanel("План ФХД 2026: расходы", "структура расходной части плана",
      hbars([
        { label: "Фонд оплаты труда", value: fot, color: "#2563eb",
          text: fmtFull(fot) + " · " + Math.round(fot / income * 100) + " %" },
        { label: "Аренда", value: rent, color: "#14b8a6",
          text: fmtFull(rent) + " · " + Math.round(rent / income * 100) + " %" },
        { label: "Прочие расходы", value: otherExp, color: "#8b5cf6",
          text: fmtFull(otherExp) + " · " + Math.round(otherExp / income * 100) + " %" }
      ], income));

    if (last) {
      var pRows = codes.map(function (code) {
        var ind = last.indicators[code];
        if (!ind) return null;
        return {
          label: E_SHORT[code], value: ind.percentile || 0, color: toneColor(ind.percentile),
          text: ind.percentile + " · место " + ind.rank + " из " + ind.total
        };
      }).filter(Boolean);
      html += chartPanel("Перцентили E-показателей (2025)", "100 — лучший результат в области",
        hbars(pRows, 100));

      html += chartPanel("Радар: перцентили E-показателей", "полигон вуза против середины области (кольцо 50)",
        radarSVG(codes, pRows.map(function (r) { return r.value; })));

      var hmHead = '<div class="heat-corner"></div>' +
        years.map(function (y) { return '<div class="heat-head">' + esc(y) + "</div>"; }).join("");
      var hmBody = "";
      codes.forEach(function (code) {
        hmBody += '<div class="heat-rowlabel">' + esc(E_SHORT[code]) + "</div>";
        years.forEach(function (y) {
          var ind = mon.years[y].indicators[code];
          if (!ind || ind.percentile === null || ind.percentile === undefined) {
            hmBody += '<div class="heat-cell" style="background:#cbd5e1;color:#475569" title="' +
              esc(E_SHORT[code]) + " · " + y + ': нет данных">—</div>';
            return;
          }
          var p = ind.percentile;
          hmBody += '<div class="heat-cell" style="background:hsl(' + Math.round(p * 1.2) + ",62%,42%)\" title=\"" +
            esc(E_SHORT[code]) + " · " + y + " · перцентиль " + p + " · место " + ind.rank + " из " + ind.total + '">' +
            p + "</div>";
        });
      });
      html += chartPanel("Тепловая карта: перцентили 2024 → 2025", "зелёный — сильная позиция, красный — слабая",
        '<div class="heatmap" style="grid-template-columns:1fr repeat(' + years.length + ',auto)">' +
        hmHead + hmBody + "</div>");
    } else {
      html += chartPanel("E-показатели мониторинга", "сервер недоступен — данные мониторинга не загружены",
        '<p class="muted">Запустите сервер, чтобы увидеть перцентили и места.</p>');
    }

    html += chartPanel("Трудоустройство выпускников", "отчёт о самообследовании на 01.04.2026, стр. 15: «более 90 %»",
      gauge(val("trudoustroystvo"), "Трудоустройство выпускников"));

    grid.innerHTML = html;
    requestAnimationFrame(function () {
      grid.querySelectorAll(".hbar-fill").forEach(function (el) {
        el.style.width = el.getAttribute("data-w");
      });
      grid.querySelectorAll(".gauge-arc").forEach(function (el) {
        el.setAttribute("stroke-dasharray", el.getAttribute("data-arc"));
      });
    });
  }

  // ---------- Подключение к локальному серверу ----------
  // Сервер поднимает start.bat. Если его нет (например, index.html
  // открыли двойным кликом), дашборд продолжает работать на значениях
  // из документов и честно пишет об этом в шапке.
  async function connectServer() {
    if (!API) { setStatus({ offline: true }); return; }
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
      try {
        state.monitoring = await API.monitoring();
      } catch (err) {
        state.monitoring = null;
      }
      renderCharts();
      setStatus(h);
    } catch (err) {
      // Раньше здесь стоял молчаливый catch, и любая ошибка выглядела
      // как «сервер не запущен»: и недоступность сервера, и сбой
      // отрисовки после подстановки данных давали одну и ту же надпись
      // в шапке. Теперь причина попадает и в консоль, и в подсказку.
      console.error("[dashboard] не удалось подключиться к серверу:", err);
      state.server = false;
      setStatus({ offline: true, error: err && err.message });
    }
  }

  function setStatus(h) {
    updateAiStatus(h);
    var el = $("sysStatus");
    if (!el) return;
    el.className = "sys-status";
    if (h && h.offline) {
      el.classList.add("is-offline");
      if (h.error) {
        // Сервер ответил, но с данными что-то не так — это не то же
        // самое, что «сервер не запущен».
        el.textContent = "ошибка данных: " + h.error;
        el.title = "Подробности в консоли браузера (F12 → Console)";
      } else {
        el.textContent = "сервер не запущен · показаны значения из документов";
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

  // Статус ассистента в панели чата. Раньше «Онлайн» стояло в HTML
  // жёстко и кодом никогда не обновлялось — это не измерение, а
  // надпись. Теперь текст считается по /api/health в момент загрузки:
  // если модель не отвечает, это видно до первого вопроса, а не после
  // десяти минут ожидания.
  function updateAiStatus(h) {
    var el = $("aiStatus");
    if (!el) return;
    var o = (h && h.ollama) || {};
    if (h && !h.offline && o.available && o.llm_installed) {
      el.textContent = "Онлайн · " + (o.llm_model || "модель");
      el.title = "Проверено по /api/health: Ollama отвечает, модель " +
        (o.llm_model || "") + " установлена";
      el.style.color = "";
    } else if (h && !h.offline) {
      el.textContent = o.available ? "Модель не скачана" : "Модель недоступна";
      el.title = "Проверено по /api/health при загрузке страницы";
      el.style.color = "var(--red)";
    } else {
      el.textContent = "Нет связи";
      el.title = "Сервер дашборда не отвечает — чат работать не будет";
      el.style.color = "var(--red)";
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
      : D.DEPARTMENTS.map(function (d) {
          return { id: d.id, name: d.name, parentId: d.parentId };
        });
    var academies = (state.data.academies && state.data.academies.length)
      ? state.data.academies
      : (D.ACADEMIES || []);
    // Кафедры сгруппированы по академиям: кафедра — учебное ядро,
    // академия — верхний уровень сводки. В одну плоскую кучу девять
    // названий не складываются, а «Академия управления и цифрового
    // развития» в списке кафедр вообще не из чего вывести.
    var byParent = {};
    deptList.forEach(function (d) {
      var key = d.parentId || "";
      (byParent[key] = byParent[key] || []).push(d);
    });
    academies.forEach(function (a) {
      var kids = byParent[a.id];
      if (!kids || !kids.length) return;
      var g = document.createElement("optgroup");
      g.label = a.name;
      kids.forEach(function (d) {
        var o = document.createElement("option");
        o.value = d.id; o.textContent = d.name;
        g.appendChild(o);
      });
      deptSel.appendChild(g);
    });
    // Кафедры без академии (в структуре 2026–2027 таких нет) — в конец,
    // чтобы они не потерялись молча.
    (byParent[""] || []).forEach(function (d) {
      var o = document.createElement("option");
      o.value = d.id; o.textContent = d.name;
      deptSel.appendChild(o);
    });
    applyTheme();
    applyRole();
    renderDashboard();
    renderStandards();
    renderCharts();
    bindGridClicks();
    bindDetailClicks();
    bindControls();
    initAI();
    updateStamp();
    connectServer();
  }

  document.addEventListener("DOMContentLoaded", init);
})();