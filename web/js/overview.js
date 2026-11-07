/* Обзор университета в стиле Mosaic: единственный источник данных — API.
 *
 * Никаких выдуманных чисел: всё приходит из /api/metrics (витрина),
 * /api/monitoring и /api/monitoring/directions (мониторинг) и /api/health.
 * Если сервер молчит или данных нет — так и пишем, а не рисуем ноль.
 */
(function () {
  "use strict";

  var state = {
    metrics: [],
    blocks: [],
    periods: [],
    health: null,
    monitoring: null,
    directions: null,
    query: "",
    block: "all",
    span: 13,
    trend: null,
    sources: null,
    radar: null
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
    green: { text: "в норме", cls: "bg-emerald-50 text-emerald-700", bar: "bg-emerald-500", dot: "bg-emerald-500" },
    yellow: { text: "внимание", cls: "bg-amber-50 text-amber-700", bar: "bg-amber-500", dot: "bg-amber-500" },
    red: { text: "отклонение", cls: "bg-rose-50 text-rose-700", bar: "bg-rose-500", dot: "bg-rose-500" },
    none: { text: "без цели", cls: "bg-slate-100 text-slate-500", bar: "bg-slate-300", dot: "bg-slate-300" }
  };

  var KPI_IDS = ["continuer-total", "income", "trudoustroystvo", "nir"];

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

  // Плашка изменения как в референсе: мягкий фон, стрелка, процент.
  function deltaPill(metric) {
    var d = delta(metric);
    if (d === null) return "";
    var up = d >= 0;
    var good = metric.dir === "lower" ? !up : up;
    var cls = good ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700";
    return '<span class="inline-flex items-center gap-1 whitespace-nowrap rounded-lg px-2 py-0.5 text-xs font-semibold ' + cls + '">' +
      '<i data-lucide="' + (up ? "arrow-up-right" : "arrow-down-right") + '" class="h-3 w-3"></i>' + num(Math.abs(d)) + "%</span>";
  }

  function statusBadge(status) {
    var s = STATUS[status] || STATUS.none;
    if (!status || status === "none") return "";
    return '<span class="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-0.5 text-[11px] font-semibold ' + s.cls + '">' +
      '<span class="h-1.5 w-1.5 rounded-full ' + s.dot + '"></span>' + s.text + "</span>";
  }

  // ---------- KPI ----------
  function renderKpi() {
    var used = [];
    var list = [];
    KPI_IDS.forEach(function (id) {
      state.metrics.forEach(function (m) {
        if (m.id === id && used.indexOf(id) === -1) { used.push(id); list.push(m); }
      });
    });
    if (list.length < 4) {
      state.metrics.slice().sort(function (a, b) {
        return (b.target ? 1 : 0) - (a.target ? 1 : 0);
      }).forEach(function (m) {
        if (list.length < 4 && used.indexOf(m.id) === -1) { used.push(m.id); list.push(m); }
      });
    }

    $("kpi").innerHTML = list.map(function (m) {
      var icon = ICONS[m.block] || "activity";
      var secondary = [];
      if (m.target) secondary.push("цель " + num(m.target) + " " + esc(m.unit));
      secondary.push(m.status && m.status !== "none" ? STATUS[m.status].text : "один период в данных");
      return '' +
        '<article class="group rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card transition duration-200 hover:-translate-y-0.5 hover:shadow-card-hover">' +
          '<div class="flex items-start justify-between gap-3">' +
            '<div class="flex items-center gap-2.5">' +
              '<span class="flex h-8 w-8 items-center justify-center rounded-lg bg-slate-50 text-slate-600 ring-1 ring-inset ring-slate-200/70">' +
                '<i data-lucide="' + icon + '" class="h-4 w-4"></i>' +
              "</span>" +
              '<p class="text-[13px] font-medium leading-tight text-slate-500">' + esc(m.title) + "</p>" +
            "</div>" +
            deltaPill(m) +
          "</div>" +
          '<p class="mt-3.5 text-[30px] font-semibold leading-none tracking-display tabular-nums">' + num(m.current) +
            ' <span class="text-sm font-medium text-slate-400">' + esc(m.unit) + "</span></p>" +
          '<p class="mt-3 text-[12.5px] text-slate-500">' + esc(secondary.join(" · ")) + "</p>" +
        "</article>";
    }).join("");
  }

  // ---------- График ----------
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
    var labels = state.periods.slice(-state.span).map(function (p) {
      var label = String(p.label || "");
      var long = label.match(/^([а-яё]+)\s+(\d{4})$/i);
      if (long) return long[1].slice(0, 3) + " " + long[2].slice(2);
      return label.length > 6 ? label.slice(0, 3) : label;
    });
    while (labels.length < series.length) labels.unshift("");

    // Один период — рисовать нечего. Раньше на таком месте оставалась
    // пустая сетка: она выглядела как сломанный график, а не как
    // отсутствие истории. Честнее сказать это словами.
    var canvas = $("trendChart");
    var wrap = canvas.parentNode;
    var empty = wrap.querySelector(".trend-empty");
    if (series.length < 2) {
      if (state.trend) { state.trend.destroy(); state.trend = null; }
      canvas.style.display = "none";
      if (!empty) {
        empty = document.createElement("div");
        empty.className = "trend-empty flex h-full items-center justify-center px-6 text-center text-sm text-slate-500";
        wrap.appendChild(empty);
      }
      empty.textContent = "У показателя один период в документах — исторического ряда нет. " +
        "График не рисуется, чтобы не показывать выдуманную динамику.";
      $("chartTitle").textContent = m.title;
      $("chartSubtitle").textContent = "один период · " + (m.unit || "значение");
      return;
    }
    canvas.style.display = "";
    if (empty) empty.remove();

    $("chartTitle").textContent = m.title;
    $("chartSubtitle").textContent = "по периодам · " + (m.unit || "значение") +
      (m.target ? " · цель " + num(m.target) : "");

    var ctx = $("trendChart").getContext("2d");
    var gradient = ctx.createLinearGradient(0, 0, 0, 280);
    gradient.addColorStop(0, "rgba(37, 99, 235, 0.14)");
    gradient.addColorStop(1, "rgba(37, 99, 235, 0)");

    if (state.trend) state.trend.destroy();
    state.trend = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: m.title,
          data: series,
          borderColor: "#2563eb",
          backgroundColor: gradient,
          borderWidth: 2,
          fill: true,
          tension: 0.35,
          pointRadius: 0,
          pointHoverRadius: 4,
          pointHoverBackgroundColor: "#ffffff",
          pointHoverBorderColor: "#2563eb",
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
            backgroundColor: "#0f1115", padding: 10, cornerRadius: 10, displayColors: false,
            callbacks: { label: function (c) { return num(c.parsed.y) + " " + (m.unit || ""); } }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: { padding: 8, maxRotation: 0, autoSkip: true, maxTicksLimit: 7, font: { size: 11 } }
          },
          y: {
            beginAtZero: false,
            grid: { color: "#eef1f5", drawTicks: false },
            border: { display: false },
            ticks: { padding: 10, callback: function (v) { return num(v); }, font: { size: 11 } }
          }
        }
      }
    });
  }

  // ---------- Показатели по блокам: полукруг со счётчиком ----------
  function renderSources() {
    var palette = ["#2563eb", "#0ea5e9", "#14b8a6", "#f59e0b", "#8b5cf6", "#ec4899", "#64748b"];
    var counts = {};
    state.blocks.forEach(function (b) { counts[b.id] = 0; });
    state.metrics.forEach(function (m) {
      if (counts[m.block] !== undefined) counts[m.block]++;
    });
    var rows = state.blocks.map(function (b, i) {
      return { label: b.title, value: counts[b.id] || 0, color: palette[i % palette.length] };
    });
    var total = state.metrics.length;
    $("sourceTotal").textContent = num(total);

    if (state.sources) state.sources.destroy();
    state.sources = new Chart($("sourceChart"), {
      type: "doughnut",
      data: {
        labels: rows.map(function (r) { return r.label; }),
        datasets: [{
          data: rows.map(function (r) { return r.value; }),
          backgroundColor: rows.map(function (r) { return r.color; }),
          borderColor: "#ffffff",
          borderWidth: 3,
          hoverOffset: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        circumference: 180,
        rotation: 270,
        cutout: "76%",
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: "#0f1115", padding: 10, cornerRadius: 10 }
        }
      }
    });

    $("sourceLegend").innerHTML = rows.map(function (r) {
      var share = total ? Math.round((r.value / total) * 100) : 0;
      return '<li class="border-l-2 pl-2.5" style="border-color:' + r.color + '">' +
        '<p class="text-[11px] uppercase tracking-[0.06em] text-slate-400">' + esc(r.label) + "</p>" +
        '<p class="text-sm font-semibold tabular-nums text-slate-900">' + r.value + ' <span class="text-xs font-medium text-slate-400">' + share + "%</span></p>" +
      "</li>";
    }).join("");
  }

  // ---------- Исполнение целей: квадраты как в референсе ----------
  function renderGoals() {
    var withTarget = state.metrics.filter(function (m) { return !!m.target; });
    var green = withTarget.filter(function (m) { return m.status === "green"; });
    var share = withTarget.length ? Math.round((green.length / withTarget.length) * 100) : 0;

    $("goalsShare").textContent = share + "%";
    $("goalsValue").textContent = green.length + " из " + withTarget.length;
    $("goalsNote").textContent = withTarget.length
      ? "Показателей с целью: " + withTarget.length + ". В норме " + green.length +
        ", требуют внимания " + (withTarget.length - green.length) + "."
      : "Ни у одного показателя не задана цель — сравнивать не с чем.";

    $("goalsSquares").innerHTML = withTarget.map(function (m) {
      var s = STATUS[m.status] || STATUS.none;
      return '<span class="h-2.5 w-2.5 rounded-[3px] ' + s.bar + '" title="' + esc(m.title) + ' — ' + num(m.current) + " " + esc(m.unit) + '"></span>';
    }).join("");
  }

  // ---------- Состояние ----------
  function renderStatus() {
    var h = state.health;
    var rows = [
      ["Сервер", h && h.totals ? "онлайн" : "не отвечает", h && h.totals ? "text-emerald-600" : "text-rose-600"],
      ["Документов", h && h.totals ? h.totals.docs : "—", ""],
      ["Фрагментов", h && h.totals ? h.totals.chunks : "—", ""],
      ["Показателей", state.metrics.length, ""]
    ];
    $("statusList").innerHTML = rows.map(function (r) {
      return '<li class="flex items-center justify-between gap-3">' +
        '<span class="text-slate-500">' + r[0] + "</span>" +
        '<span class="font-semibold tabular-nums ' + (r[2] || "text-slate-900") + '">' + r[1] + "</span>" +
      "</li>";
    }).join("");

    var dot = $("railStatus");
    dot.className = "h-2 w-2 rounded-full " + (h && h.totals ? "bg-emerald-500" : "bg-rose-500");
  }

  // Сегментная полоса как в референсе: 20 делений, заполнено по значению,
  // цвет по коридору — зелёный от 66, жёлтый от 33, красный ниже.
  function scoreBar(pct) {
    var total = 20;
    var value = Math.max(0, Math.min(100, Number(pct) || 0));
    var filled = Math.round(value / 100 * total);
    var color = value >= 66 ? "bg-emerald-500" : value >= 33 ? "bg-amber-400" : "bg-rose-500";
    var parts = [];
    for (var i = 0; i < total; i++) {
      parts.push('<span class="h-1.5 w-1.5 rounded-[2px] ' + (i < filled ? color : "bg-slate-200") + '"></span>');
    }
    return '<span class="inline-flex items-center gap-[2px]">' + parts.join("") + "</span>";
  }

  // ---------- Место среди вузов области: радар ----------
  // Полные названия осей не влезают вокруг радара — на графике короткие,
  // полные остаются в таблице справа.
  var MON_SHORT = {
    E1: "Образование",
    E2: "Наука",
    E3: "Международная",
    E4: "Финансы",
    E8: "Доп. показатель"
  };

  function wrapLabel(axis) {
    var short = MON_SHORT[axis.code];
    if (short) return short;
    var text = String(axis.label || "").replace(/^E\.\d+\.\s*/, "");
    if (text.length <= 14) return text;
    var words = text.split(" ");
    var mid = Math.ceil(words.length / 2);
    return [words.slice(0, mid).join(" "), words.slice(mid).join(" ")];
  }

  // Какие показатели раздела III показывать для каждого блока ленты.
  // Полного соответствия у сайта нет: «ВНОКО» и «воспитательной работы»
  // в мониторинге эффективности не существует, поэтому там честный
  // отказ, а не выдуманный радар.
  var BLOCK_MONITORING_CODES = {
    academic: ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8",
               "1.9", "1.10", "1.11", "1.12", "1.13", "1.14", "1.15"],
    results: ["1.3", "1.4", "1.11", "1.12"],
    vnoko: ["1.3", "1.4", "1.15"],
    finance: ["4.1", "4.2", "4.3"],
    science: ["2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8",
              "2.9", "2.10", "2.11", "2.12", "2.13"],
    digital: ["5.6", "5.7", "5.8"],
    upr: []
  };

  var DIRECTION_INDEX = null;

  function directionIndex() {
    if (DIRECTION_INDEX) return DIRECTION_INDEX;
    var map = {};
    var sections = (state.directions && state.directions.sections) || [];
    sections.forEach(function (section) {
      (section.indicators || []).forEach(function (ind) {
        map[ind.code] = { ind: ind, section: section };
      });
    });
    DIRECTION_INDEX = map;
    return map;
  }

  function blockIndicators(blockId) {
    var codes = BLOCK_MONITORING_CODES[blockId] || [];
    var map = directionIndex();
    return codes.map(function (code) { return map[code]; }).filter(Boolean);
  }

  function ratioCell(ind, kind) {
    var capped = kind === "private" ? ind.median_private_capped : ind.median_region_capped;
    var ratio = kind === "private" ? ind.median_private_ratio : ind.median_region_ratio;
    var median = kind === "private" ? ind.median_private : ind.median_region;
    if (capped) {
      return '<span class="inline-flex items-center rounded-md bg-amber-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-700" ' +
        'title="Значение не менее чем в 2 раза выше медианы: шкала на сайте обрезана">≥ 2×</span>';
    }
    if (median === null || median === undefined || ratio === null || ratio === undefined) {
      return '<span class="text-slate-300">—</span>';
    }
    return '<span class="inline-flex flex-col items-end">' +
      '<span class="font-semibold tabular-nums text-slate-700">×' + Number(ratio).toFixed(2) + "</span>" +
      '<span class="text-[10px] text-slate-400">' + num(median) + "</span></span>";
  }

  function renderBlockMonitoring(blockId) {
    var block = (state.blocks || []).filter(function (b) { return b.id === blockId; })[0];
    var items = blockIndicators(blockId);
    var release = (state.directions && state.directions.release) || "2025";

    $("monitoringTitle").textContent = "Блок: " + (block ? block.title : blockId) + " · мониторинг";
    $("monitoringSubtitle").textContent = "Показатели раздела III · сравнение с медианами частных вузов и региона";
    $("monitoringAvg").textContent = items.length ? "показателей: " + items.length : "нет данных";
    $("monitoringYears").textContent = "выпуск " + release;

    if (!items.length) {
      if (state.radar) { state.radar.destroy(); state.radar = null; }
      $("radarWrap").hidden = true;
      $("radarLegend").innerHTML = "";
      $("monitoringTable").innerHTML =
        '<p class="text-sm leading-relaxed text-slate-500">В мониторинге эффективности нет показателей по блоку «' +
        esc(block ? block.title : blockId) + '». Ближайшие данные — раздел IV (контингент и УГН) на странице «Мониторинг».</p>';
      return;
    }
    $("radarWrap").hidden = false;

    var labels = items.map(function (item) { return item.ind.code; });
    function ratios(kind) {
      return items.map(function (item) {
        var r = kind === "private" ? item.ind.median_private_ratio : item.ind.median_region_ratio;
        if (r === null || r === undefined) return 0;
        return Math.min(2, Number(r) || 0);
      });
    }
    var baseline = items.map(function () { return 1; });

    if (state.radar) state.radar.destroy();
    state.radar = new Chart($("radarChart"), {
      type: "radar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Медиана (1×)",
            data: baseline,
            borderColor: "#94a3b8",
            backgroundColor: "transparent",
            borderWidth: 1,
            borderDash: [4, 3],
            pointRadius: 0,
            fill: false,
            order: 10
          },
          {
            label: "К медиане частных вузов",
            data: ratios("private"),
            borderColor: "#0ea5e9",
            backgroundColor: "rgba(14,165,233,.10)",
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0ea5e9",
            pointBorderWidth: 2,
            fill: true
          },
          {
            label: "К медиане региона",
            data: ratios("region"),
            borderColor: "#b45309",
            backgroundColor: "rgba(180,83,9,.08)",
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#b45309",
            pointBorderWidth: 2,
            fill: true
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: 6 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f1115", padding: 10, cornerRadius: 10,
            callbacks: {
              label: function (ctx) {
                var item = items[ctx.dataIndex] || {};
                var ind = item.ind || {};
                var value = Number(ctx.parsed.r);
                if (ctx.dataset.label === "Медиана (1×)") return "медиана: ×1.00";
                var kind = ctx.dataset.label.indexOf("частных") !== -1 ? "private" : "region";
                var median = kind === "private" ? ind.median_private : ind.median_region;
                var capped = kind === "private" ? ind.median_private_capped : ind.median_region_capped;
                var text = ctx.dataset.label + ": ×" + value.toFixed(2);
                if (capped) text += " (≥ 2×)";
                else if (value === 0) text += " (сравнения нет)";
                else if (median !== null && median !== undefined) text += " · медиана " + num(median);
                return text;
              }
            }
          }
        },
        scales: {
          r: {
            min: 0, max: 2,
            ticks: { stepSize: 0.5, showLabelBackdrop: false, color: "#94a3b8", font: { size: 9 } },
            grid: { color: "#e8edf3" },
            angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 9 } }
          }
        }
      }
    });

    $("radarLegend").innerHTML = [
      { color: "#94a3b8", text: "медиана (1×)" },
      { color: "#0ea5e9", text: "к медиане частных вузов" },
      { color: "#b45309", text: "к медиане региона" }
    ].map(function (row) {
      return '<span class="inline-flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-full" style="background:' +
        row.color + '"></span>' + esc(row.text) + "</span>";
    }).join("");

    var head = '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 border-b border-slate-100 pb-2 text-[10.5px] uppercase tracking-[0.06em] text-slate-400">' +
      "<span>Показатель</span><span class=\"text-right\">Значение</span>" +
      '<span class="text-right">К медиане частных</span><span class="text-right">К медиане региона</span></div>';
    var rows = items.map(function (item) {
      var ind = item.ind;
      return '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto_auto] gap-x-3 border-b border-slate-50 py-2 text-[13px] last:border-0">' +
        '<span class="min-w-0 pr-2 leading-snug text-slate-600"><span class="font-mono text-[11px] text-slate-400">' + esc(ind.code) + "</span> " +
        esc(ind.title) + ' <span class="text-[10.5px] text-slate-400">' + esc(ind.unit || "") + "</span></span>" +
        '<span class="text-right font-semibold tabular-nums text-slate-900">' + num(ind.value) + "</span>" +
        '<span class="text-right">' + ratioCell(ind, "private") + "</span>" +
        '<span class="text-right">' + ratioCell(ind, "region") + "</span>" +
        "</div>";
    }).join("");
    $("monitoringTable").innerHTML = head + rows +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">Источник: monitoring.miccedu.ru, раздел III. ' +
      "×1 — уровень медианы, «≥ 2×» — не менее двух медиан (шкала на сайте обрезана), «—» — сравнения нет.</p>";
  }

  function renderMonitoring() {
    if (!state.monitoring || !state.monitoring.available) {
      $("monitoringSection").hidden = true;
      return;
    }
    $("monitoringSection").hidden = false;
    if (state.block && state.block !== "all") {
      renderBlockMonitoring(state.block);
    } else {
      renderMonitoringAll();
    }
  }

  function renderMonitoringAll() {
    var data = state.monitoring;
    if (!data || !data.available) {
      $("monitoringSection").hidden = true;
      return;
    }
    $("monitoringSection").hidden = false;
    $("monitoringTitle").textContent = "Место среди вузов Ростовской области";
    $("monitoringSubtitle").textContent =
      "мониторинг эффективности · процентиль: 100 — лучший результат в области";
    $("radarWrap").hidden = false;

    var years = Object.keys(data.years || {}).sort();
    var axes = data.axes || [];
    var table = data.table || [];
    var first = years[0], last = years[years.length - 1];

    $("monitoringYears").textContent = years.length
      ? data.years[first].total + " → " + data.years[last].total + " вузов"
      : "";

    var avgFirst = data.years[first] ? data.years[first].average_percentile : 0;
    var avgLast = data.years[last] ? data.years[last].average_percentile : 0;
    $("monitoringAvg").textContent = "средний процентиль " + avgFirst + " → " + avgLast;

    var colors = ["#94a3b8", "#2563eb", "#0d9488"];
    var datasets = years.map(function (year, i) {
      var color = colors[i] || colors[colors.length - 1];
      return {
        label: year,
        data: data.series[year] || [],
        borderColor: color,
        backgroundColor: color + "1f",
        pointBackgroundColor: "#ffffff",
        pointBorderColor: color,
        pointBorderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        borderWidth: 2,
        fill: true
      };
    });

    if (state.radar) state.radar.destroy();
    state.radar = new Chart($("radarChart"), {
      type: "radar",
      data: { labels: axes.map(function (a) { return wrapLabel(a); }), datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        layout: { padding: 6 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f1115", padding: 10, cornerRadius: 10,
            callbacks: {
              label: function (ctx) {
                var row = table[ctx.dataIndex];
                var info = row && row.years[ctx.dataset.label];
                var extra = info ? " · место " + info.rank + " из " + info.total : "";
                return ctx.dataset.label + ": " + ctx.parsed.r + " процентиль" + extra;
              }
            }
          }
        },
        scales: {
          r: {
            min: 0,
            max: 100,
            ticks: { stepSize: 25, showLabelBackdrop: false, color: "#94a3b8", font: { size: 10 } },
            grid: { color: "#e8edf3" },
            angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 11 } }
          }
        }
      }
    });

    // Легенда своим HTML: в канвасе она не влезала под радар и обрезалась.
    $("radarLegend").innerHTML = years.map(function (year, i) {
      var color = colors[i] || colors[colors.length - 1];
      return '<span class="inline-flex items-center gap-2">' +
        '<span class="h-2.5 w-2.5 rounded-full" style="background:' + color + '"></span>' +
        year + "</span>";
    }).join("");

    // Таблица: значение и место по каждому показателю за оба года.
    var head = '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 border-b border-slate-100 pb-2 text-[11px] uppercase tracking-[0.06em] text-slate-400">' +
      "<span>Показатель</span>" +
      years.map(function (y) { return '<span class="text-right">' + y + "</span>"; }).join("") +
      "</div>";

    var rows = table.map(function (row) {
      var cells = years.map(function (year) {
        var info = row.years[year];
        if (!info) return '<span class="text-right text-slate-300">—</span>';
        var tone = info.rank <= 5 ? "text-emerald-600" : info.rank <= 15 ? "text-slate-900" : "text-slate-500";
        return '<span class="flex flex-col items-end gap-1">' +
          '<span class="whitespace-nowrap">' +
          '<span class="font-semibold tabular-nums ' + tone + '">' + num(info.value) + "</span>" +
          '<span class="ml-2 text-[11px] text-slate-400">' + info.rank + "/" + info.total + "</span></span>" +
          scoreBar(info.percentile) +
          "</span>";
      }).join("");
      return '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 border-b border-slate-50 py-2 text-[13px] last:border-0">' +
        '<span class="min-w-0 pr-2 leading-snug text-slate-600">' + esc(row.label.replace(/^E\.\d+\.\s*/, "")) +
        ' <span class="text-[11px] text-slate-400">' + esc(row.unit) + "</span></span>" + cells +
        "</div>";
    }).join("");

    $("monitoringTable").innerHTML = head + rows +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">' +
      "Источник: monitoring.miccedu.ru. Место — среди вузов Ростовской области, участвовавших в мониторинге. " +
      "Показатель E.3 считается по вузам, у которых он заполнен, поэтому знаменатель меньше." +
      "</p>";
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
      var raw = (Number(m.current) / Number(m.target)) * 100;
      var bar = Math.max(0, Math.min(100, raw));
      progress = '<div class="mt-3 h-1 overflow-hidden rounded-full bg-slate-100">' +
        '<div class="h-1 rounded-full ' + s.bar + '" style="width:' + bar.toFixed(0) + '%"></div></div>' +
        '<p class="mt-1.5 text-[11px] text-slate-400">' + num(raw) + "% от цели " + num(m.target) + "</p>";
    } else {
      progress = '<p class="mt-3 text-[11px] text-slate-400">' +
        (m.source === "official" ? "официальный документ" : "источник не указан") + "</p>";
    }
    return '<div class="rounded-xl border border-slate-100 p-4 transition-colors hover:border-slate-200 hover:bg-slate-50/60">' +
      '<div class="flex items-start justify-between gap-2">' +
        '<p class="min-h-[36px] text-[13px] leading-snug text-slate-600">' + esc(m.title) + "</p>" +
        statusBadge(m.status) +
      "</div>" +
      '<p class="mt-1 text-[21px] font-semibold tracking-display tabular-nums">' + num(m.current) +
        ' <span class="text-[11px] font-medium text-slate-400">' + esc(m.unit) + "</span></p>" +
      progress +
    "</div>";
  }

  function renderBlocks() {
    var html = "";
    state.blocks.forEach(function (b) {
      var items = state.metrics.filter(function (m) { return m.block === b.id && matches(m); });
      if (!items.length) return;
      html += '<article class="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-card">' +
        '<div class="flex items-start gap-3 border-b border-slate-100 px-5 py-4">' +
          '<span class="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-600 ring-1 ring-inset ring-slate-200/70">' +
            '<i data-lucide="' + (ICONS[b.id] || "activity") + '" class="h-4 w-4"></i></span>' +
          '<div class="min-w-0">' +
            '<h3 class="text-[15px] font-semibold tracking-tight text-slate-900">' + esc(b.title) + "</h3>" +
            '<p class="mt-0.5 text-[13px] text-slate-500">' + esc(b.desc || "") + "</p>" +
          "</div>" +
          '<span class="ml-auto shrink-0 rounded-lg bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">' + items.length + "</span>" +
        "</div>" +
        '<div class="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">' +
          items.map(metricTile).join("") +
        "</div>" +
      "</article>";
    });
    $("blocks").innerHTML = html;
    var empty = $("emptyState");
    empty.hidden = html !== "";
    if (html === "") {
      if (state.query) {
        empty.textContent = "По запросу ничего не найдено. Измените слово поиска или выберите другой блок.";
      } else if (state.block !== "all") {
        empty.textContent = "В этом блоке пока нет подтверждённых документами показателей.";
      } else {
        empty.textContent = "Показателей пока нет.";
      }
    }
  }

  function renderChips() {
    var chips = [{ id: "all", title: "Все блоки" }].concat(state.blocks);
    $("blockChips").innerHTML = chips.map(function (b) {
      var active = state.block === b.id;
      return '<button data-block="' + b.id + '" class="rounded-lg border px-3 py-1.5 text-[13px] font-medium transition ' +
        (active ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900") +
        '">' + esc(b.title) + "</button>";
    }).join("");
    var selected = state.block === "all" ? null : state.blocks.filter(function (b) {
      return b.id === state.block;
    })[0];
    var hint = $("blocksHint");
    if (hint) hint.textContent = selected ? selected.title : "все блоки";
  }

  // ---------- Заголовок ----------
  function renderHeader() {
    var now = new Date();
    var date = now.toLocaleDateString("ru-RU", { day: "numeric", month: "long", weekday: "long" });
    $("todayLine").textContent = date.charAt(0).toUpperCase() + date.slice(1);
    $("subtitle").textContent = state.metrics.length + " показателей в " + state.blocks.length +
      " блоках · данные из витрины и документов";
  }

  // ---------- События ----------
  function wire() {
    var rail = $("rail"), overlay = $("overlay");
    function openRail() {
      rail.classList.remove("-translate-x-full");
      overlay.classList.remove("hidden");
      document.body.classList.add("overflow-hidden");
    }
    function closeRail() {
      rail.classList.add("-translate-x-full");
      overlay.classList.add("hidden");
      document.body.classList.remove("overflow-hidden");
    }
    $("openRail").addEventListener("click", openRail);
    $("closeRail").addEventListener("click", closeRail);
    overlay.addEventListener("click", closeRail);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeRail(); });

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
      renderMonitoring();
      lucide.createIcons();
    });

    document.querySelectorAll(".chart-span").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.span = Number(btn.getAttribute("data-span")) || 13;
        document.querySelectorAll(".chart-span").forEach(function (b) {
          b.className = "chart-span rounded-lg px-3 py-1 text-xs font-medium text-slate-500 transition hover:text-slate-700";
        });
        btn.className = "chart-span rounded-lg bg-white px-3 py-1 text-xs font-semibold text-slate-800 shadow-card";
        renderTrend();
      });
    });
  }

  // ---------- Загрузка ----------
  function boot() {
    Promise.all([
      get("/api/metrics"),
      get("/api/health").catch(function () { return null; }),
      get("/api/monitoring").catch(function () { return null; }),
      get("/api/monitoring/directions").catch(function () { return null; })
    ]).then(function (res) {
      var metrics = res[0] || {};
      state.metrics = metrics.metrics || [];
      state.blocks = metrics.blocks || [];
      state.periods = metrics.periods || [];
      state.health = res[1];
      state.monitoring = res[2];
      state.directions = res[3];
      DIRECTION_INDEX = null;

      renderHeader();
      renderStatus();
      renderChips();
      renderKpi();
      renderTrend();
      renderSources();
      renderMonitoring();
      renderGoals();
      renderBlocks();
      lucide.createIcons();
    }).catch(function (err) {
      $("subtitle").textContent = "Не удалось загрузить данные: " + err.message;
      $("railStatus").className = "h-2 w-2 rounded-full bg-rose-500";
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
