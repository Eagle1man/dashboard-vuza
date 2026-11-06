/* Обзор университета: единственный источник данных — API дашборда.
 *
 * Никаких выдуманных чисел: всё, что на странице, приходит из
 * /api/metrics (витрина), /api/docs (индекс документов) и /api/health.
 * Если сервер недоступен или данных нет — так и пишем, а не рисуем ноль.
 */
(function () {
  "use strict";

  var state = {
    metrics: [],
    blocks: [],
    periods: [],
    docs: [],
    demo: false,
    query: "",
    block: "all",
    span: 13,
    trend: null,
    sources: null
  };

  var ICONS = {
    academic: "graduation-cap",
    results: "book-open-check",
    finance: "wallet",
    vnoko: "badge-check",
    science: "flask-conical",
    upr: "users",
    digital: "monitor-smartphone"
  };

  var STATUS = {
    green: { text: "в норме", cls: "bg-emerald-50 text-emerald-700", dot: "bg-emerald-500", bar: "bg-emerald-500" },
    yellow: { text: "внимание", cls: "bg-amber-50 text-amber-700", dot: "bg-amber-500", bar: "bg-amber-500" },
    red: { text: "отклонение", cls: "bg-rose-50 text-rose-700", dot: "bg-rose-500", bar: "bg-rose-500" },
    none: { text: "без цели", cls: "bg-slate-100 text-slate-500", dot: "bg-slate-300", bar: "bg-slate-300" }
  };

  // Что показываем крупно наверху: самые «ректорские» показатели.
  var KPI_IDS = ["continuer-total", "income", "trudoustroystvo", "nagruzka-pps"];

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return "—";
    var n = Number(value);
    if (!isFinite(n)) return String(value);
    var abs = Math.abs(n);
    var digits = abs >= 100 ? 0 : abs >= 1 ? 1 : 2;
    return n.toLocaleString("ru-RU", { maximumFractionDigits: digits });
  }

  function get(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    });
  }

  function delta(metric) {
    if (metric.delta !== null && metric.delta !== undefined) return Number(metric.delta);
    var cur = Number(metric.current);
    var prev = Number(metric.prev);
    if (!isFinite(cur) || !isFinite(prev) || prev === 0) return null;
    return Math.round(((cur - prev) / prev) * 1000) / 10;
  }

  function deltaPill(metric) {
    var d = delta(metric);
    if (d === null) {
      return '<span class="text-xs text-slate-400">один период в данных</span>';
    }
    var up = d >= 0;
    // Для показателей, где меньше — лучше (dir = lower), рост означает ухудшение.
    var good = metric.dir === "lower" ? !up : up;
    var cls = good ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700";
    var icon = up ? "arrow-up-right" : "arrow-down-right";
    return '<span class="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-1 text-xs font-semibold ' + cls + '">' +
      '<i data-lucide="' + icon + '" class="h-3.5 w-3.5 shrink-0"></i>' + num(Math.abs(d)) + "%</span>" +
      '<span class="text-xs text-slate-500">к прошлому периоду</span>';
  }

  function statusBadge(status) {
    var s = STATUS[status] || STATUS.none;
    if (status === "none" || !status) return "";
    return '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-1 text-[11px] font-semibold ' + s.cls + '">' +
      '<span class="h-1.5 w-1.5 rounded-full ' + s.dot + '"></span>' + s.text + "</span>";
  }

  // ---------- KPI ----------
  function kpiMetric(id, used) {
    for (var i = 0; i < state.metrics.length; i++) {
      if (state.metrics[i].id === id && used.indexOf(id) === -1) return state.metrics[i];
    }
    return null;
  }

  function renderKpi() {
    var used = [];
    var list = [];
    KPI_IDS.forEach(function (id) {
      var m = kpiMetric(id, used);
      if (m) { used.push(id); list.push(m); }
    });
    // Если чего-то из списка нет — добираем показателями с целью.
    if (list.length < 4) {
      state.metrics.slice().sort(function (a, b) {
        return (b.target ? 1 : 0) - (a.target ? 1 : 0);
      }).forEach(function (m) {
        if (list.length < 4 && used.indexOf(m.id) === -1) { used.push(m.id); list.push(m); }
      });
    }

    $("kpi").innerHTML = list.map(function (m) {
      var icon = ICONS[m.block] || "activity";
      var target = m.target ? '<span class="text-xs text-slate-500">цель ' + num(m.target) + " " + esc(m.unit) + "</span>" : "";
      return '' +
        '<article class="group flex h-full min-h-[168px] flex-col rounded-2xl border border-slate-200/80 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md sm:min-h-[190px]">' +
          '<div class="mb-4 flex items-start justify-between">' +
            '<p class="text-sm font-medium leading-snug text-slate-500">' + esc(m.title) + "</p>" +
            '<span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">' +
              '<i data-lucide="' + icon + '" class="h-5 w-5"></i>' +
            "</span>" +
          "</div>" +
          '<p class="text-3xl font-semibold tracking-tight tabular-nums">' + num(m.current) +
            ' <span class="text-base font-medium text-slate-400">' + esc(m.unit) + "</span></p>" +
          '<div class="mt-auto space-y-2 pt-3">' +
            '<div class="flex flex-wrap items-center gap-2">' + deltaPill(m) + "</div>" +
            (target || m.status ? '<div class="flex flex-wrap items-center gap-2">' + target + statusBadge(m.status) + "</div>" : "") +
          "</div>" +
        "</article>";
    }).join("");
  }

  // ---------- Графики ----------
  function trendMetric() {
    var best = null;
    state.metrics.forEach(function (m) {
      if (!best || (m.series || []).length > (best.series || []).length) best = m;
    });
    return best;
  }

  function renderTrend() {
    var m = trendMetric();
    if (!m) return;

    var series = (m.series || []).slice(-state.span);
    // В витрине встречаются и короткие «Янв», и длинные «март 2026» —
    // на оси такие подписи наезжают друг на друга.
    var labels = state.periods.slice(-state.span).map(function (p) {
      var label = String(p.label || "");
      var long = label.match(/^([а-яё]+)\s+(\d{4})$/i);
      if (long) return long[1].slice(0, 3) + " " + long[2].slice(2);
      return label.length > 6 ? label.slice(0, 3) : label;
    });
    while (labels.length < series.length) labels.unshift("");

    $("chartTitle").textContent = m.title;
    $("chartSubtitle").textContent = "по периодам · " + esc(m.unit || "значение") +
      (m.target ? " · цель " + num(m.target) : "");

    var ctx = $("trendChart").getContext("2d");
    var gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, "rgba(79, 70, 229, 0.22)");
    gradient.addColorStop(1, "rgba(79, 70, 229, 0)");

    if (state.trend) state.trend.destroy();
    state.trend = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: m.title,
          data: series,
          borderColor: "#4f46e5",
          backgroundColor: gradient,
          borderWidth: 2.5,
          fill: true,
          tension: 0.4,
          pointRadius: 0,
          pointHoverRadius: 5,
          pointHoverBackgroundColor: "#ffffff",
          pointHoverBorderColor: "#4f46e5",
          pointHoverBorderWidth: 3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f172a", padding: 12, cornerRadius: 10, displayColors: false,
            callbacks: { label: function (c) { return num(c.parsed.y) + " " + (m.unit || ""); } }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            // На телефоне 13 подписей не влезают: Chart.js начинает их
            // наклонять и обрезает последнюю. Просим прореживать.
            ticks: { padding: 8, maxRotation: 0, autoSkip: true, maxTicksLimit: 7 }
          },
          y: {
            beginAtZero: false,
            grid: { color: "#f1f5f9", drawTicks: false },
            border: { display: false },
            ticks: { padding: 10, callback: function (v) { return num(v); } }
          }
        }
      }
    });
  }

  function renderSources() {
    var official = 0, demo = 0;
    state.metrics.forEach(function (m) { if (m.source === "official") official++; else demo++; });
    var total = official + demo;
    $("sourceTotal").textContent = num(total);

    if (state.sources) state.sources.destroy();
    state.sources = new Chart($("sourceChart"), {
      type: "doughnut",
      data: {
        labels: ["Официальные документы", "Демонстрационные"],
        datasets: [{
          data: [official, demo],
          backgroundColor: ["#6366f1", "#cbd5e1"],
          borderColor: "#ffffff",
          borderWidth: 4,
          hoverOffset: 6
        }]
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "72%",
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: "#0f172a", padding: 10, cornerRadius: 10 }
        }
      }
    });

    var rows = [
      { label: "Официальные документы", color: "bg-indigo-500", value: official },
      { label: "Демонстрационные", color: "bg-slate-300", value: demo }
    ];
    $("sourceLegend").innerHTML = rows.map(function (r) {
      var share = total ? Math.round((r.value / total) * 100) : 0;
      return '<li class="flex items-center gap-2.5 text-sm">' +
        '<span class="h-2.5 w-2.5 rounded-full ' + r.color + '"></span>' +
        '<span class="text-slate-600">' + r.label + "</span>" +
        '<span class="ml-auto font-semibold tabular-nums text-slate-900">' + share + "%</span>" +
        "</li>";
    }).join("");
  }

  // ---------- Блоки ----------
  function matches(metric) {
    if (state.block !== "all" && metric.block !== state.block) return false;
    if (!state.query) return true;
    var hay = (metric.title + " " + metric.id + " " + (metric.unit || "")).toLowerCase();
    return hay.indexOf(state.query.toLowerCase()) !== -1;
  }

  function metricTile(m) {
    var s = STATUS[m.status] || STATUS.none;
    var progress = "";
    if (m.target) {
      var rawPct = (Number(m.current) / Number(m.target)) * 100;
      var barPct = Math.max(0, Math.min(100, rawPct));
      progress = '<div class="mt-3 h-1.5 overflow-hidden rounded-full bg-slate-100">' +
        '<div class="h-1.5 rounded-full ' + s.bar + '" style="width:' + barPct.toFixed(0) + '%"></div></div>' +
        // В подписи — настоящий процент, а не обрезанный: 116% от цели это
        // не «100%», и скрывать перевыполнение нельзя.
        '<p class="mt-1.5 text-[11px] text-slate-400">' + num(rawPct) + "% от цели " + num(m.target) + " " + esc(m.unit) + "</p>";
    } else {
      progress = '<p class="mt-3 text-[11px] text-slate-400">' + (m.source === "official" ? "официальный документ" : "демонстрационное значение") + "</p>";
    }
    return '<div class="rounded-xl border border-slate-100 p-4 transition hover:border-slate-200 hover:shadow-sm">' +
      '<div class="flex items-start justify-between gap-2">' +
        '<p class="text-sm leading-snug text-slate-600">' + esc(m.title) + "</p>" +
        statusBadge(m.status) +
      "</div>" +
      '<p class="mt-2 text-xl font-semibold tabular-nums">' + num(m.current) +
        ' <span class="text-xs font-medium text-slate-400">' + esc(m.unit) + "</span></p>" +
      progress +
    "</div>";
  }

  function renderBlocks() {
    var html = "";
    state.blocks.forEach(function (b) {
      var items = state.metrics.filter(function (m) { return m.block === b.id && matches(m); });
      if (!items.length) return;
      html += '<article class="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-sm">' +
        '<div class="flex items-start gap-3 border-b border-slate-100 px-5 py-4">' +
          '<span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">' +
            '<i data-lucide="' + (ICONS[b.id] || "activity") + '" class="h-5 w-5"></i></span>' +
          '<div class="min-w-0">' +
            '<h3 class="text-base font-semibold tracking-tight text-slate-900">' + esc(b.title) + "</h3>" +
            '<p class="mt-0.5 text-sm text-slate-500">' + esc(b.desc || "") + "</p>" +
          "</div>" +
          '<span class="ml-auto shrink-0 rounded-lg bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-600">' + items.length + "</span>" +
        "</div>" +
        '<div class="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">' +
          items.map(metricTile).join("") +
        "</div>" +
      "</article>";
    });
    $("blocks").innerHTML = html;
    $("emptyState").hidden = html !== "";
  }

  function renderChips() {
    var chips = [{ id: "all", title: "Все блоки" }].concat(state.blocks);
    $("blockChips").innerHTML = chips.map(function (b) {
      var active = state.block === b.id;
      return '<button data-block="' + b.id + '" class="rounded-xl border px-3 py-1.5 text-sm font-medium transition ' +
        (active ? "border-indigo-200 bg-indigo-50 text-indigo-700" : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50") +
        '">' + esc(b.title) + "</button>";
    }).join("");
  }

  // ---------- Документы ----------
  function docBadge(isDemo) {
    return isDemo
      ? '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-amber-50 px-2 py-1 text-[11px] font-semibold text-amber-700"><span class="h-1.5 w-1.5 rounded-full bg-amber-500"></span>демо</span>'
      : '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-emerald-50 px-2 py-1 text-[11px] font-semibold text-emerald-700"><span class="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>официальный</span>';
  }

  function renderDocs() {
    var docs = state.docs.slice(0, 8);
    $("docsSubtitle").textContent = state.docs.length
      ? "всего " + state.docs.length + " документов · по ним ассистент отвечает на вопросы"
      : "пока ни одного документа";

    $("docsTable").innerHTML = docs.map(function (d) {
      return "<tr class=\"transition hover:bg-slate-50/70\">" +
        '<td class="px-3 py-4 sm:px-5"><p class="font-medium text-slate-900">' + esc(d.title) + "</p>" +
          '<p class="text-xs text-slate-500">id ' + d.doc_id + "</p></td>" +
        '<td class="px-3 py-4 text-slate-600 sm:px-5">' + esc(d.doc_type || "—") + "</td>" +
        '<td class="px-3 py-4 text-right font-semibold tabular-nums sm:px-5">' + num(d.n_chunks) + "</td>" +
        '<td class="px-3 py-4 sm:px-5">' + docBadge(d.is_demo) + "</td>" +
      "</tr>";
    }).join("");

    $("docsCards").innerHTML = docs.map(function (d) {
      return '<li class="flex items-start justify-between gap-4 px-5 py-4">' +
        '<div class="min-w-0">' +
          '<p class="font-medium leading-snug text-slate-900">' + esc(d.title) + "</p>" +
          '<p class="mt-1 text-xs text-slate-500">' + esc(d.doc_type || "—") + " · фрагментов " + num(d.n_chunks) + "</p>" +
        "</div>" +
        '<div class="shrink-0">' + docBadge(d.is_demo) + "</div>" +
      "</li>";
    }).join("");
  }

  // ---------- Статус сервера ----------
  function renderStatus(health) {
    var ok = health && health.totals;
    $("statusText").textContent = ok ? "Сервер онлайн" : "Сервер не отвечает";
    $("statusDot").className = "h-2 w-2 rounded-full " + (ok ? "bg-emerald-300" : "bg-rose-300");
    if (ok) {
      $("statusDetails").textContent = "документов " + health.totals.docs +
        " · фрагментов " + health.totals.chunks;
    }
    $("updatedAt").textContent = "данные на " + new Date().toLocaleString("ru-RU", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit"
    });
  }

  // ---------- События ----------
  function wire() {
    var sidebar = $("sidebar"), overlay = $("overlay");
    function openSidebar() {
      sidebar.classList.remove("-translate-x-full");
      overlay.classList.remove("hidden");
      document.body.classList.add("overflow-hidden");
    }
    function closeSidebar() {
      sidebar.classList.add("-translate-x-full");
      overlay.classList.add("hidden");
      document.body.classList.remove("overflow-hidden");
    }
    $("openSidebar").addEventListener("click", openSidebar);
    $("closeSidebar").addEventListener("click", closeSidebar);
    overlay.addEventListener("click", closeSidebar);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeSidebar(); });

    $("searchInput").addEventListener("input", function (e) {
      state.query = e.target.value.trim();
      renderBlocks();
      lucide.createIcons();
    });

    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        $("searchInput").focus();
      }
    });

    $("blockChips").addEventListener("click", function (e) {
      var btn = e.target.closest("[data-block]");
      if (!btn) return;
      state.block = btn.getAttribute("data-block");
      renderChips();
      renderBlocks();
      lucide.createIcons();
    });

    document.querySelectorAll(".chart-span").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.span = Number(btn.getAttribute("data-span")) || 13;
        document.querySelectorAll(".chart-span").forEach(function (b) {
          b.className = "chart-span rounded-lg px-2.5 py-1 text-xs font-medium text-slate-500 transition hover:text-slate-700";
        });
        btn.className = "chart-span rounded-lg bg-white px-2.5 py-1 text-xs font-semibold text-slate-700 shadow-sm";
        renderTrend();
      });
    });
  }

  // ---------- Загрузка ----------
  function boot() {
    Promise.all([
      get("/api/metrics"),
      get("/api/docs").catch(function () { return { documents: [] }; }),
      get("/api/health").catch(function () { return null; })
    ]).then(function (res) {
      var metrics = res[0] || {};
      state.metrics = metrics.metrics || [];
      state.blocks = metrics.blocks || [];
      state.periods = metrics.periods || [];
      state.demo = !!metrics.demo;

      var docs = res[1] || {};
      state.docs = docs.documents || docs.docs || docs.items || [];

      $("demoBadge").hidden = !state.demo;
      $("subtitle").textContent = state.metrics.length
        ? state.metrics.length + " показателей в " + state.blocks.length + " блоках · данные из витрины и документов"
        : "Витрина пуста — загрузите документы";

      renderStatus(res[2]);
      renderChips();
      renderKpi();
      renderTrend();
      renderSources();
      renderBlocks();
      renderDocs();
      lucide.createIcons();
    }).catch(function (err) {
      $("subtitle").textContent = "Не удалось загрузить данные: " + err.message;
      $("statusText").textContent = "Сервер не отвечает";
      lucide.createIcons();
    });

    wire();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  // Tailwind CDN применяет стили после первой отрисовки — графики,
  // построенные раньше, застревают с неправильной шириной.
  window.addEventListener("load", function () {
    Object.values(Chart.instances).forEach(function (c) { c.resize(); });
  });
})();
