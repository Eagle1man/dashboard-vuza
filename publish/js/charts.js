/* ============================================================
   charts.js — самописные SVG-графики (без внешних библиотек)
   lineChart, barChart, donutChart, sparkline.
   ============================================================ */

(function (global) {
  "use strict";

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Спарклайн (мини-тренд в виджете)
  function sparkline(series, color) {
    const W = 120, H = 40, PAD = 2;
    // Ряда может не быть совсем (пустой массив) или он может состоять
    // из одной точки — у официального показателя истории нет. При длине 1
    // деление на (length-1) давало NaN, и SVG ломался: координаты
    // «NaN» браузер не рисует, а в консоли сыпались ошибки.
    if (!series || series.length === 0) {
      return '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-hidden="true"></svg>';
    }
    const min = Math.min.apply(null, series);
    const max = Math.max.apply(null, series);
    const range = (max - min) || 1;
    const pts = series.map((v, i) => {
      const x = series.length === 1
        ? (PAD + (W - PAD * 2) / 2)
        : PAD + (i / (series.length - 1)) * (W - PAD * 2);
      const y = H - PAD - ((v - min) / range) * (H - PAD * 2);
      return [x, y];
    });
    const d = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    return '<svg class="spark" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + d + '" fill="none" stroke="' + (color || "#097aae") + '" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }

  // Линейный график с несколькими сериями
  function lineChart(labels, series, opts) {
    opts = opts || {};
    const W = 720, H = 260, PL = 54, PR = 16, PT = 20, PB = 40;
    const iw = W - PL - PR, ih = H - PT - PB;
    let all = [];
    series.forEach((s) => { all = all.concat(s.data); });
    let min = Math.min.apply(null, all), max = Math.max.apply(null, all);
    if (min === max) { min -= 1; max += 1; }
    const padAmt = (max - min) * 0.1;
    min -= padAmt; max += padAmt;
    const n = labels.length;
    const x = (i) => PL + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    const y = (v) => PT + ih - ((v - min) / (max - min)) * ih;

    // Сетка и подписи оси Y
    let g = '<g>';
    const gridN = 4;
    for (let gi = 0; gi <= gridN; gi++) {
      const yy = PT + (ih / gridN) * gi;
      const val = max - ((max - min) / gridN) * gi;
      g += '<line x1="' + PL + '" y1="' + yy + '" x2="' + (W - PR) + '" y2="' + yy + '" stroke="var(--border)" stroke-width="1"/>';
      g += '<text x="' + (PL - 8) + '" y="' + (yy + 4) + '" text-anchor="end" font-size="11" fill="var(--muted)">' + fmt(val) + "</text>";
    }
    const step = labels.length > 8 ? 2 : 1;
    for (let i = 0; i < n; i += step) {
      g += '<text x="' + x(i).toFixed(1) + '" y="' + (H - 12) + '" text-anchor="middle" font-size="11" fill="var(--muted)">' + esc(labels[i]) + "</text>";
    }
    g += "</g>";

    series.forEach((s) => {
      const d = s.data.map((v, i) => (i === 0 ? "M" : "L") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
      g += '<path d="' + d + '" fill="none" stroke="' + (s.color || "#097aae") + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>';
      s.data.forEach((v, i) => {
        g += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="3" fill="' + (s.color || "#097aae") + '" data-val="' + esc(v) + '" data-label="' + esc(labels[i]) + '" class="pt"/>';
      });
    });

    let leg = '<g>';
    let lx = PL;
    series.slice(0, 6).forEach((s) => {
      leg += '<rect x="' + lx + '" y="' + (PT - 14) + '" width="10" height="10" rx="2" fill="' + (s.color || "#097aae") + '"/>';
      leg += '<text x="' + (lx + 14) + '" y="' + (PT - 5) + '" font-size="11" fill="var(--muted)">' + esc(s.name) + "</text>";
      lx += 14 + s.name.length * 7.2 + 20;
    });
    leg += "</g>";

    return '<svg class="chart-svg" viewBox="0 0 ' + W + " " + H + '" role="img">' + leg + g + "</svg>";
  }

  // Форматирование чисел
  function fmt(v) {
    if (v >= 1000000) return (v / 1000000).toFixed(1) + "М";
    if (v >= 10000) return (v / 1000).toFixed(1) + "К";
    if (Number.isInteger(v)) return String(v);
    return String(parseFloat(v.toFixed(1)));
  }

  // Столбиковая диаграмма
  function barChart(labels, values, opts) {
    opts = opts || {};
    const W = 720, H = 250, PL = 46, PR = 14, PT = 16, PB = 36;
    const iw = W - PL - PR, ih = H - PT - PB;
    let max = Math.max.apply(null, values) || 1;
    max = max * 1.1;
    const n = labels.length;
    const slot = iw / n;
    const bw = Math.min(46, slot * 0.62);
    let g = "";
    const gridN = 4;
    for (let gi = 0; gi <= gridN; gi++) {
      const yy = PT + (ih / gridN) * gi;
      const val = max - (max / gridN) * gi;
      g += '<line x1="' + PL + '" y1="' + yy + '" x2="' + (W - PR) + '" y2="' + yy + '" stroke="var(--border)" stroke-width="1"/>';
      g += '<text x="' + (PL - 8) + '" y="' + (yy + 4) + '" text-anchor="end" font-size="11" fill="var(--muted)">' + fmt(val) + "</text>";
    }
    values.forEach((v, i) => {
      const bx = PL + slot * i + (slot - bw) / 2;
      const bh = (v / max) * ih;
      const by = PT + ih - bh;
      const color = (opts.colors && opts.colors[i]) || "#097aae";
      g += '<rect data-val="' + esc(v) + '" data-label="' + esc(labels[i]) + '" data-idx="' + i + '" class="bar datab" x="' + bx.toFixed(1) + '" y="' + by.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + bh.toFixed(1) + '" rx="3" fill="' + color + '"/>';
      if (i % 2 === 0) {
        g += '<text x="' + (bx + bw / 2).toFixed(1) + '" y="' + (H - 10) + '" text-anchor="middle" font-size="10" fill="var(--muted)">' + esc(labels[i]) + "</text>";
      }
    });
    return '<svg class="chart-svg" viewBox="0 0 ' + W + " " + H + '" role="img">' + g + "</svg>";
  }

  // Круговая (донут) диаграмма
  function donutChart(labels, values, opts) {
    opts = opts || {};
    const total = values.reduce((a, b) => a + b, 0) || 1;
    const C = 150, R = 90, r = 60;
    const cx = C, cy = C;
    const palette = ["#097aae", "#0c5f97", "#e5b02a", "#0f7a38", "#c8102e", "#4b688b", "#0891b2", "#a16207", "#7b2ff7"];
    let acc = 0;
    let g = "";
    values.forEach((v, i) => {
      const frac = v / total;
      const a0 = acc * Math.PI * 2 - Math.PI / 2;
      const a1 = (acc + frac) * Math.PI * 2 - Math.PI / 2;
      const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0);
      const x1 = cx + R * Math.cos(a1), y1 = cy + R * Math.sin(a1);
      const xi0 = cx + r * Math.cos(a1), yi0 = cy + r * Math.sin(a1);
      const xi1 = cx + r * Math.cos(a0), yi1 = cy + r * Math.sin(a0);
      const large = frac > 0.5 ? 1 : 0;
      const color = (opts.colors && opts.colors[i]) || palette[i % palette.length];
      g += '<path data-val="' + esc(v) + '" data-label="' + esc(labels[i]) + '" d="M ' + x0.toFixed(1) + " " + y0.toFixed(1) +
        " A " + R + " " + R + " 0 " + large + " 1 " + x1.toFixed(1) + " " + y1.toFixed(1) +
        " L " + xi0.toFixed(1) + " " + yi0.toFixed(1) +
        " A " + r + " " + r + " 0 " + large + " 0 " + xi1.toFixed(1) + " " + yi1.toFixed(1) + " Z" +
        '" fill="' + color + '" class="donut"/>';
      acc += frac;
    });
    let leg = "";
    const lx = cx + R + 16;
    labels.forEach((lb, i) => {
      const c = (opts.colors && opts.colors[i]) || palette[i % palette.length];
      const ly = (C - labels.length * 6) + i * 14;
      leg += '<rect x="' + lx + '" y="' + ly + '" width="10" height="10" rx="2" fill="' + c + '"/>';
      leg += '<text x="' + (lx + 14) + '" y="' + (ly + 9) + '" font-size="11" fill="var(--muted)">' + esc(lb) + "</text>";
    });
    return '<svg class="chart-svg" style="max-width:' + (C + R + 200) + 'px;margin:0 auto" viewBox="0 0 ' + (C + R + 200) + " " + C + '" role="img">' + g + leg + "</svg>";
  }

  global.Charts = { sparkline, lineChart, barChart, donutChart, fmt };
})(window);