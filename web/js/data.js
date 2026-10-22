/* ============================================================
   data.js — данные дашборда ректора
   Южный университет (ИУБиП), ЧОУ ВО.

   Два слоя данных, и они различаются по природе:

   * ОФИЦИАЛЬНЫЕ — числа взяты из документов университета (отчёт о
     самообследовании на 01.04.2026, план ФХД на 2026 год). У них
     НЕТ исторического ряда: документ даёт одну точку. Поэтому
     series состоит из одного значения, prev и delta равны null, а
     разбивки по кафедрам нет — её нет и в документах. Придумывать
     к такому числу прошлое нельзя: ровный график ректор прочтёт
     как историю.

   * ДЕМО — то, чего в документах не существует (опросы ВНОКО,
     нагрузка ППС, активность в LMS). У них полный ряд на 12
     месяцев и разбивка по кафедрам.
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

  // ---- Подразделения: академия -> кафедра ----
  // Организационная структура на 2026–2027 учебный год (приложение 1
  // к приказу) — 5 академий и 9 кафедр. Принадлежность кафедр
  // академиям указана на странице «Академии» сайта университета.
  // В отчёте о самообследовании на 01.04.2026 академий тоже было пять,
  // но назывались они иначе, а кафедр было на две больше: «Физическое
  // воспитание» и «Экономика и инновационные рыночные исследования»
  // к 2026–2027 учебному году из структуры ушли.
  const ACADEMIES = [
    { id: "ak-econ",    name: "Академия экономики, туризма и индустрии гостеприимства" },
    { id: "ak-digital", name: "Академия управления и цифрового развития" },
    { id: "ak-law",     name: "Академия права и государственной службы" },
    { id: "ak-hum",     name: "Академия гуманитарных технологий" },
    { id: "ak-prof",    name: "Академия профессионального образования" }
  ];

  const DEPARTMENTS = [
    { id: "kaf-fin",  name: "Кафедра «Финансы, бухучет и налогообложение»",          parentId: "ak-econ" },
    { id: "kaf-tour", name: "Кафедра «Экономика, туризм и инновационные рыночные исследования»", parentId: "ak-econ" },
    { id: "kaf-uo",   name: "Кафедра «Управление организационными системами»",       parentId: "ak-digital" },
    { id: "kaf-it",   name: "Кафедра «Информационные технологии и прикладная математика»", parentId: "ak-digital" },
    { id: "kaf-gp",   name: "Кафедра «Государственно-правовые дисциплины»",           parentId: "ak-law" },
    { id: "kaf-gr",   name: "Кафедра «Гражданско-правовые дисциплины»",               parentId: "ak-law" },
    { id: "kaf-up",   name: "Кафедра «Уголовно-правовые дисциплины»",                parentId: "ak-law" },
    { id: "kaf-psy",  name: "Кафедра «Психология и управление персоналом»",          parentId: "ak-hum" },
    { id: "kaf-hum",  name: "Кафедра «Гуманитарные дисциплины»",                      parentId: "ak-hum" }
  ];

  // ---- 7 стратегических блоков ----
  const BLOCKS = [
    { id: "academic", title: "Академическая политика", icon: "🎓",
      role: ["rector", "prorector", "admin"],
      desc: "Контингент, приём, выпуск, действующие образовательные программы." },
    { id: "results", title: "Учебные результаты", icon: "📚",
      role: ["rector", "prorector", "admin"],
      desc: "Трудоустройство выпускников, успеваемость, качество знаний." },
    { id: "finance", title: "Финансово-хозяйственная деятельность", icon: "💰",
      role: ["rector", "admin"],
      desc: "План ФХД: доходы, расходы, фонд оплаты труда, структура затрат." },
    { id: "vnoko", title: "Качество образования (ВНОКО)", icon: "✅",
      role: ["rector", "prorector", "admin"],
      desc: "Внутренняя независимая оценка качества: опросы и план-факт." },
    { id: "science", title: "Наука и метакомпетенции", icon: "🔬",
      role: ["rector", "prorector", "admin"],
      desc: "Объём НИР, библиотечный фонд, публикации, интеллектуальная собственность." },
    { id: "upr", title: "Воспитательная работа и молодёжная политика", icon: "🤝",
      role: ["rector", "prorector", "admin"],
      desc: "Студенческий совет, юридическая клиника, центр карьеры." },
    { id: "digital", title: "Цифровые сервисы и ЭИОС", icon: "💻",
      role: ["rector", "prorector", "admin"],
      desc: "Электронная информационно-образовательная среда, LMS, сервисы." }
  ];

  // ---- Метрики ----
  // dir: 'higher' (выше — лучше) или 'lower' (ниже — лучше)
  // target: целевой порог. null — в документах его нет, статус не считается.
  // base / vol — параметры генератора, только для демо-метрик.
  const METRIC_DEFS = [
    // --- Академическая политика ---
    { id: "continuer-total",   block: "academic", title: "Контингент обучающихся",  unit: "чел.", dir: "higher", target: null },
    { id: "continuer-spo",     block: "academic", title: "Контингент СПО",          unit: "чел.", dir: "higher", target: null },
    { id: "continuer-ba",      block: "academic", title: "Контингент бакалавриата",  unit: "чел.", dir: "higher", target: null },
    { id: "continuer-ma",      block: "academic", title: "Контингент магистратуры",  unit: "чел.", dir: "higher", target: null },
    { id: "continuer-aspir",   block: "academic", title: "Аспиранты",               unit: "чел.", dir: "higher", target: null },
    { id: "applicant-2025",    block: "academic", title: "Принято на 1 курс (2025)", unit: "чел.", dir: "higher", target: null },
    { id: "graduate-2025",     block: "academic", title: "Выпуск 2025",             unit: "чел.", dir: "higher", target: null },
    { id: "foreign",           block: "academic", title: "Иностранцы",                unit: "чел.", dir: "higher", target: null },
    { id: "programs",          block: "academic", title: "Действующие образовательные программы", unit: "шт.", dir: "higher", target: null },
    { id: "plan-enroll",       block: "academic", title: "Выполнение плана приёма", unit: "%", dir: "higher", target: 90, base: 96, vol: 8 },

    // --- Учебные результаты ---
    { id: "trudoustroystvo",   block: "results", title: "Трудоустройство выпускников", unit: "%", dir: "higher", target: null },
    { id: "sredniy-ball",      block: "results", title: "Средний балл ЕГЭ", unit: "балл", dir: "higher", target: 70, base: 74, vol: 3 },
    { id: "uspevaemost",       block: "results", title: "Успеваемость (без двоек)", unit: "%", dir: "higher", target: 90, base: 94, vol: 2 },
    { id: "kachestvo",         block: "results", title: "Качество знаний (без троек)", unit: "%", dir: "higher", target: 65, base: 68, vol: 3 },
    { id: "zadolzhennosti",    block: "results", title: "Доля задолженностей", unit: "%", dir: "lower", target: 8, base: 9.4, vol: 1 },
    { id: "otlichniki",        block: "results", title: "Доля отличников", unit: "%", dir: "higher", target: 12, base: 13.5, vol: 1.5 },
    { id: "sga",               block: "results", title: "Средний семестровый балл", unit: "балл", dir: "higher", target: 4.0, base: 4.1, vol: 0.15 },

    // --- Финансово-хозяйственная деятельность ---
    { id: "income",         block: "finance", title: "Доходы (план 2026)", unit: "тыс ₽", dir: "higher", target: null },
    { id: "expense",        block: "finance", title: "Расходы (план 2026)", unit: "тыс ₽", dir: "lower", target: null },
    { id: "income-edu",     block: "finance", title: "Доходы от образовательных услуг", unit: "тыс ₽", dir: "higher", target: null },
    { id: "subsidy",        block: "finance", title: "Субсидия на образование", unit: "тыс ₽", dir: "higher", target: null },
    { id: "fot",            block: "finance", title: "Фонд оплаты труда", unit: "тыс ₽", dir: "lower", target: null },
    { id: "fot-share",      block: "finance", title: "Доля ФОТ в расходах", unit: "%", dir: "higher", target: null },
    { id: "arenda-share",   block: "finance", title: "Доля аренды в расходах", unit: "%", dir: "lower", target: null },
    { id: "subsidy-share",  block: "finance", title: "Доля субсидии в доходах", unit: "%", dir: "higher", target: null },

    // --- Качество образования (ВНОКО): демо ---
    { id: "vnoko-employers", block: "vnoko", title: "Опрос работодателей", unit: "%", dir: "higher", target: 85, base: 88, vol: 2 },
    { id: "vnoko-staff",     block: "vnoko", title: "Опрос педагогических работников", unit: "%", dir: "higher", target: 85, base: 86, vol: 2 },
    { id: "vnoko-parents",   block: "vnoko", title: "Опрос родителей", unit: "%", dir: "higher", target: 85, base: 84, vol: 2 },
    { id: "vnoko-students",  block: "vnoko", title: "Опрос обучающихся", unit: "%", dir: "higher", target: 85, base: 89, vol: 2 },
    { id: "vnoko-vpr",       block: "vnoko", title: "Средний балл ВПР (СПО)", unit: "балл", dir: "higher", target: 4.0, base: 4.2, vol: 0.1 },
    { id: "vnoko-planfact",  block: "vnoko", title: "Доля ООП с опубликованным планом-фактом", unit: "%", dir: "higher", target: 95, base: 80, vol: 3 },

    // --- Наука ---
    { id: "nir",          block: "science", title: "Объём НИР за 2025", unit: "млн ₽", dir: "higher", target: null },
    { id: "biblioteka",   block: "science", title: "Библиотечный фонд", unit: "экз.", dir: "higher", target: null },
    { id: "granty",       block: "science", title: "Объём грантов", unit: "млн ₽", dir: "higher", target: 420, base: 390, vol: 25 },
    { id: "publ-scopus",  block: "science", title: "Публикации Scopus/WoS", unit: "шт.", dir: "higher", target: 700, base: 680, vol: 40 },
    { id: "publ-rinc",    block: "science", title: "Публикации РИНЦ", unit: "шт.", dir: "higher", target: 1400, base: 1080, vol: 70 },
    { id: "citiruemost",  block: "science", title: "Цитируемость", unit: "индекс", dir: "higher", target: 12, base: 12.8, vol: 0.8 },
    { id: "patenty",      block: "science", title: "Патенты и РИД", unit: "шт.", dir: "higher", target: 40, base: 38, vol: 4 },

    // --- Воспитательная работа и молодёжная политика: демо ---
    { id: "studcouncil",    block: "upr", title: "Мероприятия студсовета", unit: "шт./год", dir: "higher", target: 60, base: 52, vol: 5 },
    { id: "clinic",         block: "upr", title: "Дела юридической клиники", unit: "шт.", dir: "higher", target: 40, base: 34, vol: 4 },
    { id: "career-centre",  block: "upr", title: "Обращений в центр карьеры", unit: "тыс", dir: "higher", target: 12, base: 11, vol: 1 },
    { id: "vovlechennost",  block: "upr", title: "Вовлечённость в воспитательные мероприятия", unit: "%", dir: "higher", target: 70, base: 66, vol: 4 },

    // --- Цифровые сервисы и ЭИОС: демо ---
    { id: "napolnyaemost", block: "digital", title: "Наполняемость аудиторий", unit: "%", dir: "higher", target: 75, base: 78, vol: 4 },
    { id: "nagruzka-pps",  block: "digital", title: "Средняя нагрузка ППС", unit: "час.", dir: "lower", target: 900, base: 780, vol: 60 },
    { id: "lms-activity",  block: "digital", title: "Активность в LMS", unit: "индекс", dir: "higher", target: 55, base: 62, vol: 6 },
    { id: "cifrovyye",     block: "digital", title: "Цифровые сервисы (внедрённые)", unit: "шт.", dir: "higher", target: 30, base: 20, vol: 3 },
    { id: "eios",          block: "digital", title: "Доля курсов в ЭИОС", unit: "%", dir: "higher", target: 90, base: 85, vol: 3 }
  ];

  // ---- Официальные значения: резерв для режима без сервера ----------
  //
  // Пока сервер не поднят, страница рисует то, что собрано здесь. У
  // официальных показателей раньше в этом режиме вместо числа стояло
  // прочерк, а рядом — демонстрационные цифры: выглядело так, будто
  // настоящих данных нет вовсе.
  //
  // Значения дублируются в backend/db.py (OFFICIAL). Расхождение
  // ловит backend/tests/test_seed_matches_js — вручную их сверять
  // бессмысленно, их всегда сверяет тест.
  const OFFICIAL_VALUES = {
    "continuer-total": 2946, "continuer-spo": 1328, "continuer-ba": 1145,
    "continuer-ma": 443, "continuer-aspir": 30, "applicant-2025": 1245,
    "graduate-2025": 888, "foreign": 55, "programs": 25,
    "trudoustroystvo": 90, "income": 221475.08, "expense": 221475.08,
    "income-edu": 184447.27, "subsidy": 8287.30, "fot": 89900,
    "fot-share": 40.6, "arenda-share": 15.9, "subsidy-share": 3.7,
    "nir": 18, "biblioteka": 84700
  };

  // ---- Образовательные программы (ФГОС) ----
  const STANDARDS = [
    { code: "09.03.03", level: "Бакалавриат", title: "Прикладная информатика", approved: 2017, updated: 2023 },
    { code: "37.03.01", level: "Бакалавриат", title: "Психология", approved: 2020, updated: 2023 },
    { code: "38.03.01", level: "Бакалавриат", title: "Экономика", approved: 2020, updated: 2023 },
    { code: "38.03.02", level: "Бакалавриат", title: "Менеджмент", approved: 2020, updated: 2023 },
    { code: "38.03.03", level: "Бакалавриат", title: "Управление персоналом", approved: 2020, updated: 2023 },
    { code: "38.03.05", level: "Бакалавриат", title: "Бизнес-информатика", approved: 2020, updated: 2023 },
    { code: "40.03.01", level: "Бакалавриат", title: "Юриспруденция", approved: 2020, updated: 2023 },
    { code: "43.03.02", level: "Бакалавриат", title: "Туризм", approved: 2017, updated: 2023 },
    { code: "09.04.03", level: "Магистратура", title: "Прикладная информатика", approved: 2017, updated: 2021 },
    { code: "37.04.01", level: "Магистратура", title: "Психология", approved: 2020, updated: 2020 },
    { code: "38.04.01", level: "Магистратура", title: "Экономика", approved: 2020, updated: 2020 },
    { code: "38.04.02", level: "Магистратура", title: "Менеджмент", approved: 2020, updated: 2020 },
    { code: "40.04.01", level: "Магистратура", title: "Юриспруденция", approved: 2020, updated: 2020 },
    { code: "43.04.02", level: "Магистратура", title: "Туризм", approved: 2017, updated: 2021 },
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

  // ---- Генерация ряда тренда (только для демо-метрик) ----
  function makeSeries(def, seed) {
    const rnd = mulberry32(seed);
    const points = 12;
    const arr = [];
    let v = def.base * (0.9 + rnd() * 0.2);
    const drift = (def.base - v) / points;
    for (let i = 0; i < points; i++) {
      v += drift + (rnd() - 0.5) * def.vol * 0.5;
      arr.push(Math.max(0, Math.round(v * 10) / 10));
    }
    return arr;
  }

  const LASTNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Смирнов", "Волков", "Лебедев", "Козлов", "Новиков", "Морозов"];
  // Единицы, для которых разбивка по кафедрам осмысленна: счётные
  // величины. Держим в одном списке с backend/db.py (COUNT_UNITS) —
  // иначе витрина в браузере и в базе разойдутся.
  const COUNT_UNITS = { "чел.": 1, "шт.": 1, "шт./год": 1, "тыс": 1, "млн ₽": 1 };
  const FIRSTNAMES = ["Алексей", "Мария", "Дмитрий", "Анна", "Иван", "Елена", "Никита", "Ольга", "Сергей", "Наталья"];
  const REASONS = ["академическая задолженность", "неуспеваемость", "семейные обстоятельства", "перевод в другой вуз", "по собственному желанию", "непрохождение аттестации"];

  function makeDrill(def, seed, deptName, deptId) {
    const rnd = mulberry32(seed);
    const count = 5 + Math.floor(rnd() * 6);
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
      const m = { ...def, id: def.id, title: def.title, unit: def.unit, dir: def.dir, target: def.target };

      if (def.base === undefined) {
        // Официальная метрика: значения подставляет seedVitrine()
        // из базы. Ряда нет — рисовать нечего. Но и прочерка вместо
        // числа быть не должно: пока сервер не поднят, значение
        // известно из документов и показывается.
        m.source = "official";
        m.hasHistory = false;
        m.current = OFFICIAL_VALUES[def.id];
        m.series = (m.current === undefined) ? [] : [m.current];
        m.prev = m.delta = null;
        m.dept = [];
        m.drill = {};
        m.status = "none";
        return m;
      }

      m.source = "demo";
      m.hasHistory = true;
      m.seed = seed + i * 101;
      m.series = makeSeries(def, seed + i * 101);
      m.current = m.series[m.series.length - 1];
      m.prev = m.series[m.series.length - 2] || m.series[0];
      m.delta = m.prev ? ((m.current - m.prev) / m.prev) * 100 : 0;
      m.status = computeStatus(m);

      // Разбивка по кафедрам строится только для счётных величин.
      // Для процентов, баллов, индексов и часов она бессмысленна: кафедра
      // получила бы долю от процента, и «успеваемость 11,4 %» читалось бы
      // как провал там, где на деле 94 % по университету.
      if (COUNT_UNITS[def.unit]) {
        const weights = DEPARTMENTS.map(function () { return 0.82 + rnd() * 0.36; });
        const wsum = weights.reduce(function (a, b) { return a + b; }, 0);
        m.dept = DEPARTMENTS.map((d, di) => {
          const v = m.current * (weights[di] / wsum);
          const rounded = Math.round(v * 10) / 10;
          const share = (rounded / m.current) * 100;
          return { deptId: d.id, deptName: d.name, value: rounded, share };
        });
        m.drill = {};
        DEPARTMENTS.forEach((d, di) => {
          m.drill[d.id] = makeDrill(def, seed + i * 7 + di, d.name, d.id);
        });
      } else {
        m.dept = [];
        m.drill = {};
      }
      return m;
    });
    return { blocks, metrics, dept: DEPARTMENTS, academies: ACADEMIES, standards: STANDARDS, seed };
  }

  // ---- Статус по целевым порогам ----
  function computeStatus(m) {
    // Порога нет — статус по нему считать не из чего.
    if (m.target === null || m.target === undefined) return "none";
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

  const initial = build(2026);

  global.DashboardData = {
    MONTHS, QUARTERS, YEARS, DEPARTMENTS, ACADEMIES,
    BLOCKS, METRIC_DEFS, STANDARDS, OFFICIAL_VALUES,
    build, computeStatus, metricsOfBlock, blocksForRole,
    initialData: initial
  };
})(window);
