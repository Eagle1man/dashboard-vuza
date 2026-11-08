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
    spo: null,
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
    if (!$("trendChart")) return;
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
  // Подробное описание каждой цели: что за показатель, почему он важен
  // для места в рейтинге и какими мерами его достигают. Тексты опираются
  // на показатели ленты дашборда и карточку monitoring.miccedu.ru.
  var GOAL_GUIDE = {
    E1: {
      what: "Средний балл ЕГЭ студентов, принятых на обучение по очной форме. У ИУБиП приём идёт на платной основе — бюджетного набора нет, поэтому в расчёт берётся балл платного приёма. Показатель напрямую измеряет качество абитуриентов и стартовый уровень первокурсников.",
      why: "В рейтинге вуз ниже середины области: пока средний балл отстаёт от медианы региона, E.1 тянет средний процентиль места вниз.",
      how: "Усилить приёмную кампанию: профориентация в школах и колледжах, дни открытых дверей, олимпиады, скидки и гранты для высокобалльников, целевые договоры с работодателями. В 2025 балл уже вырос с 57,4 до 60,7 (+5,8 %) — динамику нужно закрепить на всех 25 программах. Контроль: «Принято на 1 курс» (объём приёма) и средний балл принятых — последнего в ленте нет, его стоит завести."
    },
    E2: {
      what: "Объём научно-исследовательских и опытно-конструкторских работ в расчёте на одного научно-педагогического работника. Показывает, сколько средств на науку приносит каждый НПР.",
      why: "Сильнейшая позиция вуза — 5-е место в области. Но в 2025 значение снизилось с 444 до 420 тыс. ₽ (−5,5 %): если падение продолжится, вуз потеряет главный вклад в место.",
      how: "Вернуть объём НИОКР к уровню 2024 года и выше: хоздоговоры с предприятиями, гранты, заказные НИОКР, публикации в журналах «Белого списка». Важен и знаменатель: рост штата НПР без роста НИОКР ухудшает показатель. Контроль: «Объём НИР за 2025» (18 млн ₽) ежеквартально; численность НПР — завести в ленте."
    },
    E3: {
      what: "Доля иностранных студентов (из СНГ и дальнего зарубежья) в приведённом контингенте высшего образования. Характеризует международную привлекательность университета.",
      why: "Самый резкий спад среди E-показателей: доля упала с 8,2 % до 4,4 % (−46,9 %), перцентиль 42 — один из трёх самых слабых показателей вуза.",
      how: "Восстановить международный набор: агентские и партнёрские программы, набор из СНГ, подготовительное отделение, продвижение программ за рубежом. Одновременно растёт знаменатель — контингент ВО, поэтому нужен параллельный рост бакалавриата и магистратуры. Контроль ежемесячно: «Иностранцы» (55 чел.)."
    },
    E4: {
      what: "Доходы университета из всех источников в расчёте на одного НПР: образовательные услуги, субсидия, НИОКР и прочие поступления. Показывает финансовую устойчивость на одного преподавателя.",
      why: "Позиция выше среднего (перцентиль 65), но доходы на НПР снизились с 4 579 до 4 426 тыс. ₽ (−3,3 %) — запас перед медианой тает.",
      how: "Наращивать доходную часть: расширение платного приёма и программ ДПО (главная статья — образовательные услуги, 184 447 тыс. ₽), внебюджетная НИР, полное освоение субсидии (8 287 тыс. ₽). План ФХД 2026 (221 475 тыс. ₽) уже закладывает рост — ключевое исполнение доходной части. Численность НПР — завести в ленте."
    },
    E8: {
      what: "Численность НПР с учёными степенями (кандидаты и доктора наук) на 100 студентов. Показывает научную зрелость преподавательского состава.",
      why: "Самый слабый E-показатель вуза — 23-е место в области. Именно его рост даёт наибольший прирост среднего перцентиля и, значит, места в рейтинге.",
      how: "Стимулировать защиты диссертаций (30 аспирантов — готовый кадровый резерв), привлекать остепенённых преподавателей и совместителей, поддерживать публикационную активность. Знаменатель — 100 студентов: рост контингента должен сопровождаться ростом числа остепенённых НПР. Показатель в ленте отсутствует — завести и вести ежеквартально."
    }
  };

  // Шкала прогресса цели: заливка — 2025 к цели, метка — 2024,
  // правый край — цель. Стиль — как у диаграмм ленты (синяя заливка,
  // зелёная — когда цель выполнена).
  function goalBarHtml(row) {
    var cur = row.cur, goal = row.goal, old = row.old;
    var pctVal = goal ? Math.max(0, Math.min(100, Math.round(cur / goal * 100))) : 0;
    var oldPct = (goal && old !== null && old !== undefined)
      ? Math.min(100, Math.round(old / goal * 100)) : null;
    var done = cur >= goal;
    var fill = done ? "#16a34a" : "#2563eb";
    var diff = Math.round((goal - cur) * 10) / 10;
    var chip = done
      ? '<span class="rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-700">цель достигнута</span>'
      : '<span class="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-700">' +
        (diff > 0 ? "ещё +" : "") + num(diff) + (row.unit ? " " + esc(row.unit) : "") + " до цели</span>";
    var tick = oldPct !== null
      ? '<span class="absolute -top-0.5 bottom-0 w-0.5 rounded bg-slate-400" style="left:' + oldPct +
        '%" title="2024: ' + num(old) + '"></span>'
      : "";
    return '<div class="mt-2 flex items-center gap-3">' +
      '<div class="relative h-2 flex-1 overflow-hidden rounded-full bg-slate-100" title="цель: ≥ ' + num(goal) + '">' +
        '<span class="absolute inset-y-0 left-0 rounded-full" style="width:' + pctVal + "%;background:" + fill + '"></span>' +
        tick +
      "</div>" +
      '<span class="w-10 text-right text-[11px] font-semibold tabular-nums ' +
        (done ? "text-emerald-600" : "text-slate-500") + '">' + pctVal + " %</span>" +
      chip +
    "</div>";
  }

  function renderGoals() {
    var data = state.monitoring;
    var share = $("goalsShare");
    var value = $("goalsValue");
    var list = $("goalsSquares");
    var note = $("goalsNote");

    if (!data || !data.available || !data.years) {
      share.textContent = "нет данных";
      value.textContent = "—";
      list.innerHTML = "";
      note.textContent = "Данных мониторинга нет: цели не из чего построить.";
      return;
    }

    var years = Object.keys(data.years).sort();
    var last = years[years.length - 1];
    var prev = years[years.length - 2] || null;
    $("goalsTitle").textContent = "Цели на " + (Number(last) + 1);
    var subtitle = $("goalsSubtitle");
    if (subtitle) {
      subtitle.textContent = (prev ? prev + " → " : "") + last + " → цель";
    }
    var axes = (data.e_axes || data.axes || []).filter(function (axis) {
      return axis.code !== "E5"; // E.5 не публикуется в выпуске 2025
    });
    var rows = [];
    axes.forEach(function (axis) {
      var curInd = ((data.years[last] || {}).indicators || {})[axis.code];
      var oldInd = prev ? (((data.years[prev] || {}).indicators || {})[axis.code]) : null;
      var cur = curInd ? curInd.value : null;
      if (cur === null || cur === undefined) return;
      var old = oldInd ? oldInd.value : null;
      var dir = "higher"; // E-показатели: больше — лучше
      var goal = dir === "higher"
        ? Math.ceil(cur * 1.05 * 10) / 10
        : Math.floor(cur * 0.95 * 10) / 10;
      rows.push({ code: axis.code, label: axis.label, unit: axis.unit,
                  old: old, cur: cur, goal: goal, dir: dir });
    });

    if (!rows.length) {
      share.textContent = "0 целей";
      value.textContent = "—";
      list.innerHTML = "";
      note.textContent = "В мониторинге нет показателей с двумя выпусками — цель не построить.";
      return;
    }

    share.textContent = rows.length + " целей";
    value.textContent = rows.length;
    list.innerHTML = rows.map(function (row) {
      var label = String(row.label || "").replace(/^E\.\d+\.\s*/, "");
      var mark = row.dir === "higher" ? "≥" : "≤";
      var tone = row.dir === "higher" ? "text-emerald-700" : "text-rose-700";
      return '<div class="flex w-full items-baseline justify-between gap-2 border-b border-slate-50 py-1.5 text-[12px] last:border-0" title="' +
        esc(row.label + (row.unit ? " · " + row.unit : "")) + '">' +
        '<span class="min-w-0 truncate text-slate-600"><span class="font-mono text-[10.5px] text-slate-400">' +
        esc(row.code) + "</span> " + esc(label) + "</span>" +
        '<span class="whitespace-nowrap tabular-nums">' +
          '<span class="text-slate-400">' + (row.old === null ? "—" : num(row.old)) + "</span>" +
          ' <span class="text-slate-300">→</span> ' +
          '<span class="font-semibold text-slate-900">' + num(row.cur) + "</span>" +
          ' <span class="text-slate-300">→</span> ' +
          '<span class="font-semibold ' + tone + '">' + mark + " " + num(row.goal) + "</span>" +
        "</span></div>";
    }).join("");
    note.textContent = "База — " + last + ". Цель: превысить " + last +
      " на 5% по позитивным показателям; негативные снизить на 5%. " +
      "Позитивных: " + rows.length + ", негативных: 0. " +
      "Шкала под каждой целью: синяя заливка — " + last + " к цели, серый штрих — " +
      (prev || "предыдущий выпуск") + ", правый край — цель. Ниже — что это за показатель, почему он важен и как его достичь.";

    var detail = $("goalsDetail");
    if (detail) {
      detail.innerHTML = rows.map(function (row) {
        var guide = GOAL_GUIDE[row.code] || { what: "", why: "", how: "" };
        var label = String(row.label || "").replace(/^E\.\d+\.\s*/, "");
        var mark = "≥";
        var ind = ((state.monitoring.years[last] || {}).indicators || {})[row.code] || {};
        var med = ind.medians || {};
        var tone = percentileTone(ind.percentile);
        var meta = (ind.rank
          ? '<p class="mt-0.5 text-[11.5px] text-slate-500">место ' + ind.rank + " из " + ind.total +
            ' · перцентиль <span class="font-semibold ' + tone + '">' + ind.percentile + "</span>" +
            (med.region !== null && med.region !== undefined
              ? " · медиана региона " + num(med.region) + (row.unit ? " " + esc(row.unit) : "") : "") + "</p>"
          : "");
        return '<div class="border-t border-slate-100 py-3 first:border-t-0 first:pt-1">' +
          '<div class="flex flex-wrap items-center justify-between gap-2">' +
            '<p class="font-semibold text-slate-800"><span class="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10.5px] text-slate-500">' +
            esc(row.code) + "</span> <span class=\"ml-1.5\">" + esc(label) + "</span>" +
            ' <span class="text-[11px] font-normal text-slate-400">' + esc(row.unit || "") + "</span></p>" +
            '<p class="whitespace-nowrap tabular-nums text-[12px]">' +
              '<span class="text-slate-400">' + (row.old === null ? "—" : num(row.old)) + "</span>" +
              ' <span class="text-slate-300">→</span> ' +
              '<span class="font-semibold text-slate-900">' + num(row.cur) + "</span>" +
              ' <span class="text-slate-300">→</span> ' +
              '<span class="font-semibold text-emerald-700">' + mark + " " + num(row.goal) + "</span>" +
            "</p>" +
          "</div>" + goalBarHtml(row) + meta +
          '<p class="mt-1.5 text-[12.5px] leading-relaxed text-slate-600"><span class="font-semibold text-slate-700">Что это.</span> ' + esc(guide.what) + "</p>" +
          '<p class="mt-1 text-[12.5px] leading-relaxed text-slate-600"><span class="font-semibold text-slate-700">Почему важно.</span> ' + esc(guide.why) + "</p>" +
          '<p class="mt-1 text-[12.5px] leading-relaxed text-slate-600"><span class="font-semibold text-slate-700">Как достичь.</span> ' + esc(guide.how) + "</p>" +
        "</div>";
      }).join("");
    }
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
    var statusList = $("statusList");
    if (statusList) {
      statusList.innerHTML = rows.map(function (r) {
        return '<li class="flex items-center justify-between gap-3">' +
          '<span class="text-slate-500">' + r[0] + "</span>" +
          '<span class="font-semibold tabular-nums ' + (r[2] || "text-slate-900") + '">' + r[1] + "</span>" +
        "</li>";
      }).join("");
    }

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
    E8: "НПР со степенями"
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

  // Оценка по медиане. В разделе III «больше — лучше», поэтому
  // отношение > 1 — хорошо (зелёный), < 1 — плохо (оранжевый),
  // ровно 1 — нейтрально. Если медиана не публикуется, сравнения нет:
  // остаётся серый прочерк, а не выдуманный цвет.
  var TONE_GOOD = "text-emerald-600";
  var TONE_BAD = "text-orange-600";
  var TONE_SAME = "text-slate-500";

  function ratioValue(ind, kind) {
    var capped = kind === "private" ? ind.median_private_capped : ind.median_region_capped;
    var median = kind === "private" ? ind.median_private : ind.median_region;
    var ratio = kind === "private" ? ind.median_private_ratio : ind.median_region_ratio;
    if (ratio === null || ratio === undefined) return null;
    var value = Number(ratio);
    if (!isFinite(value) || value <= 0) return null;
    if (!capped && (median === null || median === undefined)) return null;
    return value;
  }

  function ratioTone(value) {
    var shown = Number(Number(value).toFixed(2));
    if (shown > 1) return TONE_GOOD;
    if (shown < 1) return TONE_BAD;
    return TONE_SAME;
  }

  function valueTone(ind) {
    var ratios = [ratioValue(ind, "private"), ratioValue(ind, "region")]
      .filter(function (value) { return value !== null; });
    if (!ratios.length) return "text-slate-900";
    var shown = ratios.map(function (value) { return Number(value.toFixed(2)); });
    if (shown.some(function (value) { return value < 1; })) return TONE_BAD;
    if (shown.every(function (value) { return value > 1; })) return TONE_GOOD;
    return "text-slate-900";
  }

  function ratioCell(ind, kind) {
    var capped = kind === "private" ? ind.median_private_capped : ind.median_region_capped;
    var median = kind === "private" ? ind.median_private : ind.median_region;
    if (capped) {
      return '<span class="inline-flex items-center rounded-md bg-emerald-50 px-1.5 py-0.5 text-[10.5px] font-semibold text-emerald-700" ' +
        'title="Значение не менее чем в 2 раза выше медианы: шкала на сайте обрезана">≥ 2×</span>';
    }
    var value = ratioValue(ind, kind);
    if (value === null || median === null || median === undefined) {
      return '<span class="text-slate-300">—</span>';
    }
    var tone = ratioTone(value);
    var label = tone === TONE_GOOD ? "выше медианы" : tone === TONE_BAD ? "ниже медианы" : "ровно медиана";
    return '<span class="inline-flex flex-col items-end">' +
      '<span class="font-semibold tabular-nums ' + tone + '" title="' + label + '">×' + value.toFixed(2) + "</span>" +
      '<span class="text-[10px] text-slate-400">' + num(median) + "</span></span>";
  }

  // Радар и таблица стоят в двух колонках. Для СПО радар не нужен:
  // прячем левую колонку и отдаём всю ширину таблице.
  function showMonitoringRadar() {
    var radarCol = $("monitoringRadarCol");
    if (radarCol) radarCol.hidden = false;
    var grid = $("monitoringGrid");
    if (grid) grid.style.gridTemplateColumns = "";
  }

  function renderBlockMonitoring(blockId) {
    showMonitoringRadar();
    var block = (state.blocks || []).filter(function (b) { return b.id === blockId; })[0];
    var items = blockIndicators(blockId);
    var release = (state.directions && state.directions.release) || "2025";

    $("monitoringTitle").textContent = "Блок: " + (block ? block.title : blockId) + " · мониторинг";
    $("monitoringSubtitle").textContent = "Показатели вуза (ИУБиП) и медианы частных вузов / региона";
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

    function positive(value) {
      var number = Number(value);
      return isFinite(number) && number !== 0 ? number : null;
    }

    var vuzData = [], regionData = [], privateData = [], allValues = [];
    items.forEach(function (item) {
      var ind = item.ind;
      var vuz = positive(ind.value);
      var region = positive(ind.median_region);
      var priv = positive(ind.median_private);
      var base = region || priv;
      if (vuz === null || base === null) {
        // Сравнения нет: на сайте такая точка стоит в центре.
        vuzData.push(0);
        regionData.push(region ? 1 : null);
        privateData.push(priv ? (region ? priv / region : 1) : null);
        return;
      }
      vuzData.push(vuz / base);
      regionData.push(region ? 1 : null);
      privateData.push(priv ? priv / base : null);
      allValues.push(vuz / base);
      if (region) allValues.push(1);
      if (priv) allValues.push(priv / base);
    });
    var max = Math.max(1.2, Math.min(5, Math.max.apply(
      null, allValues.filter(function (value) { return isFinite(value); })
        .concat([1])) * 1.08));
    var step = max <= 2 ? 0.5 : 1;

    if (state.radar) state.radar.destroy();
    state.radar = new Chart($("radarChart"), {
      type: "radar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Показатели вуза (ИУБиП)",
            data: vuzData,
            borderColor: "#0f766e",
            backgroundColor: "rgba(15,118,110,.12)",
            borderWidth: 2.5,
            pointRadius: 3.5,
            pointHoverRadius: 5,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0f766e",
            pointBorderWidth: 2,
            fill: true,
            order: 10
          },
          {
            label: "Медиана региона",
            data: regionData,
            borderColor: "#b45309",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#b45309"
          },
          {
            label: "Медиана частных вузов",
            data: privateData,
            borderColor: "#0ea5e9",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0ea5e9"
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
                if (!isFinite(value)) return "";
                var unit = ind.unit ? " " + ind.unit : "";
                if (ctx.dataset.label.indexOf("Показатели вуза") === 0) {
                  if (value === 0) return "ИУБиП: " + num(ind.value) + unit + " (сравнения нет)";
                  return "ИУБиП: " + num(ind.value) + unit +
                    " (×" + value.toFixed(2) + " к медиане)";
                }
                var isRegion = ctx.dataset.label.indexOf("региона") !== -1;
                var median = isRegion ? ind.median_region : ind.median_private;
                var capped = isRegion ? ind.median_region_capped : ind.median_private_capped;
                if (value === 0) return ctx.dataset.label + ": сравнения нет";
                return ctx.dataset.label + ": " + num(median) + unit +
                  (capped ? " (≥ 2×)" : "") + " (×" + value.toFixed(2) + ")";
              }
            }
          }
        },
        scales: {
          r: {
            min: 0, max: max,
            ticks: { stepSize: step, showLabelBackdrop: false, color: "#94a3b8", font: { size: 9 } },
            grid: { color: "#e8edf3" },
            angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 9 } }
          }
        }
      }
    });

    $("radarLegend").innerHTML = [
      { color: "#0f766e", text: "показатели вуза (ИУБиП)" },
      { color: "#b45309", text: "медиана региона" },
      { color: "#0ea5e9", text: "медиана частных вузов" }
    ].map(function (row) {
      return '<span class="inline-flex items-center gap-2"><span class="h-2.5 w-2.5 rounded-full" style="background:' +
        row.color + '"></span>' + esc(row.text) + "</span>";
    }).join("");

    var head = '<div class="overflow-x-auto"><table class="w-full min-w-[720px] text-left text-sm">' +
      '<thead><tr class="border-b border-slate-100 text-[10.5px] uppercase tracking-[0.06em] text-slate-400">' +
      '<th scope="col" class="py-2 pr-3 font-semibold">Показатель</th>' +
      '<th scope="col" class="whitespace-nowrap px-2 py-2 text-right font-semibold">Значение</th>' +
      '<th scope="col" class="whitespace-nowrap px-2 py-2 text-right font-semibold">К медиане частных</th>' +
      '<th scope="col" class="whitespace-nowrap py-2 pl-3 text-right font-semibold">К медиане региона</th>' +
      "</tr></thead><tbody>";
    var rows = items.map(function (item) {
      var ind = item.ind;
      return '<tr class="border-b border-slate-50 text-[13px] transition-colors last:border-0 hover:bg-slate-50/60">' +
        '<td class="min-w-0 py-2 pr-3 align-top leading-snug text-slate-600"><span class="font-mono text-[11px] text-slate-400">' + esc(ind.code) + "</span> " +
        esc(ind.title) + ' <span class="text-[10.5px] text-slate-400">' + esc(ind.unit || "") + "</span></td>" +
        '<td class="px-2 py-2 text-right align-top font-semibold tabular-nums ' + valueTone(ind) + '">' + num(ind.value) + "</td>" +
        '<td class="px-2 py-2 text-right align-top">' + ratioCell(ind, "private") + "</td>" +
        '<td class="py-2 pl-3 text-right align-top">' + ratioCell(ind, "region") + "</td>" +
        "</tr>";
    }).join("");
    $("monitoringTable").innerHTML = head + rows + "</tbody></table></div>" +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">Источник: monitoring.miccedu.ru, раздел III. ' +
      "×1 — уровень медианы, «≥ 2×» — не менее двух медиан (шкала на сайте обрезана), «—» — сравнения нет. " +
      "Зелёный — выше медианы, оранжевый — ниже.</p>";
  }

  // ---------- СПО ----------
  var SPO_FALLBACK_SECTIONS = [
    { code: "1", title: "Образовательная деятельность" },
    { code: "2", title: "Международная деятельность" },
    { code: "3", title: "Уровень подготовки выпускников" },
    { code: "4", title: "Инфраструктура" },
    { code: "5", title: "Кадровый состав" },
    { code: "6", title: "Социальная ответственность" },
    { code: "7", title: "Инклюзивное образование" }
  ];

  function spoSections(data) {
    var sections = (data && data.sections) || [];
    return sections.length ? sections : SPO_FALLBACK_SECTIONS;
  }

  function spoDelta(delta) {
    if (delta === null || delta === undefined) {
      return '<span class="text-slate-300">—</span>';
    }
    var tone = delta > 0 ? "text-emerald-600" : delta < 0 ? "text-orange-600" : "text-slate-500";
    var sign = delta > 0 ? "+" : "";
    return '<span class="font-semibold tabular-nums ' + tone + '">' + sign + num(delta) + "%</span>";
  }

  function renderSpoBlock() {
    var data = state.spo;
    $("monitoringSection").hidden = false;
    $("monitoringTitle").textContent = "СПО: показатели ИУБиП";
    $("monitoringSubtitle").textContent =
      "Среднее профессиональное образование · ИУБиП, область и РФ";
    $("monitoringYears").textContent = data && data.total
      ? "организаций: " + data.total
      : "";
    $("monitoringAvg").textContent = data && data.rows
      ? "показателей: " + data.rows.length
      : "нет данных";

    $("radarWrap").hidden = true;
    $("radarLegend").innerHTML = "";
    var radarCol = $("monitoringRadarCol");
    if (radarCol) radarCol.hidden = true;
    var grid = $("monitoringGrid");
    if (grid) grid.style.gridTemplateColumns = "minmax(0, 1fr)";
    if (state.radar) { state.radar.destroy(); state.radar = null; }

    if (!data || !data.available || !(data.rows || []).length) {
      $("monitoringTable").innerHTML =
        '<p class="text-sm leading-relaxed text-slate-500">Данных СПО нет: файл не собран.</p>';
      return;
    }

    var years = data.years || [];
    var first = years[0], last = years[years.length - 1];
    var rows = data.rows || [];

    function rowHtml(row) {
      var a = row.years[first] || {};
      var b = row.years[last] || {};
      var unitLine = row.unit && String(row.title || "").toLowerCase()
        .indexOf(row.unit.toLowerCase()) === -1
        ? '<p class="text-[10.5px] text-slate-400">' + esc(row.unit) + "</p>"
        : "";
      var place = (b.rank && b.total)
        ? '<span class="inline-flex flex-col items-end gap-1"><span class="whitespace-nowrap">' +
          '<span class="font-semibold tabular-nums">' + b.rank + "</span>" +
          '<span class="text-[11px] text-slate-400"> / ' + b.total + "</span></span>" +
          scoreBar(b.percentile) + "</span>"
        : '<span class="text-slate-300">—</span>';
      return '<tr class="transition-colors hover:bg-slate-50/60">' +
        '<td class="px-3 py-2"><p class="leading-snug text-slate-700">' + esc(row.title) + "</p>" +
          unitLine + "</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-500">' + num(a.org) +
          (a.mapped ? '<span class="ml-0.5 text-[10px] text-slate-400" title="В 2024 году показатель учитывался под другим кодом">*</span>' : "") + "</td>" +
        '<td class="px-3 py-2 text-right font-semibold tabular-nums">' + num(b.org) + "</td>" +
        '<td class="px-3 py-2 text-right">' + spoDelta(b.delta) + "</td>" +
        '<td class="px-3 py-2 text-right">' + place + "</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-600">' + num(b.region) + "</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-500">' + num(b.rf) + "</td>" +
        "</tr>";
    }

    var body = spoSections(data).map(function (section) {
      var items = rows.filter(function (row) {
        return String(row.code).split(".")[0] === section.code;
      });
      if (!items.length) return "";
      return '<tr class="bg-slate-50/80"><td colspan="7" class="px-3 py-2 text-[11px] font-semibold uppercase tracking-[0.07em] text-slate-500">' +
        esc(section.code) + ". " + esc(section.title) + "</td></tr>" +
        items.map(rowHtml).join("");
    }).join("");

    $("monitoringTable").innerHTML =
      '<div class="overflow-x-auto"><table class="w-full min-w-[980px] text-left text-sm">' +
      '<thead><tr class="border-b border-slate-100 text-[10.5px] uppercase tracking-[0.07em] text-slate-400">' +
        '<th class="px-3 py-2 font-semibold">Показатель</th>' +
        '<th class="px-3 py-2 text-right font-semibold">' + esc(first) + "</th>" +
        '<th class="px-3 py-2 text-right font-semibold">' + esc(last) + "</th>" +
        '<th class="px-3 py-2 text-right font-semibold">Δ</th>' +
        '<th class="px-3 py-2 text-right font-semibold">Место в области</th>' +
        '<th class="px-3 py-2 text-right font-semibold">Область</th>' +
        '<th class="px-3 py-2 text-right font-semibold">РФ</th>' +
      "</tr></thead><tbody class=\"divide-y divide-slate-50\">" + body + "</tbody></table></div>" +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">Источник: monitoring.miccedu.ru, СПО, карточка организации id=1971. ' +
      "Место — среди " + (data.total || "—") + " организаций СПО Ростовской области; область и РФ — медианные значения. " +
      "По численности студентов место отражает размер, а не качество. За 2024 год показатели, которых не было в прежней методике, отмечены прочерком; «*» — показатель учитывался под другим кодом.</p>";
  }

  function renderMonitoring() {
    if (state.block === "spo") {
      renderSpoBlock();
      return;
    }
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
    showMonitoringRadar();
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

    $("monitoringSubtitle").textContent =
      "показатели вуза и медианы по РФ, субъекту и ведомственной принадлежности";

    var release = data.years[last] || {};
    var indicators = release.indicators || {};

    function positive(value) {
      var number = Number(value);
      return isFinite(number) && number !== 0 ? number : null;
    }

    var vuzData = [], rfData = [], regionData = [], deptData = [];
    var allValues = [];
    axes.forEach(function (axis) {
      var ind = indicators[axis.code] || {};
      var vuz = positive(ind.value);
      var med = ind.medians || {};
      var rf = positive(med.rf);
      var region = positive(med.region);
      var dept = positive(med.department);
      var base = rf || region || vuz;
      if (vuz === null || base === null || base === 0) {
        vuzData.push(null); rfData.push(null);
        regionData.push(null); deptData.push(null);
        return;
      }
      vuzData.push(vuz / base);
      rfData.push(rf ? 1 : null);
      regionData.push(region ? region / base : null);
      deptData.push(dept ? dept / base : null);
      allValues.push(vuz / base);
      if (region) allValues.push(region / base);
      if (dept) allValues.push(dept / base);
    });
    var max = Math.max(1.2, Math.min(5, Math.max.apply(
      null, allValues.filter(function (value) { return isFinite(value); })
        .concat([1])) * 1.08));
    var step = max <= 2 ? 0.5 : 1;

    if (state.radar) state.radar.destroy();
    state.radar = new Chart($("radarChart"), {
      type: "radar",
      data: {
        labels: axes.map(function (a) { return wrapLabel(a); }),
        datasets: [
          {
            label: "Показатели вуза (ИУБиП)",
            data: vuzData,
            borderColor: "#0f766e",
            backgroundColor: "rgba(15,118,110,.12)",
            borderWidth: 2.5,
            pointRadius: 3.5,
            pointHoverRadius: 5,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0f766e",
            pointBorderWidth: 2,
            fill: true,
            order: 10
          },
          {
            label: "Медианы вузов/филиалов по РФ",
            data: rfData,
            borderColor: "#86efac",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#86efac"
          },
          {
            label: "Медианы по субъекту РФ",
            data: regionData,
            borderColor: "#a78bfa",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#a78bfa"
          },
          {
            label: "Медианы по ведомственной принадлежности",
            data: deptData,
            borderColor: "#22c55e",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            pointRadius: 2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#22c55e"
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
                var axis = axes[ctx.dataIndex] || {};
                var ind = indicators[axis.code] || {};
                var value = Number(ctx.parsed.r);
                if (!isFinite(value)) return "";
                var unit = axis.unit ? " " + axis.unit : "";
                if (ctx.dataset.label.indexOf("Показатели вуза") === 0) {
                  return "ИУБиП: " + num(ind.value) + unit +
                    " (×" + value.toFixed(2) + " к медиане РФ)";
                }
                var med = ind.medians || {};
                var absolute = ctx.dataset.label.indexOf("субъекту") !== -1
                  ? med.region
                  : ctx.dataset.label.indexOf("ведомственной") !== -1
                    ? med.department
                    : med.rf;
                return ctx.dataset.label + ": " + num(absolute) + unit +
                  " (×" + value.toFixed(2) + ")";
              }
            }
          }
        },
        scales: {
          r: {
            min: 0,
            max: max,
            ticks: { stepSize: step, showLabelBackdrop: false, color: "#94a3b8", font: { size: 10 } },
            grid: { color: "#e8edf3" },
            angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 10 } }
          }
        }
      }
    });

    // Легенда своим HTML: в канвасе она не влезала под радар и обрезалась.
    $("radarLegend").innerHTML = [
      { color: "#0f766e", text: "показатели вуза (ИУБиП)" },
      { color: "#86efac", text: "медианы вузов/филиалов по РФ" },
      { color: "#a78bfa", text: "медианы по субъекту РФ" },
      { color: "#22c55e", text: "медианы по ведомственной принадлежности" }
    ].map(function (row) {
      return '<span class="inline-flex items-center gap-2">' +
        '<span class="h-2.5 w-2.5 rounded-full" style="background:' + row.color + '"></span>' +
        esc(row.text) + "</span>";
    }).join("");

    // Таблица: значение и место по каждому показателю за оба года.
    var head = '<div class="overflow-x-auto"><table class="w-full min-w-[620px] text-left text-sm">' +
      '<thead><tr class="border-b border-slate-100 text-[11px] uppercase tracking-[0.06em] text-slate-400">' +
      '<th scope="col" class="py-2 pr-3 font-semibold">Показатель</th>' +
      years.map(function (y) {
        return '<th scope="col" class="whitespace-nowrap px-2 py-2 text-right font-semibold">' + y + "</th>";
      }).join("") +
      "</tr></thead><tbody>";

    var rows = table.map(function (row) {
      var cells = years.map(function (year) {
        var info = row.years[year];
        if (!info) return '<td class="px-2 py-2 text-right align-top text-slate-300">—</td>';
        var tone = info.rank <= 5 ? "text-emerald-600" : info.rank <= 15 ? "text-slate-900" : "text-slate-500";
        return '<td class="px-2 py-2 text-right align-top"><span class="inline-flex flex-col items-end gap-1">' +
          '<span class="whitespace-nowrap">' +
          '<span class="font-semibold tabular-nums ' + tone + '">' + num(info.value) + "</span>" +
          '<span class="ml-2 text-[11px] text-slate-400">' + info.rank + "/" + info.total + "</span></span>" +
          scoreBar(info.percentile) +
          "</span></td>";
      }).join("");
      var tip = row.code === "E8"
        ? ' title="НПР — научно-педагогические работники; учитываются кандидаты и доктора наук"'
        : "";
      return '<tr class="border-b border-slate-50 text-[13px] transition-colors last:border-0 hover:bg-slate-50/60">' +
        '<td class="min-w-0 py-2 pr-3 align-top leading-snug text-slate-600"' + tip + ">" + esc(row.label.replace(/^E\.\d+\.\s*/, "")) +
        ' <span class="text-[11px] text-slate-400">' + esc(row.unit) + "</span></td>" + cells +
        "</tr>";
    }).join("");

    $("monitoringTable").innerHTML = head + rows + "</tbody></table></div>" +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">' +
      "Источник: monitoring.miccedu.ru. Место — среди вузов Ростовской области, участвовавших в мониторинге. " +
      "Показатель E.3 считается по вузам, у которых он заполнен, поэтому знаменатель меньше." +
      "</p>";
  }

  // ---------- Влияние показателей ленты на место в мониторинге ----------
  function metricById(id) {
    return (state.metrics || []).filter(function (m) { return m.id === id; })[0];
  }

  function percentileTone(value) {
    var v = Number(value) || 0;
    if (v >= 66) return "text-emerald-600";
    if (v >= 33) return "text-amber-600";
    return "text-orange-600";
  }

  function renderMonitoringLinks() {
    var links = (window.MONITORING_LINKS && window.MONITORING_LINKS.links) || [];
    var indicators = {};
    var years = (state.monitoring && state.monitoring.years)
      ? Object.keys(state.monitoring.years).sort() : [];
    if (years.length) {
      indicators = state.monitoring.years[years[years.length - 1]].indicators || {};
    }
    var spoLast = null;
    if (state.spo && state.spo.available) {
      var spoRow = (state.spo.rows || []).filter(function (r) {
        return r.code === "1.1";
      })[0];
      if (spoRow) {
        var spoYears = Object.keys(spoRow.years || {});
        spoLast = spoYears.length ? spoRow.years[spoYears[spoYears.length - 1]] : null;
      }
    }

    var rows = links.map(function (link) {
      var sub = "";
      if (link.eCode === "SPO") {
        if (spoLast) {
          sub = num(spoLast.org) + " чел. · место " + spoLast.rank + " из " + spoLast.total;
        }
      } else if (link.eCode) {
        var ind = indicators[link.eCode];
        if (ind) {
          sub = num(ind.value) + " " + esc(ind.unit) + " · место " + ind.rank + " из " + ind.total +
            ' · перцентиль <span class="font-semibold ' + percentileTone(ind.percentile) + '">' +
            ind.percentile + "</span>";
        }
      } else {
        sub = "в E-место не входит";
      }
      var feed = (link.feed || []).map(function (f) {
        var m = metricById(f.id);
        var value = m ? '<span class="font-semibold tabular-nums">' + num(m.current) + "</span> " + esc(m.unit) : "";
        return '<div class="leading-snug">' + esc(m ? m.title : f.id) +
          (value ? " — " + value : "") +
          '<span class="text-[11px] text-slate-400"> · ' + esc(f.role) + "</span></div>";
      }).join("");
      return '<tr class="align-top">' +
        '<td class="px-5 py-2.5"><p class="font-semibold leading-snug text-slate-800">' + esc(link.section) + "</p>" +
        '<p class="mt-0.5 text-[11.5px] text-slate-500">' + sub + "</p></td>" +
        '<td class="px-4 py-2.5 text-[12.5px] text-slate-600">' + feed + "</td>" +
        '<td class="px-4 py-2.5 text-[12px] leading-snug text-slate-600">' + esc(link.monitors) + "</td>" +
        '<td class="px-5 py-2.5 text-[12px] leading-snug text-slate-500">' + esc(link.missing) + "</td>" +
        "</tr>";
    }).join("");
    $("monitoringLinksTable").innerHTML = rows;

    var weakest = [];
    ["E1", "E2", "E3", "E4", "E8"].forEach(function (code) {
      var ind = indicators[code];
      if (ind) weakest.push({ code: code, percentile: ind.percentile });
    });
    weakest.sort(function (a, b) { return a.percentile - b.percentile; });
    var weakestText = weakest.slice(0, 3).map(function (item) {
      return esc(item.code) + " (" + item.percentile + ")";
    }).join(", ");

    $("monitoringLinksTotal").textContent = links.length + " разделов";
    $("monitoringLinksNote").innerHTML =
      '<p class="font-semibold text-slate-600">Не привязаны ни к одному разделу:</p>' +
      '<p>«Трудоустройство выпускников» и «Выпуск 2025» — входят в дополнительные характеристики мониторинга, а не в разделы III. ' +
      "«Расходы», «Фонд оплаты труда», «Доля ФОТ» и «Доля аренды» — расходы в разделах III не оцениваются (там доходы). " +
      "«Доходы (план 2026)» — план: в мониторинге факт («услуги» и «субсидия»). " +
      "«Контингент обучающихся» (всего) — включает СПО: по разделам ВО берутся бакалавриат и магистратура.</p>" +
      '<p class="mt-1.5 font-semibold text-slate-600">Рекомендация.</p>' +
      "<p>Место тянут вниз " + (weakestText || "—") + ". Чтобы управлять местом, в ленте не хватает трёх метрик: " +
      "средний балл ЕГЭ принятых, НПР с учёными степенями и численность НПР (знаменатель E.2 и E.4).</p>";
  }

  // ---------- Мини-радары по разделам ----------
  var MINI_CHARTS = [];

  function miniBlockItems() {
    var items = [{ id: "all", title: "Все блоки" }];
    var blocks = (state.blocks || []).slice();
    var spo = { id: "spo", title: "СПО" };
    var digital = -1;
    blocks.forEach(function (b, i) { if (b.id === "digital") digital = i; });
    if (digital >= 0) blocks.splice(digital, 0, spo); else blocks.push(spo);
    return items.concat(blocks);
  }

  function eRadarData() {
    var data = state.monitoring;
    if (!data || !data.available || !data.years) return null;
    var years = Object.keys(data.years).sort();
    var inds = (data.years[years[years.length - 1]] || {}).indicators || {};
    var codes = ["E1", "E2", "E3", "E4", "E8"].filter(function (c) { return inds[c]; });
    var labels = [], vuz = [], compare = [], region = [];
    codes.forEach(function (code) {
      var ind = inds[code];
      var med = ind.medians || {};
      var reg = med.region;
      var rf = med.rf;
      var ratio = (reg !== null && reg !== undefined && reg > 0)
        ? Math.min(2, ind.value / reg) : null;
      labels.push(code.replace("E", "E."));
      vuz.push(ratio);
      compare.push((rf !== null && rf !== undefined && reg > 0) ? Math.min(2, rf / reg) : null);
      region.push(ratio !== null ? 1 : null);
    });
    return { labels: labels, vuz: vuz, compare: compare, region: region, compareLabel: "Медиана РФ" };
  }

  function blockRadarData(blockId) {
    var codes = BLOCK_MONITORING_CODES[blockId] || [];
    var map = directionIndex();
    var labels = [], vuz = [], compare = [], region = [];
    codes.forEach(function (code) {
      var entry = map[code];
      if (!entry) return;
      var ind = entry.ind;
      var reg = ind.median_region;
      var ratio = (reg !== null && reg !== undefined && reg > 0 && ind.median_region_ratio > 0)
        ? Math.min(2, ind.median_region_ratio) : null;
      var priv = (ind.median_private !== null && ind.median_private !== undefined &&
                  reg !== null && reg !== undefined && reg > 0)
        ? Math.min(2, ind.median_private / reg) : null;
      labels.push(code);
      vuz.push(ratio);
      compare.push(priv);
      region.push(ratio !== null ? 1 : null);
    });
    return labels.length
      ? { labels: labels, vuz: vuz, compare: compare, region: region, compareLabel: "Медиана частных" }
      : null;
  }

  function spoRadarData() {
    var data = state.spo;
    if (!data || !data.available || !data.rows || !data.rows.length) return null;
    var years = data.years || [];
    var last = years.length ? years[years.length - 1] : null;
    var sections = (data.sections || []).slice();
    if (!sections.length) {
      sections = ["1", "2", "3", "4", "5", "6", "7"].map(function (code) {
        return { code: code };
      });
    }
    var labels = [], vuz = [], compare = [], region = [];
    sections.forEach(function (section) {
      var rows = data.rows.filter(function (r) {
        return String(r.code).split(".")[0] === section.code;
      });
      var vr = [], cr = [];
      rows.forEach(function (row) {
        var b = (row.years && row.years[last]) || {};
        if (b.org !== null && b.org !== undefined && b.region > 0) {
          vr.push(Math.min(2, b.org / b.region));
        }
        if (b.rf !== null && b.rf !== undefined && b.region > 0) {
          cr.push(Math.min(2, b.rf / b.region));
        }
      });
      if (!vr.length) return;
      labels.push(section.code);
      vuz.push(vr.reduce(function (a, b) { return a + b; }, 0) / vr.length);
      compare.push(cr.length ? cr.reduce(function (a, b) { return a + b; }, 0) / cr.length : null);
      region.push(1);
    });
    return labels.length
      ? { labels: labels, vuz: vuz, compare: compare, region: region, compareLabel: "Медиана РФ" }
      : null;
  }

  function miniRadarConfig(data) {
    return {
      type: "radar",
      data: {
        labels: data.labels,
        datasets: [
          {
            label: "ИУБиП",
            data: data.vuz,
            borderColor: "#0f766e",
            backgroundColor: "rgba(15,118,110,.18)",
            borderWidth: 2.2,
            pointRadius: 2.2,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0f766e",
            pointBorderWidth: 1.5,
            fill: true
          },
          {
            label: data.compareLabel || "Медиана сравнения",
            data: data.compare,
            borderColor: "#1e3a8a",
            borderDash: [4, 3],
            borderWidth: 1.1,
            pointRadius: 0,
            fill: false
          },
          {
            label: "Медиана региона",
            data: data.region,
            borderColor: "#92400e",
            borderDash: [2, 2],
            borderWidth: 1,
            pointRadius: 0,
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f172a",
            padding: 7,
            cornerRadius: 7,
            callbacks: {
              label: function (ctx) {
                var v = ctx.parsed.r;
                if (v === null || v === undefined) return ctx.dataset.label + ": нет сравнения";
                return ctx.dataset.label + ": ×" + Number(v).toFixed(2);
              }
            }
          }
        },
        scales: {
          r: {
            min: 0,
            max: 2,
            ticks: { display: false },
            grid: { color: "#e2e8f0" },
            angleLines: { color: "#e2e8f0" },
            pointLabels: {
              display: data.labels.length <= 8,
              color: "#334155",
              font: { size: 7, family: "'Golos Text', Inter, sans-serif" }
            }
          }
        }
      }
    };
  }

  function renderMiniRadars() {
    var box = $("miniRadars");
    if (!box) return;
    function plural(n, one, few, many) {
      var a = Math.abs(n) % 100, b = a % 10;
      if (a > 10 && a < 20) return many;
      if (b > 1 && b < 5) return few;
      if (b === 1) return one;
      return many;
    }
    MINI_CHARTS.forEach(function (chart) { chart.destroy(); });
    MINI_CHARTS = [];
    var items = miniBlockItems();
    box.innerHTML = items.map(function (item) {
      return '<div class="rounded-xl border border-slate-200 bg-slate-50/70 p-2">' +
        '<p class="truncate text-[10.5px] font-semibold text-slate-800" title="' + esc(item.title) + '">' + esc(item.title) + "</p>" +
        '<div style="position:relative;height:132px"><canvas id="miniRadar-' + item.id + '" role="img" aria-label="' + esc(item.title) + '"></canvas></div>' +
        '<p class="mt-1 text-[9.5px] leading-tight text-slate-500" id="miniRadarNote-' + item.id + '">—</p>' +
      "</div>";
    }).join("");
    $("miniRadarsTotal").textContent = items.length + " разделов";
    items.forEach(function (item) {
      var data = item.id === "all" ? eRadarData()
        : item.id === "spo" ? spoRadarData()
        : blockRadarData(item.id);
      var note = $("miniRadarNote-" + item.id);
      if (!data) {
        if (note) note.textContent = "нет данных мониторинга";
        return;
      }
      if (note) {
        note.textContent = item.id === "spo"
          ? data.labels.length + " разделов · среднее к медиане региона"
          : data.labels.length + " " + plural(data.labels.length, "показатель", "показателя", "показателей") +
            " · к медиане региона";
      }
      var canvas = $("miniRadar-" + item.id);
      if (canvas) MINI_CHARTS.push(new Chart(canvas, miniRadarConfig(data)));
    });
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
    var blocksSection = $("blocksSection");
    if (blocksSection) blocksSection.hidden = state.block === "spo";
    if (state.block === "spo") {
      $("blocks").innerHTML = "";
      $("emptyState").hidden = true;
      return;
    }
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
    var chips = [{ id: "all", title: "Все блоки" }];
    var spoAdded = false;
    state.blocks.forEach(function (b) {
      if (!spoAdded && b.id === "digital") {
        chips.push({ id: "spo", title: "СПО" });
        spoAdded = true;
      }
      chips.push(b);
    });
    if (!spoAdded) chips.push({ id: "spo", title: "СПО" });
    $("blockChips").innerHTML = chips.map(function (b) {
      var active = state.block === b.id;
      return '<button data-block="' + b.id + '" class="rounded-lg border px-3 py-1.5 text-[13px] font-medium transition ' +
        (active ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-900") +
        '">' + esc(b.title) + "</button>";
    }).join("");
    var selected = state.block === "all" ? null
      : state.block === "spo" ? { title: "СПО" }
      : state.blocks.filter(function (b) { return b.id === state.block; })[0];
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
      get("/api/monitoring/directions").catch(function () { return null; }),
      get("/api/monitoring/spo").catch(function () { return null; })
    ]).then(function (res) {
      var metrics = res[0] || {};
      state.metrics = metrics.metrics || [];
      // «Воспитательная работа» — пустой блок: в мониторинге для него
      // нет показателей, поэтому в переключателе он не нужен.
      state.blocks = (metrics.blocks || []).filter(function (b) {
        return b.id !== "upr";
      });
      state.periods = metrics.periods || [];
      state.health = res[1];
      state.monitoring = res[2];
      state.directions = res[3];
      state.spo = res[4];
      DIRECTION_INDEX = null;

      renderHeader();
      renderStatus();
      renderChips();
      renderKpi();
      renderTrend();
      renderSources();
      renderMonitoring();
      renderMonitoringLinks();
      renderMiniRadars();
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
