/* Раздел «Мониторинг»: вуз против прошлого года и против области.
 *
 * Два блока страницы — два независимых источника:
 *   /api/monitoring/detail — все показатели карточки вуза за 2024 и 2025;
 *   /api/monitoring        — места среди вузов Ростовской области.
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

  function get(path) {
    return fetch(path, { headers: { Accept: "application/json" } }).then(function (r) {
      if (!r.ok) throw new Error(path + ": HTTP " + r.status);
      return r.json();
    });
  }

  // Сегментная полоса как в референсе: 20 делений, цвет по коридору.
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

  // ---------- Раздел 1: динамика вуза ----------
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
      "Выпуск " + last + " описывает " + first + " год; прочерк — показатель не заполнен в этом выпуске.";
  }

  // ---------- Раздел 2: сравнение с областью ----------
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

  // ---------- Раздел 3: СПО ----------
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
  }

  function boot() {
    wire();
    get("/api/monitoring/detail").then(renderDetail).catch(function (err) {
      $("detailTable").innerHTML = '<tr><td colspan="4" class="px-5 py-6 text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</td></tr>";
    });
    get("/api/monitoring").then(renderRegion).catch(function (err) {
      $("regionTable").innerHTML = '<p class="text-sm text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</p>";
    });
    get("/api/monitoring/spo").then(renderSpo).catch(function (err) {
      $("spoTable").innerHTML = '<tr><td colspan="7" class="px-5 py-6 text-slate-500">Не удалось загрузить: ' + esc(err.message) + "</td></tr>";
    }).finally(function () {
      lucide.createIcons();
      Object.values(Chart.instances).forEach(function (c) { c.resize(); });
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
    lucide.createIcons();
  });
})();
