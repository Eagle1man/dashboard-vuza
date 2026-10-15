/* ============================================================
   data.js — демо-данные дашборда ректора
   7 стратегических блоков, 32 метрики, drill-down записи.
   Детерминированная генерация на seeded RNG (стабильные цифры).
   ============================================================ */

(function (global) {
  "use strict";

  // ---- Seeded RNG (mulberry32) ----
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
  const QUARTERS = ["Q1", "Q2", "Q3", "Q4"];
  const YEARS = ["2022", "2023", "2024", "2025", "2026"];

  const DEPARTMENTS = [
    { id: "ff", name: "Факультет фундаментальных наук" },
    { id: "it", name: "Институт информационных технологий" },
    { id: "econ", name: "Экономический факультет" },
    { id: "eng", name: "Инженерный институт" },
    { id: "hum", name: "Гуманитарный институт" },
    { id: "med", name: "Медицинский институт" }
  ];

  // ---- Определение 7 блоков ----
  const BLOCKS = [
    { id: "academic", title: "Приём и контингент", icon: "🎓",
      role: ["rector", "prorector", "admin"], desc: "Набор, численность студентов, движение контингента." },
    { id: "results", title: "Академическая результативность", icon: "📚",
      role: ["rector", "prorector", "admin"], desc: "Успеваемость, качество знаний, задолженности." },
    { id: "finance", title: "Финансовое здоровье", icon: "💰",
      role: ["rector", "admin"], desc: "Выручка, ликвидность, долговая нагрузка." },
    { id: "employ", title: "Трудоустройство выпускников", icon: "💼",
      role: ["rector", "prorector", "admin"], desc: "Доля трудоустроенных и карьерные метрики." },
    { id: "science", title: "Исследования и инновации", icon: "🔬",
      role: ["rector", "prorector", "admin"], desc: "Гранты, публикации, интеллектуальная собственность." },
    { id: "ops", title: "Операционная эффективность", icon: "⚙️",
      role: ["rector", "prorector", "admin"], desc: "Наполняемость, нагрузка ППС, цифровая трансформация." },
    { id: "reputation", title: "Репутация и студенческий опыт", icon: "🌟",
      role: ["rector", "prorector", "admin"], desc: "NPS, удовлетворённость, медиа-присутствие." }
  ];
// ---- Определение 32 метрик ----
  // direction: 'higher' (выше — лучше) или 'lower' (ниже — лучше)
  const METRIC_DEFS = [
    { id: "plan-enroll", block: "academic", title: "Выполнение плана приёма", unit: "%", dir: "higher", target: 90, base: 96, vol: 8 },
    { id: "enroll-count", block: "academic", title: "План приёма (бюджет)", unit: "чел.", dir: "higher", target: 1800, base: 1750, vol: 90 },
    { id: "enroll-paid", block: "academic", title: "Внебюджетный приём", unit: "чел.", dir: "higher", target: 900, base: 860, vol: 60 },
    { id: "continuent", block: "academic", title: "Контингент студентов", unit: "чел.", dir: "higher", target: 21000, base: 20800, vol: 220 },
    { id: "rostchislenie", block: "academic", title: "Отчисления (год)", unit: "чел.", dir: "lower", target: 700, base: 640, vol: 70 },
    { id: "procent-otchislenie", block: "academic", title: "Процент отчисления", unit: "%", dir: "lower", target: 3.5, base: 3.1, vol: 0.4 },

    { id: "sredniy-ball", block: "results", title: "Средний балл ЕГЭ", unit: "балл", dir: "higher", target: 70, base: 74, vol: 3 },
    { id: "uspevaemost", block: "results", title: "Успеваемость (без двоек)", unit: "%", dir: "higher", target: 90, base: 94, vol: 2 },
    { id: "kachestvo", block: "results", title: "Качество знаний (без троек)", unit: "%", dir: "higher", target: 65, base: 68, vol: 3 },
    { id: "zadolzhennosti", block: "results", title: "Доля задолженностей", unit: "%", dir: "lower", target: 8, base: 9.4, vol: 1 },
    { id: "otlichniki", block: "results", title: "Доля отличников", unit: "%", dir: "higher", target: 12, base: 13.5, vol: 1.5 },
    { id: "sga", block: "results", title: "Средний семестровый балл", unit: "балл", dir: "higher", target: 4.0, base: 4.1, vol: 0.15 },

    { id: "vyruchka", block: "finance", title: "Выручка (год)", unit: "млн ₽", dir: "higher", target: 3800, base: 3650, vol: 150 },
    { id: "rashody", block: "finance", title: "Расходы (год)", unit: "млн ₽", dir: "lower", target: 3600, base: 3520, vol: 140 },
    { id: "likvidnost", block: "finance", title: "Текущая ликвидность", unit: "x", dir: "higher", target: 1.5, base: 1.35, vol: 0.2 },
    { id: "dolg", block: "finance", title: "Долговая нагрузка", unit: "%", dir: "lower", target: 40, base: 45, vol: 5 },
    { id: "rentabel", block: "finance", title: "Рентабельность", unit: "%", dir: "higher", target: 6, base: 7.4, vol: 1.2 },
    { id: "vnobyudjet", block: "finance", title: "Доля внебюджетных", unit: "%", dir: "higher", target: 30, base: 33, vol: 3 },

    { id: "trudoustroystvo", block: "employ", title: "Доля трудоустроенных", unit: "%", dir: "higher", target: 80, base: 84, vol: 3 },
    { id: "zarplata", block: "employ", title: "Ср. з/п выпускников", unit: "тыс ₽", dir: "higher", target: 55, base: 58, vol: 3 },
    { id: "po-specialnosti", block: "employ", title: "Доля по специальности", unit: "%", dir: "higher", target: 60, base: 63, vol: 3 },

    { id: "granty", block: "science", title: "Объём грантов", unit: "млн ₽", dir: "higher", target: 420, base: 390, vol: 25 },
    { id: "publ-scopus", block: "science", title: "Публикации Scopus/WoS", unit: "шт.", dir: "higher", target: 700, base: 680, vol: 40 },
    { id: "publ-rinc", block: "science", title: "Публикации РИНЦ", unit: "шт.", dir: "higher", target: 1400, base: 1080, vol: 70 },
    { id: "citiruemost", block: "science", title: "Цитируемость", unit: "индекс", dir: "higher", target: 12, base: 12.8, vol: 0.8 },
    { id: "patenty", block: "science", title: "Патенты и РИД", unit: "шт.", dir: "higher", target: 40, base: 38, vol: 4 },

    { id: "napolnyaemost", block: "ops", title: "Наполняемость аудиторий", unit: "%", dir: "higher", target: 75, base: 78, vol: 4 },
    { id: "nagruzka-pps", block: "ops", title: "Средняя нагрузка ППС", unit: "час.", dir: "lower", target: 900, base: 780, vol: 60 },
    { id: "lms-activity", block: "ops", title: "Активность в LMS", unit: "индекс", dir: "higher", target: 55, base: 62, vol: 6 },
    { id: "cifrovyye", block: "ops", title: "Цифровые сервисы (внедрённые)", unit: "шт.", dir: "higher", target: 30, base: 20, vol: 3 },

    { id: "nps", block: "reputation", title: "NPS студентов", unit: "балл", dir: "higher", target: 40, base: 44, vol: 5 },
    { id: "udovletvorennost", block: "reputation", title: "Удовлетворённость", unit: "%", dir: "higher", target: 85, base: 88, vol: 3 },
    { id: "upominaniya", block: "reputation", title: "Упоминания в СМИ", unit: "шт./мес", dir: "higher", target: 150, base: 112, vol: 15 },
    { id: "ton-media", block: "reputation", title: "Тональность медиа", unit: "% позитива", dir: "higher", target: 60, base: 52, vol: 6 }
  ];
// ---- Генерация ряда тренда для метрики ----
  function makeSeries(def, seed) {
    const rnd = mulberry32(seed);
    const points = 12; // 12 прошлых периодов
    const arr = [];
    let v = def.base * (0.9 + rnd() * 0.2);
    const drift = (def.base - v) / points; // тянем к базе
    for (let i = 0; i < points; i++) {
      v += drift + (rnd() - 0.5) * def.vol * 0.5;
      arr.push(Math.max(0, Math.round(v * 10) / 10));
    }
    return arr;
  }

  // ---- Списки для фейковых записей drill-down ----
  const LASTNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Волков", "Лебедев", "Козлов", "Новиков", "Морозов"];
  const FIRSTNAMES = ["Алексей", "Мария", "Дмитрий", "Анна", "Иван", "Елена", "Никита", "Ольга", "Сергей", "Наталья"];
  const REASONS = ["академическая задолженность", "неуспеваемость", "семейные обстоятельства", "перевод в другой вуз", "по собственному желанию", "непрохождение аттестации"];

  function makeDrill(def, seed, deptName, deptId) {
    const rnd = mulberry32(seed);
    const count = 5 + Math.floor(rnd() * 6); // 5..10 записей
    const rows = [];
    for (let i = 0; i < count; i++) {
      rows.push({
        id: deptId + "-" + i,
        fio: LASTNAMES[Math.floor(rnd() * LASTNAMES.length)] + " " +
             FIRSTNAMES[Math.floor(rnd() * FIRSTNAMES.length)],
        dept: deptName,
        value: def.dir === "lower"
          ? Math.round((def.base * (0.6 + rnd() * 0.8)) * 10) / 10
          : Math.round((def.base * (0.85 + rnd() * 0.3)) * 10) / 10,
        reason: REASONS[Math.floor(rnd() * REASONS.length)]
      });
    }
    return rows;
  }
// ---- Сборка полного набора данных ----
  function build(seed) {
    const rnd = mulberry32(seed);
    const blocks = BLOCKS.map((b) => ({ ...b }));
    const metrics = METRIC_DEFS.map((def, i) => {
      const m = {
        ...def,
        seed: seed + i * 101,
        series: makeSeries(def, seed + i * 101),
        deptValues: {}
      };
      m.current = m.series[m.series.length - 1];
      m.prev = m.series[m.series.length - 2] || m.series[0];
      m.delta = m.prev ? ((m.current - m.prev) / m.prev) * 100 : 0;
      m.status = computeStatus(m);
      // значения по подразделениям
      m.dept = DEPARTMENTS.map((d, di) => {
        const v = m.base * (0.82 + rnd() * 0.36);
        const rounded = Math.round(v * 10) / 10;
        const share = (rounded / m.current) * 100;
        return { deptId: d.id, deptName: d.name, value: rounded, share };
      });
      // drill-down записи по каждому подразделению
      m.drill = {};
      DEPARTMENTS.forEach((d, di) => {
        m.drill[d.id] = makeDrill(def, seed + i * 7 + di, d.name, d.id);
      });
      return m;
    });
    return { blocks, metrics, dept: DEPARTMENTS, seed };
  }

  // ---- Статус по целевым порогам ----
  function computeStatus(m) {
    let score;
    if (m.dir === "higher") {
      score = m.target === 0 ? 1 : m.current / m.target;
    } else {
      score = m.target === 0 ? 1 : m.target / m.current;
    }
    if (score >= 0.92) return "green";
    if (score >= 0.8) return "yellow";
    return "red";
  }

  function metricsOfBlock(data, blockId) {
    return data.metrics.filter((m) => m.block === blockId);
  }

  function blocksForRole(data, role) {
    return data.blocks.filter((b) => b.role.includes(role));
  }

  // Начальный набор данных (стабильный сид)
  const initial = build(2026);

  global.DashboardData = {
    MONTHS, QUARTERS, YEARS, DEPARTMENTS,
    BLOCKS, METRIC_DEFS,
    build, computeStatus, metricsOfBlock, blocksForRole,
    initialData: initial
  };
})(window);