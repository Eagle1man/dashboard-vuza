/* ============================================================
   data.js — демо-данные дашборда ректора
   Южный университет (ИУБиП), ЧОУ ВО.

   Структура и подразделения — реальные, по организационной
   структуре на 2026–2027 учебный год (приложение 1 к приказу).
   Цифры — демонстрационные, детерминированные (seeded RNG).

   6 стратегических блоков, 32 метрики, 25 образовательных
   программ (ФГОС), drill-down записи.
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

  // ---- Подразделения: реальные кафедры университета ----
  // Источник: организационная структура на 2026–2027 учебный год.
  // Кафедры — учебное ядро, поэтому разбивка показателей идёт по ним.
  const DEPARTMENTS = [
    { id: "kaf-it",  name: "Кафедра «Информационные технологии и прикладная математика»" },
    { id: "kaf-uo",  name: "Кафедра «Управление организационными системами»" },
    { id: "kaf-psy", name: "Кафедра «Психология и управление персоналом»" },
    { id: "kaf-gum", name: "Кафедра «Гуманитарные дисциплины»" },
    { id: "kaf-gr",  name: "Кафедра «Гражданско-правовые дисциплины»" },
    { id: "kaf-eco", name: "Кафедра «Экономика, туризм и инновационные рыночные исследования»" }
  ];

  // ---- 6 стратегических блоков ----
  // Блоки соответствуют зонам ответственности проректоров и
  // направлениям организационной структуры. Финансового и
  // кадрового блоков в структуре нет — они не появились и здесь.
  const BLOCKS = [
    { id: "academic", title: "Академическая политика", icon: "🎓",
      role: ["rector", "prorector", "admin"],
      desc: "Приём, контингент, действующие образовательные программы." },
    { id: "results", title: "Учебные результаты", icon: "📚",
      role: ["rector", "prorector", "admin"],
      desc: "Успеваемость, качество знаний, промежуточная аттестация." },
    { id: "vnoko", title: "Качество образования (ВНОКО)", icon: "✅",
      role: ["rector", "prorector", "admin"],
      desc: "Внутренняя независимая оценка качества: опросы и план-факт." },
    { id: "science", title: "Наука и метакомпетенции", icon: "🔬",
      role: ["rector", "prorector", "admin"],
      desc: "Гранты, публикации, интеллектуальная собственность, НИЛ." },
    { id: "upr", title: "Воспитательная работа и молодёжная политика", icon: "🤝",
      role: ["rector", "prorector", "admin"],
      desc: "Студенческий совет, юридическая клиника, центр карьеры." },
    { id: "digital", title: "Цифровые сервисы и ЭИОС", icon: "💻",
      role: ["rector", "prorector", "admin"],
      desc: "Электронная информационно-образовательная среда, LMS, сервисы." }
  ];

  // ---- 32 метрики ----
  // direction: 'higher' (выше — лучше) или 'lower' (ниже — лучше)
  // base / vol — параметры генератора тренда, как и раньше.
  const METRIC_DEFS = [
    // Академическая политика
    { id: "plan-enroll", block: "academic", title: "Выполнение плана приёма", unit: "%", dir: "higher", target: 90, base: 96, vol: 8 },
    { id: "programs", block: "academic", title: "Действующие образовательные программы", unit: "шт.", dir: "higher", target: 25, base: 25, vol: 0 },
    { id: "enroll-count", block: "academic", title: "Приём на программы (год)", unit: "чел.", dir: "higher", target: 1800, base: 1750, vol: 90 },
    { id: "continuent", block: "academic", title: "Контингент обучающихся", unit: "чел.", dir: "higher", target: 21000, base: 20800, vol: 220 },
    { id: "rostchislenie", block: "academic", title: "Отчисления (год)", unit: "чел.", dir: "lower", target: 700, base: 640, vol: 70 },
    { id: "procent-otchislenie", block: "academic", title: "Процент отчисления", unit: "%", dir: "lower", target: 3.5, base: 3.1, vol: 0.4 },

    // Учебные результаты
    { id: "sredniy-ball", block: "results", title: "Средний балл ЕГЭ", unit: "балл", dir: "higher", target: 70, base: 74, vol: 3 },
    { id: "uspevaemost", block: "results", title: "Успеваемость (без двоек)", unit: "%", dir: "higher", target: 90, base: 94, vol: 2 },
    { id: "kachestvo", block: "results", title: "Качество знаний (без троек)", unit: "%", dir: "higher", target: 65, base: 68, vol: 3 },
    { id: "zadolzhennosti", block: "results", title: "Доля задолженностей", unit: "%", dir: "lower", target: 8, base: 9.4, vol: 1 },
    { id: "otlichniki", block: "results", title: "Доля отличников", unit: "%", dir: "higher", target: 12, base: 13.5, vol: 1.5 },
    { id: "sga", block: "results", title: "Средний семестровый балл", unit: "балл", dir: "higher", target: 4.0, base: 4.1, vol: 0.15 },

    // Качество образования (ВНОКО)
    // Четыре опроса — по группам респондентов, как в отчётности
    // внутренней независимой оценки качества образования.
    { id: "vnoko-employers", block: "vnoko", title: "Опрос работодателей", unit: "%", dir: "higher", target: 85, base: 88, vol: 2 },
    { id: "vnoko-staff", block: "vnoko", title: "Опрос педагогических работников", unit: "%", dir: "higher", target: 85, base: 86, vol: 2 },
    { id: "vnoko-parents", block: "vnoko", title: "Опрос родителей", unit: "%", dir: "higher", target: 85, base: 84, vol: 2 },
    { id: "vnoko-students", block: "vnoko", title: "Опрос обучающихся", unit: "%", dir: "higher", target: 85, base: 89, vol: 2 },
    { id: "vnoko-vpr", block: "vnoko", title: "Средний балл ВПР (СПО)", unit: "балл", dir: "higher", target: 4.0, base: 4.2, vol: 0.1 },
    { id: "vnoko-planfact", block: "vnoko", title: "Доля ООП с опубликованным планом-фактом", unit: "%", dir: "higher", target: 95, base: 80, vol: 3 },

    // Наука и метакомпетенции
    { id: "granty", block: "science", title: "Объём грантов", unit: "млн ₽", dir: "higher", target: 420, base: 390, vol: 25 },
    { id: "publ-scopus", block: "science", title: "Публикации Scopus/WoS", unit: "шт.", dir: "higher", target: 700, base: 680, vol: 40 },
    { id: "publ-rinc", block: "science", title: "Публикации РИНЦ", unit: "шт.", dir: "higher", target: 1400, base: 1080, vol: 70 },
    { id: "citiruemost", block: "science", title: "Цитируемость", unit: "индекс", dir: "higher", target: 12, base: 12.8, vol: 0.8 },
    { id: "patenty", block: "science", title: "Патенты и РИД", unit: "шт.", dir: "higher", target: 40, base: 38, vol: 4 },

    // Воспитательная работа и молодёжная политика
    { id: "studcouncil", block: "upr", title: "Мероприятия студсовета", unit: "шт./год", dir: "higher", target: 60, base: 52, vol: 5 },
    { id: "clinic", block: "upr", title: "Дела юридической клиники", unit: "шт.", dir: "higher", target: 40, base: 34, vol: 4 },
    { id: "career-centre", block: "upr", title: "Обращений в центр карьеры", unit: "тыс", dir: "higher", target: 12, base: 11, vol: 1 },
    { id: "vovlechennost", block: "upr", title: "Вовлечённость в воспитательные мероприятия", unit: "%", dir: "higher", target: 70, base: 66, vol: 4 },

    // Цифровые сервисы и ЭИОС
    { id: "napolnyaemost", block: "digital", title: "Наполняемость аудиторий", unit: "%", dir: "higher", target: 75, base: 78, vol: 4 },
    { id: "nagruzka-pps", block: "digital", title: "Средняя нагрузка ППС", unit: "час.", dir: "lower", target: 900, base: 780, vol: 60 },
    { id: "lms-activity", block: "digital", title: "Активность в LMS", unit: "индекс", dir: "higher", target: 55, base: 62, vol: 6 },
    { id: "cifrovyye", block: "digital", title: "Цифровые сервисы (внедрённые)", unit: "шт.", dir: "higher", target: 30, base: 20, vol: 3 },
    { id: "eios", block: "digital", title: "Доля курсов в ЭИОС", unit: "%", dir: "higher", target: 90, base: 85, vol: 3 }
  ];

  // ---- Образовательные программы (ФГОС) ----
  // Источник: раздел «Образовательные стандарты и требования»
  // на сайте университета. approved — год утверждения действующей
  // редакции, updated — год последнего изменения. Число программ
  // и коды реальные; численность обучающихся по программам на
  // сайте не публикуется, поэтому здесь её нет.
  const STANDARDS = [
    // Бакалавриат
    { code: "09.03.03", level: "Бакалавриат", title: "Прикладная информатика", approved: 2017, updated: 2023 },
    { code: "37.03.01", level: "Бакалавриат", title: "Психология", approved: 2020, updated: 2023 },
    { code: "38.03.01", level: "Бакалавриат", title: "Экономика", approved: 2020, updated: 2023 },
    { code: "38.03.02", level: "Бакалавриат", title: "Менеджмент", approved: 2020, updated: 2023 },
    { code: "38.03.03", level: "Бакалавриат", title: "Управление персоналом", approved: 2020, updated: 2023 },
    { code: "38.03.05", level: "Бакалавриат", title: "Бизнес-информатика", approved: 2020, updated: 2023 },
    { code: "40.03.01", level: "Бакалавриат", title: "Юриспруденция", approved: 2020, updated: 2023 },
    { code: "43.03.02", level: "Бакалавриат", title: "Туризм", approved: 2017, updated: 2023 },
    // Магистратура
    { code: "09.04.03", level: "Магистратура", title: "Прикладная информатика", approved: 2017, updated: 2021 },
    { code: "37.04.01", level: "Магистратура", title: "Психология", approved: 2020, updated: 2020 },
    { code: "38.04.01", level: "Магистратура", title: "Экономика", approved: 2020, updated: 2020 },
    { code: "38.04.02", level: "Магистратура", title: "Менеджмент", approved: 2020, updated: 2020 },
    { code: "40.04.01", level: "Магистратура", title: "Юриспруденция", approved: 2020, updated: 2020 },
    { code: "43.04.02", level: "Магистратура", title: "Туризм", approved: 2017, updated: 2021 },
    // Среднее профессиональное образование
    { code: "09.02.07", level: "СПО", title: "Информационные системы и программирование", approved: 2016, updated: 2024 },
    { code: "09.02.11", level: "СПО", title: "Разработка и управление программным обеспечением", approved: 2025, updated: 2025 },
    { code: "09.02.12", level: "СПО", title: "Техническая эксплуатация и сопровождение информационных систем", approved: 2025, updated: 2025 },
    { code: "31.02.02", level: "СПО", title: "Акушерское дело", approved: 2022, updated: 2026 },
    { code: "31.02.06", level: "СПО", title: "Стоматология профилактическая", approved: 2022, updated: 2026 },
    { code: "33.02.01", level: "СПО", title: "Фармация", approved: 2021, updated: 2026 },
    { code: "34.02.01", level: "СПО", title: "Сестринское дело", approved: 2022, updated: 2026 },
    { code: "38.02.01", level: "СПО", title: "Экономика и бухгалтерский учет (по отраслям)", approved: 2024, updated: 2024 },
    { code: "38.02.03", level: "СПО", title: "Операционная деятельность в логистике", approved: 2022, updated: 2024 },
    { code: "40.02.04", level: "СПО", title: "Юриспруденция", approved: 2023, updated: 2023 },
    { code: "43.02.16", level: "СПО", title: "Туризм и гостеприимство", approved: 2022, updated: 2024 }
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
      // Доли подразделений обязаны в сумме давать итог по университету.
      // Раньше каждое подразделение получало base * (0.82...1.18)
      // независимо, и шесть таких значений давали примерно шестикратный
      // итог. Это было незаметно, пока цифры показывались только общей
      // строкой; как только агенты стали считать разбивку по факультетам,
      // части перестали сходиться с целым.
      const weights = DEPARTMENTS.map(function () { return 0.82 + rnd() * 0.36; });
      const wsum = weights.reduce(function (a, b) { return a + b; }, 0);
      m.dept = DEPARTMENTS.map((d, di) => {
        const v = m.current * (weights[di] / wsum);
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
    return { blocks, metrics, dept: DEPARTMENTS, standards: STANDARDS, seed };
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
    BLOCKS, METRIC_DEFS, STANDARDS,
    build, computeStatus, metricsOfBlock, blocksForRole,
    initialData: initial
  };
})(window);
