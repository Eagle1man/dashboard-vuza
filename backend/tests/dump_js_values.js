// Выгружает из web/js/data.js те же значения, которые строит Python,
// чтобы их можно было сверить. Запуск: node backend/tests/dump_js_values.js
//
// Демонстрационных метрик больше нет: data.js содержит только
// официальные значения — резерв для режима без сервера. Они обязаны
// совпадать с тем, что лежит в базе, иначе в офлайне ректор увидит одно
// число, а с сервером — другое.
const fs = require('fs');
const path = require('path');

const dataJs = path.join(__dirname, '..', '..', 'web', 'js', 'data.js');

// data.js — это IIFE, который вешает DashboardData на window.
// В node окна нет, поэтому подставляем глобальный объект.
global.window = global;
// eslint-disable-next-line no-eval
eval(fs.readFileSync(dataJs, 'utf8'));

const data = global.DashboardData.initialData;
const official = data.metrics.map((m) => ({
  id: m.id,
  current: m.current,
  hasHistory: m.hasHistory,
  source: m.source,
}));

process.stdout.write(JSON.stringify({ official: official }, null, 1));
