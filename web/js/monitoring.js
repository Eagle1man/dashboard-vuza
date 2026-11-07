/* Раздел «Мониторинг»: карточка вуза в структуре monitoring.miccedu.ru.
 *
 * Источники (каждый блок — отдельный запрос, отказ одного не ломает
 * остальные):
 *   /api/monitoring            — E-показатели, места среди вузов области;
 *   /api/monitoring/card       — общие сведения, контингент, УГН(С);
 *   /api/monitoring/directions — 57 показателей по направлениям;
 *   /api/monitoring/detail     — дополнительные характеристики (раздел V);
 *   /api/monitoring/spo        — среднее профессиональное образование.
 *
 * Если чего-то нет, блок пишет об этом, а не рисует нули.
 */
(function () {
  "use strict";

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

  function pct(value) {
    if (value === null || value === undefined || !isFinite(Number(value))) return null;
    var n = Number(value);
    return (n > 0 ? "+" : "") + n.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) + "%";
  }

  function get(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    });
  }

  // Сегментная полоса как в референсе: 20 делений, цвет по коридору.
  function scoreBar(p) {
    var total = 20;
    var value = Math.max(0, Math.min(100, Number(p) || 0));
    var filled = Math.round(value / 100 * total);
    var color = value >= 66 ? "bg-emerald-500" : value >= 33 ? "bg-amber-400" : "bg-rose-500";
    var parts = [];
    for (var i = 0; i < total; i++) {
      parts.push('<span class="h-1.5 w-1.5 rounded-[2px] ' + (i < filled ? color : "bg-slate-200") + '"></span>');
    }
    return '<span class="inline-flex items-center gap-[2px]">' + parts.join("") + "</span>";
  }

  // Мини-график пятилетнего ряда: без осей и подписей, только форма.
  function sparkline(values, years, unit) {
    var points = years.map(function (y) { return values[y]; })
      .filter(function (v) { return v !== null && v !== undefined && isFinite(Number(v)); });
    if (points.length < 2) return '<span class="text-slate-300">—</span>';
    var w = 96, h = 26, pad = 2;
    var min = Math.min.apply(null, points);
    var max = Math.max.apply(null, points);
    var span = max - min || 1;
    var step = (w - pad * 2) / (points.length - 1);
    var coords = points.map(function (v, i) {
      var x = pad + i * step;
      var y = h - pad - ((Number(v) - min) / span) * (h - pad * 2);
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    return '<svg class="spark" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" aria-hidden="true">' +
      '<polyline fill="none" stroke="#2563eb" stroke-width="1.8" points="' + coords.join(" ") + '"/>' +
      '<circle cx="' + coords[coords.length - 1].split(",")[0] + '" cy="' +
      coords[coords.length - 1].split(",")[1] + '" r="2.2" fill="#2563eb"/></svg>';
  }

  // ▲ / ▼ относительно медианы. Для E-показателей больше — лучше.
  function medianMark(value, median) {
    if (value === null || value === undefined || median === null || median === undefined) {
      return "";
    }
    var v = Number(value), m = Number(median);
    if (!isFinite(v) || !isFinite(m)) return "";
    if (Math.abs(v - m) < 1e-9) {
      return '<span class="ml-1 text-slate-400" title="ровно медиана">=</span>';
    }
    return v > m
      ? '<span class="ml-1 text-emerald-600" title="выше медианы">▲</span>'
      : '<span class="ml-1 text-rose-600" title="ниже медианы">▼</span>';
  }

  // ---------- I. Общие сведения ----------
  function renderCardInfo(data) {
    if (!data || !data.available) {
      $("cardInfo").innerHTML = '<p class="text-slate-500">Данных карточки нет: файл не собран.</p>';
      return;
    }
    var info = data.info || {};
    var rows = Object.keys(info).map(function (key) {
      return '<div class="flex flex-col gap-0.5 border-b border-slate-50 pb-2">' +
        '<span class="text-[11px] uppercase tracking-[0.06em] text-slate-400">' + esc(key) + "</span>" +
        '<span class="leading-snug text-slate-700">' + esc(info[key]) + "</span></div>";
    });
    $("cardInfo").innerHTML = rows.join("") || '<p class="text-slate-500">Сведения не заполнены.</p>';
  }

  // ---------- II. E-показатели ----------
  function renderETable(data) {
    if (!data || !data.available) {
      $("eTable").innerHTML = '<tr><td colspan="10" class="px-5 py-6 text-slate-500">Данных нет: файл мониторинга не собран.</td></tr>';
      return;
    }
    var releases = Object.keys(data.years || {}).sort();
    var release = releases[releases.length - 1];
    var block = (data.years || {})[release] || {};
    var indicators = block.indicators || {};
    var yearsList = block.values_years || [];
    var axes = data.e_axes || data.axes || [];
    var notes = data.notes || {};

    $("eTotal").textContent = "выпуск " + release + " · организаций " + (block.total || "—");

    var rows = axes.map(function (axis) {
      var ind = indicators[axis.code];
      if (!ind) {
        return '<tr class="bg-slate-50/40">' +
          '<td class="px-5 py-2.5"><p class="leading-snug text-slate-500">' + esc(axis.label) + "</p>" +
          '<p class="text-[11px] text-slate-400">' + esc(axis.unit || "") + "</p></td>" +
          '<td colspan="9" class="px-3 py-2.5 text-[12.5px] text-slate-400">' +
          esc(notes[release] || "в этом выпуске не публикуется") + "</td></tr>";
      }
      var cells = yearsList.map(function (year) {
        var v = (ind.values || {})[year];
        var isLatest = year === String(ind.year);
        return '<td class="px-3 py-2.5 text-right tabular-nums ' +
          (isLatest ? "font-semibold text-slate-900" : "text-slate-500") + '">' + num(v) + "</td>";
      }).join("");

      var delta = ind.delta_pct;
      var deltaHtml = '<span class="block text-[11px] font-semibold ' +
        (delta === null || delta === undefined ? "text-slate-400" : delta > 0 ? "text-emerald-600" : delta < 0 ? "text-rose-600" : "text-slate-500") +
        '">' + (delta === null || delta === undefined ? "—" : pct(delta)) + "</span>";

      var med = ind.medians || {};
      function medCell(value) {
        if (value === null || value === undefined) return '<td class="px-3 py-2.5 text-right text-slate-300">—</td>';
        return '<td class="px-3 py-2.5 text-right tabular-nums text-slate-500">' +
          num(value) + medianMark(ind.value, value) + "</td>";
      }

      return '<tr class="transition-colors hover:bg-slate-50/60">' +
        '<td class="px-5 py-2.5"><p class="leading-snug text-slate-700">' + esc(axis.label) + "</p>" +
        '<p class="text-[11px] text-slate-400">' + esc(axis.unit || "") + "</p></td>" +
        cells +
        '<td class="px-3 py-2.5 text-right"><span class="inline-flex flex-col items-end">' +
        deltaHtml + sparkline(ind.values || {}, yearsList, axis.unit) + "</span></td>" +
        medCell(med.rf) + medCell(med.region) + medCell(med.department) +
        "</tr>";
    }).join("");

    $("eTable").innerHTML = rows;

    var latest = indicators[axes.length ? axes[0].code : ""];
    var parts = ["Источник: monitoring.miccedu.ru, карточка вуза id=1971."];
    if (latest && latest.best) {
      parts.push("Лучший результат по E.1 в области: " + num(latest.best.value) +
        " — " + latest.best.name + ".");
    }
    parts.push("▲ — значение вуза выше медианы, ▼ — ниже, = — ровно медиана.");
    if (notes[release]) parts.push(notes[release]);
    $("eNote").textContent = parts.join(" ");
  }

  // ---------- III. Направления деятельности ----------
  function ratioText(ratio, median, capped, unit) {
    if (capped) {
      return '<span class="inline-flex items-center rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700" ' +
        'title="На сайте шкала сравнения обрезана: значение не менее чем в 2 раза выше медианы">≥ 2×</span>';
    }
    if (median === null || median === undefined || ratio === null || ratio === undefined) {
      return '<span class="text-slate-300">—</span>';
    }
    return '<span class="inline-flex flex-col items-end gap-0.5">' +
      '<span class="font-semibold tabular-nums text-slate-700">×' + Number(ratio).toFixed(2) + "</span>" +
      '<span class="text-[10.5px] text-slate-400">медиана ' + num(median) + (unit ? " " + esc(unit) : "") + "</span></span>";
  }

  var DIR_CHARTS = {};

  function dirChartConfig(section, kind) {
    var items = section.indicators || [];
    var labels = items.map(function (ind) { return ind.code; });
    var ratios = items.map(function (ind) {
      var r = kind === "private" ? ind.median_private_ratio : ind.median_region_ratio;
      if (r === null || r === undefined) return 0;
      return Math.min(2, Number(r) || 0);
    });
    var baseline = items.map(function () { return 1; });
    var medianColor = kind === "private" ? "#38bdf8" : "#b45309";
    var medianFill = kind === "private" ? "rgba(56,189,248,.08)" : "rgba(180,83,9,.08)";
    return {
      type: "radar",
      data: {
        labels: labels,
        datasets: [
          {
            label: kind === "private" ? "Медиана частных вузов" : "Медиана региона",
            data: baseline,
            borderColor: medianColor,
            backgroundColor: medianFill,
            borderWidth: 1.5,
            pointRadius: 2.5,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: medianColor,
            pointBorderWidth: 1.5,
            fill: true
          },
          {
            label: "Показатели вуза",
            data: ratios,
            borderColor: "#0f766e",
            backgroundColor: "rgba(15,118,110,.10)",
            borderWidth: 2,
            pointRadius: 3,
            pointBackgroundColor: "#ffffff",
            pointBorderColor: "#0f766e",
            pointBorderWidth: 2,
            fill: true
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        layout: { padding: 4 },
        plugins: {
          legend: {
            display: true,
            position: "bottom",
            labels: { boxWidth: 8, boxHeight: 8, font: { size: 9 }, padding: 8 }
          },
          tooltip: {
            backgroundColor: "#0f1115", padding: 8, cornerRadius: 8,
            callbacks: {
              label: function (ctx) {
                var ind = items[ctx.dataIndex] || {};
                var value = Number(ctx.parsed.r);
                if (ctx.dataset.label.indexOf("Медиана") === 0) {
                  return "медиана: ×1.00";
                }
                var text = "вуз: ×" + value.toFixed(2);
                var median = kind === "private" ? ind.median_private : ind.median_region;
                var capped = kind === "private" ? ind.median_private_capped : ind.median_region_capped;
                if (capped) text += " (≥ 2× медианы)";
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
            ticks: { stepSize: 0.5, showLabelBackdrop: false, color: "#94a3b8", font: { size: 8 } },
            grid: { color: "#e8edf3" },
            angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 8.5 } }
          }
        }
      }
    };
  }

  function resizeDirectionCharts() {
    Object.keys(DIR_CHARTS).forEach(function (key) {
      var chart = DIR_CHARTS[key];
      if (chart) chart.resize();
    });
  }

  function renderDirections(data) {
    if (!data || !data.available) {
      $("directions").innerHTML = '<p class="px-5 py-6 text-sm text-slate-500">Данных раздела III нет: файл не собран.</p>';
      return;
    }
    var sections = data.sections || [];
    var total = 0;
    sections.forEach(function (s) { total += (s.indicators || []).length; });
    $("dirTotal").textContent = sections.length + " разделов · " + total + " показателей";

    $("directions").innerHTML = sections.map(function (section) {
      var rows = (section.indicators || []).map(function (ind) {
        return '<tr class="transition-colors hover:bg-slate-50/60">' +
          '<td class="whitespace-nowrap px-3 py-2 text-right font-mono text-[11.5px] text-slate-400">' + esc(ind.code) + "</td>" +
          '<td class="px-3 py-2"><p class="leading-snug text-slate-700">' + esc(ind.title) + "</p></td>" +
          '<td class="whitespace-nowrap px-3 py-2 text-[12px] text-slate-400">' + esc(ind.unit || "") + "</td>" +
          '<td class="px-3 py-2 text-right font-semibold tabular-nums text-slate-900">' + num(ind.value) + "</td>" +
          '<td class="px-3 py-2 text-right">' + ratioText(ind.median_private_ratio, ind.median_private, ind.median_private_capped, ind.unit) + "</td>" +
          '<td class="px-3 py-2 text-right">' + ratioText(ind.median_region_ratio, ind.median_region, ind.median_region_capped, ind.unit) + "</td>" +
          "</tr>";
      }).join("");
      var chartId = "dir-" + section.code;
      var charts =
        '<div class="grid grid-cols-1 gap-4 border-t border-slate-50 p-4 lg:grid-cols-2">' +
          '<div class="rounded-xl border border-slate-100 p-3">' +
            '<p class="mb-2 text-[11.5px] font-semibold text-slate-600">К медиане частных вузов</p>' +
            '<div class="relative h-[300px]"><canvas id="' + chartId + '-private" aria-label="Показатели вуза к медиане частных вузов" role="img"></canvas></div>' +
          '</div>' +
          '<div class="rounded-xl border border-slate-100 p-3">' +
            '<p class="mb-2 text-[11.5px] font-semibold text-slate-600">К медиане вузов региона</p>' +
            '<div class="relative h-[300px]"><canvas id="' + chartId + '-region" aria-label="Показатели вуза к медиане вузов региона" role="img"></canvas></div>' +
          '</div>' +
        '</div>';
      return '<details class="group" open>' +
        '<summary class="flex cursor-pointer items-center justify-between gap-3 px-5 py-3 hover:bg-slate-50/60">' +
        '<span class="text-[13.5px] font-semibold text-slate-800">' + esc(section.code) + ". " + esc(section.title) + "</span>" +
        '<span class="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-semibold text-slate-500">' +
        (section.indicators || []).length + " показателей</span></summary>" +
        charts +
        '<div class="overflow-x-auto border-t border-slate-50">' +
        '<table class="w-full min-w-[820px] text-left text-sm">' +
        '<thead><tr class="text-[10.5px] uppercase tracking-[0.07em] text-slate-400">' +
        '<th class="px-3 py-2 text-right font-semibold">Код</th>' +
        '<th class="px-3 py-2 font-semibold">Показатель</th>' +
        '<th class="px-3 py-2 font-semibold">Ед.</th>' +
        '<th class="px-3 py-2 text-right font-semibold">Значение</th>' +
        '<th class="px-3 py-2 text-right font-semibold">К медиане частных вузов</th>' +
        '<th class="px-3 py-2 text-right font-semibold">К медиане региона</th>' +
        "</tr></thead><tbody class=\"divide-y divide-slate-50\">" + rows + "</tbody></table></div></details>";
    }).join("");

    Object.keys(DIR_CHARTS).forEach(function (key) {
      if (DIR_CHARTS[key]) DIR_CHARTS[key].destroy();
    });
    DIR_CHARTS = {};
    sections.forEach(function (section) {
      ["private", "region"].forEach(function (kind) {
        var canvas = document.getElementById("dir-" + section.code + "-" + kind);
        if (!canvas) return;
        DIR_CHARTS[section.code + "-" + kind] =
          new Chart(canvas, dirChartConfig(section, kind));
      });
    });
    $("directions").querySelectorAll("details").forEach(function (details) {
      details.addEventListener("toggle", function () {
        if (details.open) resizeDirectionCharts();
      });
    });
  }

  // ---------- IV. Контингент и УГН ----------
  function renderCardRole(data) {
    if (!data || !data.available) {
      $("cardContingent").innerHTML = '<p class="text-slate-500">Данных нет.</p>';
      $("cardBranches").innerHTML = "";
      $("cardUgn").innerHTML = '<tr><td colspan="6" class="px-3 py-4 text-slate-500">Данных нет.</td></tr>';
      return;
    }
    var cont = data.contingent || {};
    var region = data.region || {};
    function kpi(label, value, unit) {
      return '<div class="flex items-baseline justify-between gap-3 border-b border-slate-50 py-1.5">' +
        '<span class="text-[12.5px] text-slate-500">' + esc(label) + "</span>" +
        '<span class="font-semibold tabular-nums text-slate-900">' + esc(value) +
        (unit ? ' <span class="text-[11px] font-normal text-slate-400">' + esc(unit) + "</span>" : "") +
        "</span></div>";
    }
    $("cardContingent").innerHTML =
      kpi("Обучается студентов", num(cont.total), "чел.") +
      kpi("В том числе очной формы", num(cont.full_time), "чел.") +
      kpi("Доля от контингента субъекта РФ", num(cont.subject_share), "%") +
      kpi("За счёт бюджетных ассигнований", num(cont.budget_share), "%") +
      kpi("По договорам о платных услугах", num(cont.paid_share), "%") +
      kpi("Вузов в регионе", num(region.organizations), "") +
      kpi("Студентов в регионе", num(region.students), "чел.");

    $("cardBranches").innerHTML = '<h3 class="mb-2 text-[13px] font-semibold text-slate-700">Распределение контингента по отраслям наук</h3>' +
      (data.branches || []).map(function (b) {
        var share = Math.max(0, Math.min(100, Number(b.subject_share) || 0));
        return '<div class="mb-2">' +
          '<div class="flex items-baseline justify-between gap-3 text-[12.5px]">' +
          '<span class="text-slate-600">' + esc(b.name) + "</span>" +
          '<span class="whitespace-nowrap font-semibold tabular-nums text-slate-900">' + num(b.contingent) +
          ' <span class="text-[11px] font-normal text-slate-400">чел. · ' + num(b.subject_share) + '%</span></span>' +
          "</div>" +
          '<div class="mt-1 h-1.5 w-full rounded-full bg-slate-100"><div class="h-1.5 rounded-full bg-blue-500" style="width:' + share + '%"></div></div>' +
          "</div>";
      }).join("");

    $("cardUgn").innerHTML = (data.ugns || []).map(function (u) {
      return '<tr class="transition-colors hover:bg-slate-50/60">' +
        '<td class="px-3 py-2"><p class="font-medium leading-snug text-slate-700">' + esc(u.code) + " — " + esc(u.name) + "</p></td>" +
        '<td class="px-3 py-2 text-right font-semibold tabular-nums">' + num(u.contingent) + "</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-600">' + num(u.share_org) + "%</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-600">' + num(u.share_region) + "%</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-600">' + num(u.vuz_count) + "</td>" +
        '<td class="px-3 py-2 text-right tabular-nums text-slate-600">' + num(u.branch_count) + "</td>" +
        "</tr>";
    }).join("") || '<tr><td colspan="6" class="px-3 py-4 text-slate-500">УГН(С) не заполнены.</td></tr>';
  }

  // ---------- V. Дополнительные характеристики ----------
  function renderDetail(data) {
    if (!data || !data.available) {
      $("detailTable").innerHTML = '<tr><td colspan="4" class="px-5 py-6 text-slate-500">Данных нет: файл мониторинга не собран.</td></tr>';
      return;
    }
    var years = data.years || [];
    var first = years[0], last = years[years.length - 1];
    var totals = data.totals || {};

    $("detailStats").innerHTML = [
      { text: "всего " + totals.all, cls: "bg-slate-100 text-slate-600" },
      { text: "выросло " + totals.grew, cls: "bg-emerald-50 text-emerald-700" },
      { text: "снизилось " + totals.fell, cls: "bg-rose-50 text-rose-700" },
      { text: "без изменений " + totals.flat, cls: "bg-slate-100 text-slate-500" }
    ].map(function (chip) {
      return '<span class="rounded-lg px-2 py-1 text-xs font-semibold ' + chip.cls + '">' + chip.text + "</span>";
    }).join("");

    $("detailTable").innerHTML = (data.rows || []).map(function (row) {
      var oldValue = row[first];
      var newValue = row[last];
      var delta = row.delta;
      var deltaHtml = '<span class="text-slate-400">—</span>';
      if (delta !== null && delta !== undefined) {
        var up = delta > 0;
        var tone = delta === 0 ? "text-slate-500" : up ? "text-emerald-600" : "text-rose-600";
        var icon = delta === 0 ? "minus" : up ? "arrow-up-right" : "arrow-down-right";
        deltaHtml = '<span class="inline-flex items-center gap-1 whitespace-nowrap text-[13px] font-semibold ' + tone + '">' +
          '<i data-lucide="' + icon + '" class="h-3.5 w-3.5"></i>' + num(Math.abs(delta)) + "%</span>";
      }
      return '<tr class="transition-colors hover:bg-slate-50/60">' +
        '<td class="px-5 py-2.5"><p class="leading-snug text-slate-700">' + esc(row.title) + "</p>" +
          '<p class="text-[11px] text-slate-400">' + esc(row.unit || "") + "</p></td>" +
        '<td class="px-5 py-2.5 text-right tabular-nums text-slate-500">' + num(oldValue) + "</td>" +
        '<td class="px-5 py-2.5 text-right font-semibold tabular-nums">' + num(newValue) + "</td>" +
        '<td class="px-5 py-2.5 text-right">' + deltaHtml + "</td>" +
      "</tr>";
    }).join("");

    $("detailNote").textContent = "Источник: monitoring.miccedu.ru, карточка вуза id=1971. " +
      "Выпуск " + last + " против выпуска " + first +
      "; прочерк — показатель не заполнен в одном из выпусков.";
    $("detailSubtitle").textContent = (data.rows || []).length +
      " показателей карточки вуза, выпуск " + last + " против выпуска " + first;
  }

  // ---------- Сравнение с областью ----------
  var MON_SHORT = {
    E1: "Образование", E2: "Наука", E3: "Международная",
    E4: "Финансы", E8: "Доп. показатель"
  };

  function renderRegion(data) {
    if (!data || !data.available) {
      $("regionTable").innerHTML = '<p class="text-sm text-slate-500">Данных сравнения нет.</p>';
      return;
    }
    var years = Object.keys(data.years || {}).sort();
    var axes = data.axes || [];
    var first = years[0], last = years[years.length - 1];
    var colors = ["#94a3b8", "#2563eb"];

    $("regionAvg").textContent = "средний процентиль " +
      data.years[first].average_percentile + " → " + data.years[last].average_percentile;

    if (window.__radar) window.__radar.destroy();
    window.__radar = new Chart($("radarChart"), {
      type: "radar",
      data: {
        labels: axes.map(function (a) { return MON_SHORT[a.code] || a.label; }),
        datasets: years.map(function (year, i) {
          return {
            label: year,
            data: data.series[year],
            borderColor: colors[i] || colors[colors.length - 1],
            backgroundColor: (colors[i] || colors[colors.length - 1]) + "1f",
            pointBackgroundColor: "#ffffff",
            pointBorderColor: colors[i] || colors[colors.length - 1],
            pointBorderWidth: 2,
            pointRadius: 4,
            borderWidth: 2,
            fill: true
          };
        })
      },
      options: {
        responsive: true, maintainAspectRatio: false, layout: { padding: 6 },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0f1115", padding: 10, cornerRadius: 10,
            callbacks: {
              label: function (ctx) {
                var row = (data.table || [])[ctx.dataIndex];
                var info = row && row.years[ctx.dataset.label];
                return ctx.dataset.label + ": " + ctx.parsed.r + " процентиль" +
                  (info ? " · место " + info.rank + " из " + info.total : "");
              }
            }
          }
        },
        scales: {
          r: {
            min: 0, max: 100,
            ticks: { stepSize: 25, showLabelBackdrop: false, color: "#94a3b8", font: { size: 10 } },
            grid: { color: "#e8edf3" }, angleLines: { color: "#e8edf3" },
            pointLabels: { color: "#475569", font: { size: 11 } }
          }
        }
      }
    });

    $("radarLegend").innerHTML = years.map(function (year, i) {
      return '<span class="inline-flex items-center gap-2">' +
        '<span class="h-2.5 w-2.5 rounded-full" style="background:' + (colors[i] || "#2563eb") + '"></span>' +
        year + "</span>";
    }).join("");

    var head = '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 border-b border-slate-100 pb-2 text-[11px] uppercase tracking-[0.06em] text-slate-400">' +
      "<span>Показатель</span>" +
      years.map(function (y) { return '<span class="text-right">' + y + "</span>"; }).join("") +
      "</div>";

    var rows = (data.table || []).map(function (row) {
      var cells = years.map(function (year) {
        var info = row.years[year];
        if (!info) return '<span class="text-right text-slate-300">—</span>';
        var tone = info.rank <= 5 ? "text-emerald-600" : info.rank <= 15 ? "text-slate-900" : "text-slate-500";
        return '<span class="flex flex-col items-end gap-1">' +
          '<span class="whitespace-nowrap">' +
          '<span class="font-semibold tabular-nums ' + tone + '">' + num(info.value) + "</span>" +
          '<span class="ml-2 text-[11px] text-slate-400">' + info.rank + "/" + info.total + "</span></span>" +
          scoreBar(info.percentile) + "</span>";
      }).join("");
      return '<div class="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-4 border-b border-slate-50 py-2 text-[13px] last:border-0">' +
        '<span class="min-w-0 pr-2 leading-snug text-slate-600">' + esc(row.label.replace(/^E\.\d+\.\s*/, "")) +
        ' <span class="text-[11px] text-slate-400">' + esc(row.unit) + "</span></span>" + cells + "</div>";
    }).join("");

    $("regionTable").innerHTML = head + rows +
      '<p class="mt-3 text-[11px] leading-snug text-slate-400">' +
      "Место — среди вузов Ростовской области, участвовавших в мониторинге. " +
      "E.3 считается по вузам, у которых показатель заполнен, поэтому знаменатель меньше. " +
      "Полное сравнение по РФ — отдельная выгрузка: на сайте нет одной страницы со всеми вузами страны.</p>";
  }

  // ---------- СПО ----------
  function renderSpo(data) {
    if (!data || !data.available) {
      $("spoTable").innerHTML = '<tr><td colspan="7" class="px-5 py-6 text-slate-500">Данных СПО нет: файл не собран.</td></tr>';
      return;
    }
    var years = data.years || [];
    var first = years[0], last = years[years.length - 1];
    $("spoTotal").textContent = "организаций СПО в области: " + (data.total || "—");

    $("spoTable").innerHTML = (data.rows || []).map(function (row) {
      var a = row.years[first] || {};
      var b = row.years[last] || {};
      var delta = b.delta;
      var deltaHtml = '<span class="text-slate-400">—</span>';
      if (delta !== null && delta !== undefined) {
        var up = delta > 0;
        var tone = delta === 0 ? "text-slate-500" : up ? "text-emerald-600" : "text-rose-600";
        var icon = delta === 0 ? "minus" : up ? "arrow-up-right" : "arrow-down-right";
        deltaHtml = '<span class="inline-flex items-center gap-1 whitespace-nowrap font-semibold ' + tone + '">' +
          '<i data-lucide="' + icon + '" class="h-3.5 w-3.5"></i>' + num(Math.abs(delta)) + "%</span>";
      }
      var place = (b.rank && b.total)
        ? '<span class="flex flex-col items-end gap-1"><span class="whitespace-nowrap">' +
          '<span class="font-semibold tabular-nums">' + b.rank + "</span>" +
          '<span class="text-[11px] text-slate-400">/ ' + b.total + "</span></span>" +
          scoreBar(b.percentile) + "</span>"
        : '<span class="text-slate-300">—</span>';

      return '<tr class="transition-colors hover:bg-slate-50/60">' +
        '<td class="px-5 py-3"><p class="leading-snug text-slate-700">' + esc(row.title) + "</p>" +
          '<p class="text-[11px] text-slate-400">' + esc(row.unit || "") + "</p></td>" +
        '<td class="px-5 py-3 text-right tabular-nums text-slate-500">' + num(a.org) + "</td>" +
        '<td class="px-5 py-3 text-right font-semibold tabular-nums">' + num(b.org) + "</td>" +
        '<td class="px-5 py-3 text-right">' + deltaHtml + "</td>" +
        '<td class="px-5 py-3 text-right">' + place + "</td>" +
        '<td class="px-5 py-3 text-right tabular-nums text-slate-600">' + num(b.region) + "</td>" +
        '<td class="px-5 py-3 text-right tabular-nums text-slate-500">' + num(b.rf) + "</td>" +
      "</tr>";
    }).join("");

    $("spoNote").textContent = "Источник: monitoring.miccedu.ru, СПО, карточка организации id=1971. " +
      "Место — среди " + (data.total || "—") + " организаций СПО Ростовской области; " +
      "по численности студентов место отражает размер, а не качество.";
  }

  // ---------- Общее ----------
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

    // Печать: закрытые <details> не печатаются, поэтому раскрываем все
    // разделы на время печати и возвращаем как было. События beforeprint/
    // afterprint ловят и кнопку, и Ctrl+P, и печать в PDF из браузера.
    var printState = null;
    window.addEventListener("beforeprint", function () {
      printState = [];
      document.querySelectorAll("details").forEach(function (d) {
        printState.push([d, d.open]);
        d.open = true;
      });
      resizeDirectionCharts();
    });
    window.addEventListener("afterprint", function () {
      if (!printState) return;
      printState.forEach(function (pair) { pair[0].open = pair[1]; });
      printState = null;
    });
    $("btnPrint").addEventListener("click", function () { window.print(); });
  }

  function boot() {
    wire();
    get("/api/monitoring").then(function (data) {
      renderETable(data);
      renderRegion(data);
    }).catch(function (err) {
      $("eTable").innerHTML = '<tr><td colspan="10" class="px-5 py-6 text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</td></tr>";
      $("regionTable").innerHTML = '<p class="text-sm text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</p>";
    });
    get("/api/monitoring/card").then(function (data) {
      renderCardInfo(data);
      renderCardRole(data);
    }).catch(function (err) {
      $("cardInfo").innerHTML = '<p class="text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</p>";
      renderCardRole(null);
    });
    get("/api/monitoring/directions").then(renderDirections).catch(function (err) {
      $("directions").innerHTML = '<p class="px-5 py-6 text-sm text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</p>";
    });
    get("/api/monitoring/detail").then(renderDetail).catch(function (err) {
      $("detailTable").innerHTML = '<tr><td colspan="4" class="px-5 py-6 text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</td></tr>";
    });
    get("/api/monitoring/spo").then(renderSpo).catch(function (err) {
      $("spoTable").innerHTML = '<tr><td colspan="7" class="px-5 py-6 text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</td></tr>";
    }).finally(function () {
      lucide.createIcons();
      Object.values(Chart.instances).forEach(function (c) { c.resize(); });
      resizeDirectionCharts();
    });
    get("/api/health").then(function (h) {
      $("railStatus").className = "h-2 w-2 rounded-full " + (h && h.totals ? "bg-emerald-500" : "bg-rose-500");
    }).catch(function () {
      $("railStatus").className = "h-2 w-2 rounded-full bg-rose-500";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();

  window.addEventListener("load", function () {
    Object.values(Chart.instances).forEach(function (c) { c.resize(); });
    resizeDirectionCharts();
    lucide.createIcons();
  });
})();
