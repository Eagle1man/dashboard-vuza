// Выгружает из web/js/data.js те же значения, которые строит Python,
// чтобы их можно было сверить. Запуск: node backend/tests/dump_js_values.js
//
// Сверяется только слой ДЕМО-метрик. Официальные числа живут в базе:
// data.js их не знает (и не должен — иначе документы и витрина
// разойдутся молча), поэтому сравнивать тут нечего.
const fs = require('fs');
const path = require('path');

const dataJs = path.join(__dirname, '..', '..', 'web', 'js', 'data.js');

// data.js — это IIFE, который вешает DashboardData на window.
// В node окна нет, поэтому подставляем глобальный объект.
global.window = global;
// eslint-disable-next-line no-eval
eval(fs.readFileSync(dataJs, 'utf8'));

const data = global.DashboardData.initialData;
const out = data.metrics
  .filter((m) => m.source === 'demo')
  .map((m) => ({
    id: m.id,
    series: m.series,
    current: m.current,
    prev: m.prev,
    delta: m.delta,
    status: m.status,
    dept: m.dept.map((d) => ({ deptId: d.deptId, value: d.value, share: d.share })),
    drill: Object.keys(m.drill).sort().map((k) =>
      m.drill[k].map((r) => ({ fio: r.fio, value: r.value, reason: r.reason }))),
  }));
process.stdout.write(JSON.stringify(out, null, 1));
