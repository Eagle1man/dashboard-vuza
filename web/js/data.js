/* ============================================================
   data.js — данные дашборда ректора
   Южный университет (ИУБиП), ЧОУ ВО.

   Здесь только официальные значения: числа взяты из документов
   университета (отчёт о самообследовании на 01.04.2026, план ФХД на
   2026 год). У них НЕТ исторического ряда: документ даёт одну точку.
   Поэтому series состоит из одного значения, prev и delta равны null,
   а разбивки по кафедрам нет — её нет и в документах.

   Демонстрационные ряды удалены: число без документа выглядело
   настоящим, и подписать его было нечем. Пустой блок честнее
   выдуманного графика.
   ============================================================ */

(function (global) {
  "use strict";

  const MONTHS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
  const QUARTERS = ["Q1", "Q2", "Q3", "Q4"];
  const YEARS = ["2022", "2023", "2024", "2025", "2026"];

  // ---- Подразделения: академия -> кафедра ----
  // Организационная структура на 2026–2027 учебный год (приложение 1
  // к приказу) — 5 академий и 9 кафедр. Принадлежность кафедр
  // академиям указана на странице «Академии» сайта университета.
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

    // --- Учебные результаты ---
    { id: "trudoustroystvo",   block: "results", title: "Трудоустройство выпускников", unit: "%", dir: "higher", target: null },

    // --- Финансово-хозяйственная деятельность ---
    { id: "income",         block: "finance", title: "Доходы (план 2026)", unit: "тыс ₽", dir: "higher", target: null },
    { id: "expense",        block: "finance", title: "Расходы (план 2026)", unit: "тыс ₽", dir: "lower", target: null },
    { id: "income-edu",     block: "finance", title: "Доходы от образовательных услуг", unit: "тыс ₽", dir: "higher", target: null },
    { id: "subsidy",        block: "finance", title: "Субсидия на образование", unit: "тыс ₽", dir: "higher", target: null },
    { id: "fot",            block: "finance", title: "Фонд оплаты труда", unit: "тыс ₽", dir: "lower", target: null },
    { id: "fot-share",      block: "finance", title: "Доля ФОТ в расходах", unit: "%", dir: "higher", target: null },
    { id: "arenda-share",   block: "finance", title: "Доля аренды в расходах", unit: "%", dir: "lower", target: null },
    { id: "subsidy-share",  block: "finance", title: "Доля субсидии в доходах", unit: "%", dir: "higher", target: null },

    // --- Наука ---
    { id: "nir",          block: "science", title: "Объём НИР за 2025", unit: "млн ₽", dir: "higher", target: null },
    { id: "biblioteka",   block: "science", title: "Библиотечный фонд", unit: "экз.", dir: "higher", target: null }
  ];

  // ---- Официальные значения: резерв для режима без сервера ----------
  //
  // Пока сервер не поднят, страница рисует то, что собрано здесь.
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

  // ---- Сборка полного набора данных ----
  function build() {
    const blocks = BLOCKS.map((b) => ({ ...b }));
    const metrics = METRIC_DEFS.map((def) => {
      const m = { ...def, id: def.id, title: def.title, unit: def.unit, dir: def.dir, target: def.target };
      m.source = "official";
      m.hasHistory = false;
      m.current = OFFICIAL_VALUES[def.id];
      m.series = (m.current === undefined) ? [] : [m.current];
      m.prev = m.delta = null;
      m.dept = [];
      m.drill = {};
      m.status = "none";
      return m;
    });
    return { blocks, metrics, dept: DEPARTMENTS, academies: ACADEMIES, standards: STANDARDS };
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

  const initial = build();

  global.DashboardData = {
    MONTHS, QUARTERS, YEARS, DEPARTMENTS, ACADEMIES,
    BLOCKS, METRIC_DEFS, STANDARDS, OFFICIAL_VALUES,
    build, computeStatus, metricsOfBlock, blocksForRole,
    initialData: initial
  };
})(window);
