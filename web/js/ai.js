/* ============================================================
   ai.js — ИИ-ассистент «Аналитик ректора»
   Гибридный режим:
    1) если задан ключ DeepSeek (localStorage 'dash_ai_key'),
       выполняется реальный запрос к DeepSeek API;
    2) иначе работает встроенная аналитика по данным дашборда
       (без сети) — через семантический слой метрик.
   ============================================================ */

(function (global) {
  "use strict";

  const STRIP = function (s) { return String(s).toLowerCase().replace(/[^a-zа-яё0-9\s]/gi, " ").trim(); };

  // Семантический слой: синонимы метрик для матчинга
  const SYNONYM_MAP = [
    { key: "приём|набор|поступлен|план приёма", ids: ["plan-enroll", "enroll-count", "enroll-paid"] },
    { key: "отчисл|отсев|выбыти", ids: ["rostchislenie", "procent-otchislenie"] },
    { key: "контингент|студентов|численность", ids: ["continuent"] },
    { key: "успеваемост|задолж", ids: ["uspevaemost", "zadolzhennosti", "kachestvo", "otlichniki", "sga", "sredniy-ball"] },
    { key: "трудоустро|работ|карьер|зарплат", ids: ["trudoustroystvo", "zarplata", "po-specialnosti"] },
    { key: "финанс|выручк|доход|ликвид|расход|долг|рентабель|бюджет", ids: ["vyruchka", "rashody", "likvidnost", "dolg", "rentabel", "vnobyudjet"] },
    { key: "грант|публикац|скопус|исследова|наук|патент|цитируем", ids: ["granty", "publ-scopus", "publ-rinc", "patenty", "citiruemost"] },
    { key: "аудитор|нагрузк|ппс|lms|цифр|наполняем", ids: ["napolnyaemost", "nagruzka-pps", "lms-activity", "cifrovyye"] },
    { key: "nps|репутац|медиа|см|удовлетвор|студенческ опыт|отзыв", ids: ["nps", "udovletvorennost", "upominaniya", "ton-media"] }
  ];

  // Найти метрики по вопросу
  function matchMetrics(question, data) {
    const q = STRIP(question);
    const found = new Set();
    for (const syn of SYNONYM_MAP) {
      const re = new RegExp("(" + syn.key + ")", "i");
      if (re.test(q)) syn.ids.forEach((id) => found.add(id));
    }
    if (found.size === 0) return [];
    return data.metrics.filter((m) => found.has(m.id));
  }

  function blockName(data, id) {
    const b = data.blocks.find((x) => x.id === id);
    return b ? b.title : id;
  }

  // Формирование рекомендации графика
  const DEFAULT_LABELS = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];
  function chartSuggestion(metric, prevLabels) {
    prevLabels = prevLabels || DEFAULT_LABELS;
    const s = metric.series;
    const last = Math.min(s.length, prevLabels.length);
    const labels = prevLabels.slice(-last);
    const serie = s.slice(-last);
    return {
      type: "line",
      title: metric.title,
      labels: labels,
      data: serie,
      unit: metric.unit
    };
  }

  // ---- Вспомогательные функции (внутри замыкания) ----
  function fmtVal(v, unit) {
    let s;
    // Значение может отсутствовать (нет данных за период) — прежнее
    // v.toFixed(2) на этом падало.
    if (v === null || v === undefined || isNaN(v)) s = "—";
    else if (v >= 1000000) s = (v / 1000000).toFixed(2) + " М";
    else if (v >= 10000) s = (v / 1000).toFixed(1) + " К";
    else s = parseFloat(v.toFixed(2)).toString();
    return s + (unit ? " " + unit : "");
  }

  function blockDesc(data, blockId) {
    const b = data.blocks.find((x) => x.id === blockId);
    return b ? b.desc : "";
  }

  function sourceSystem(blockId) {
    // Блоки в структуре 2026–2027: academic, results, finance, vnoko,
    // science, upr, digital. Коды employ, ops и reputation к ней не
    // относятся — их больше нет, и раньше подпись под ответом
    // уводила в несуществующую систему.
    const map = {
      academic: "приёмная кампания, 1С:Университет",
      results: "деканаты, Moodle",
      finance: "бухгалтерия, план ФХД",
      vnoko: "ВНОКО, результаты опросов",
      science: "научная часть, Scopus/WoS",
      upr: "управление по воспитательной работе",
      digital: "ИТ-служба, ЭИОС"
    };
    return map[blockId] || "Витрина данных";
  }

  // Отчёт по блоку
  function reportByBlock(question, data, blockId, dept, charts) {
    const mets = data.metrics.filter((m) => m.block === blockId);
    if (mets.length === 0) return { text: "По этому блоку нет данных.", source: "", charts: [] };
    const filter = dept ? dept.name : "весь университет";
    let lines = [];
    lines.push("**Сводка по блоку «" + blockName(data, blockId) + "» (" + filter + "):**\n");
    let green = 0, yellow = 0, red = 0;
    mets.forEach((m) => {
      const cur = dept && m.dept ? (m.dept.find((d) => d.deptId === dept.id) ? m.dept.find((d) => d.deptId === dept.id).value : m.current) : m.current;
      // У официального показателя изменения нет: исторического ряда нет,
      // сравнивать не с чем. Раньше здесь стояло m.delta.toFixed(1) без
      // проверки, и весь ответ падал.
      const dt = (m.delta === null || m.delta === undefined)
        ? "нет данных для сравнения"
        : ((m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%");
      lines.push("• " + m.title + " — " + fmtVal(cur, m.unit) + " (" + dt + ")");
      if (m.status === "green") green++;
      else if (m.status === "yellow") yellow++;
      else if (m.status === "red") red++;
    });
    lines.push("\nСтатус блока: " + green + " зелёных, " + yellow + " жёлтых, " + red + " красных показателей.");
    if (red > 0) lines.push("⚠️ Требуют внимания (красный): " + mets.filter((m) => m.status === "red").map((m) => m.title).join(", ") + ".");
    charts.push(chartSuggestion(mets[0], DEFAULT_LABELS));
    let drill = [];
    // Подразделения с id «ff» в структуре больше нет, поэтому записи
    // детализации по всему университету собираются по всем ключам.
    const dd = dept
      ? (mets[0].drill && mets[0].drill[dept.id])
      : (mets[0].drill && Object.keys(mets[0].drill).reduce(
          (acc, k) => acc.concat(mets[0].drill[k] || []), []));
    drill = dd || [];
    return {
      text: lines.join("\n"),
      source: "Витрина: " + blockName(data, blockId) + " · источник: " + sourceSystem(blockId),
      drill: drill.slice(0, 5),
      charts: charts,
      blockId: blockId,
      metricId: mets[0].id
    };
  }

  function drillText(drill, unit) {
    if (!drill || !drill.length) return "";
    return drill.map((r) => "   — " + r.fio + " · " + r.dept + " · " + fmtVal(r.value, unit)).join("\n");
  }

  // Встроенная аналитическая заглушка
  function answerLocal(question, data, ctx) {
    const q = STRIP(question);
    let dept = null;
    data.dept.forEach((d) => {
      if (q.indexOf(STRIP(d.name).slice(0, 12)) !== -1) dept = d;
    });
    const matched = matchMetrics(question, data);
    const charts = [];

    const reportMatch = /(отчёт|отчет|сводк|summary|резюме|недельный|еженедел)/.test(q);
    if (reportMatch) {
      let blockId = null;
      const blockAlias = { "приём": "academic", "контингент": "academic", "академич": "results", "успеваем": "results", "качество образования": "vnoko", "внoko": "vnoko", "вноко": "vnoko", "опрос": "vnoko", "план-факт": "vnoko", "впр": "vnoko", "наук": "science", "грант": "science", "публикац": "science", "воспитан": "upr", "студсовет": "upr", "клиник": "upr", "карьер": "upr", "цифров": "digital", "lms": "digital", "эиос": "digital", "нагрузк": "digital" };
      Object.keys(blockAlias).forEach((k) => { if (q.indexOf(k) !== -1) blockId = blockAlias[k]; });
      if (!blockId) blockId = ctx.currentBlockId || "academic";
      return reportByBlock(question, data, blockId, dept, charts);
    }

    if (matched.length === 0) {
      return {
        text: "Не удалось однозначно сопоставить вопрос с метриками дашборда. Уточните тему: приём и контингент, успеваемость, качество образования (ВНОКО), наука, воспитательная работа и карьера, цифровые сервисы. Например: «Почему выросло отчисление на 1 курсе?»",
        source: "Семантический слой метрик",
        charts: []
      };
    }

    const m = matched[0];
    let lines = [];
    const label = dept ? dept.name : "в целом по университету";
    const cur = dept && m.dept ? (m.dept.find((d) => d.deptId === dept.id) ? m.dept.find((d) => d.deptId === dept.id).value : m.current) : m.current;

    // У официального показателя изменения нет: исторического ряда нет,
    // сравнивать не с чем. Раньше здесь стояло m.delta.toFixed(1) без
    // проверки, и весь ответ падал.
    const deltaTxt = (m.delta === null || m.delta === undefined)
      ? "исторического ряда нет, изменение не рассчитано"
      : ((m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%");
    lines.push("• **" + m.title + "** (" + label + "): текущее значение **" + fmtVal(cur, m.unit) + "**, изменение к прошлому периоду **" + deltaTxt + "**.");

    if (dept && m.current) {
      const diff = ((cur - m.current) / m.current) * 100;
      lines.push("Это " + (diff >= 0 ? "выше" : "ниже") + " среднего по университету (" + fmtVal(m.current, m.unit) + ") на " + Math.abs(diff).toFixed(1) + "%.");
    }

    // Статус «none» — это «целевого порога нет», а не «показатель ниже
    // критичного порога». Раньше он попадал в последнюю ветку, и
    // официальный показатель объявлялся провалившимся.
    const statusTxt = m.status === "green" ? "показатель в пределах целевого диапазона"
      : m.status === "yellow" ? "показатель приближается к нижней границе"
      : m.status === "red" ? "показатель ниже критичного порога"
      : "целевой порог для этого показателя не задан";
    lines.push("Оценка: " + statusTxt + (m.target === null || m.target === undefined
      ? "" : " (целевой порог " + fmtVal(m.target, m.unit) + ")") + ".");
    lines.push("Контекст блока «" + blockName(data, m.block) + "»: " + blockDesc(data, m.block));
    if (m.status === "red" || m.status === "yellow") lines.push("⚠️ Рекомендую детализировать разрез по подразделениям — распределение может быть неравномерным.");

    let source = "Витрина: " + blockName(data, m.block) + " · источник: " + sourceSystem(m.block);
    let drill = [];
    if (dept && m.drill) drill = m.drill[dept.id] || [];
    else if (m.drill) {
      // Раньше здесь искали подразделение с id «ff», которого в структуре
      // уже нет (кафедры — это девять настоящих org_id), поэтому записи
      // детализации не попадали в ответ никогда.
      drill = Object.keys(m.drill).reduce((acc, k) => acc.concat(m.drill[k] || []), []);
    }

    let labels = (ctx && ctx.periodLabels) || DEFAULT_LABELS;
    charts.push(chartSuggestion(m, labels));

    return {
      text: lines.join("\n"),
      source: source,
      drill: drill.slice(0, 5),
      charts: charts,
      blockId: m.block,
      metricId: m.id
    };
  }
// ===== Ollama (локальная LLM, приоритет 1) =====
  const OLLAMA_URL = "http://localhost:11434/api/chat";
  const OLLAMA_MODEL = "qwen2.5:1.5b";

  function dashboardContext(data) {
    // Компактная сводка дашборда для системного промпта
    const lines = data.metrics.map(function (m) {
      return "- " + m.title + " (блок «" + blockName(data, m.block) + "»: " + fmtVal(m.current, m.unit) +
        ", динамика " + (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%, статус: " + m.status +
        ", цель: " + fmtVal(m.target, m.unit) + ")";
    });
    return lines.join("\n");
  }

  async function answerOllama(question, data, ctx) {
    // Быстрая проверка доступности сервера (иначе сразу fallback)
    let ok = false;
    try {
      const ping = await fetch("http://localhost:11434/api/tags", { method: "GET" });
      ok = ping.ok;
    } catch (e) { ok = false; }
    if (!ok) return null;

    const system = "Ты — «Аналитик ректора» Южного университета (ИУБиП). Ты работаешь только с данными, " +
      "которые даны в сообщении пользователя. Никогда не отказывайся отвечать и не советуй " +
      "обратиться к кому-либо. Отвечай на русском, кратко (3-5 предложений), начиная с цифр из данных. " +
      "Указывай динамику и статус показателей, при отклонениях от цели давай рекомендацию, какой график построить.";

    const metricsCtx = "ДАННЫЕ ДАШБОРДА:\n" + dashboardContext(data);
    const body = {
      model: OLLAMA_MODEL,
      stream: false,
      messages: [
        { role: "system", content: system },
        { role: "user", content: metricsCtx + "\n\nВОПРОС: " + question + "\n\nОТВЕТ (строго на основе данных выше):" }
      ],
      options: { temperature: 0.3, num_predict: 250 }
    };
    const resp = await fetch(OLLAMA_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!resp.ok) throw new Error("Ollama: HTTP " + resp.status);
    const json = await resp.json();
    const content = json && json.message && json.message.content;
    if (!content) throw new Error("Ollama: пустой ответ");
    return {
      text: content.trim(),
      source: "Ollama · " + OLLAMA_MODEL + " (локальная LLM)",
      charts: [],
      ollama: true
    };
  }

// ===== DeepSeek API (опционально) =====
  const DEEPSEEK_URL = "https://api.deepseek.com/chat/completions";

  async function answerDeepSeek(question, data, ctx) {
    const apiKey = global.localStorage && global.localStorage.getItem("dash_ai_key");
    if (!apiKey) return null;
    const semantLayer = data.metrics.map((m) => m.title + " (блок " + blockName(data, m.block) + ")").slice(0, 40).join("; ");
    const body = {
      model: "deepseek-chat",
      messages: [
        { role: "system", content: "Ты — «Аналитик ректора» Южного университета (ИУБиП). Отвечай на русском, опираясь только на данные. Доступные метрики дашборда: " + semantLayer + ". Давай цифры и предложи, какой график построить. Не выдумывай данные." },
        { role: "user", content: question }
      ],
      temperature: 0.4,
      max_tokens: 500
    };
    const resp = await fetch(DEEPSEEK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + apiKey },
      body: JSON.stringify(body)
    });
    if (!resp.ok) throw new Error("DeepSeek: " + resp.status);
    const json = await resp.json();
    const content = json.choices && json.choices[0] && json.choices[0].message ? json.choices[0].message.content : null;
    if (!content) throw new Error("DeepSeek: пустой ответ");
    return { text: content, source: "DeepSeek (глубокая генеративная аналитика)", charts: [], deepseek: true };
  }

  // ===== RAG через локальный сервер =====
  // Основной режим. Сервер ищет по документам (слова + смысл),
  // формирует ответ языковой моделью и возвращает список источников.
  // Режима «экипаж или один агент» больше нет: число агентов решает
  // сервер по типу вопроса (backend/crew.py, plan_agents). Переключатель
  // в интерфейсе стоял, но обещал неправду — «Один агент: без роутера и
  // критики», хотя роутер и аналитик витрины работали в обоих режимах.
  async function askServer(question, signal) {
    const api = global.DashboardAPI;
    if (!api) return null;
    var out = await api.askCrew(question, signal);
    if (!out || typeof out.answer !== "string") return null;

    // В ответе без модели сервер сам объясняет, что не смог ответить,
    // и показывает найденные документы. Причину показываем честно —
    // иначе "аналитик молчит" выглядит как зависание.
    {
      // Экипаж возвращает больше полей, чем одиночный путь, и они
      // показываются пользователю: без этого «проверено критиком»
      // ничем не отличается от «никто не проверял».
      var parts = [];
      parts.push("Агент · " + (out.agent_name || out.agent_id));
      if (out.agents && out.agents.length) {
        parts.push("агентов: " + out.agents.join(", "));
      }
      if (out.critic && out.critic.ran && !out.critic.ok) {
        parts.push("критик нашёл замечание");
      } else if (out.critic && out.critic.ran) {
        parts.push("критик подтвердил");
      } else if (out.critic) {
        parts.push("критик не запускался");
      }
      parts.push(Math.round((out.latency_ms || 0) / 100) / 10 + " сек");
      return {
        text: out.answer,
        source: parts.join(" · "),
        sources: out.sources || [],
        charts: [],
        demo: !!out.demo_notice,
        server: true,
        crew: out,
        meta: out.meta || {}
      };
    }
  }

  // Публичный API. signal — сигнал отмены из интерфейса: без него
  // остановить долгий запрос экипажу можно было только закрыв вкладку.
  async function ask(question, data, ctx, signal) {
    // Приоритет 0: локальный сервер с корпусом документов (RAG).
    // Если сервер поднят, он и источник истины, и генератор ответа:
    // документы вуза полезнее демо-цифр, которые всё равно выдуманы.
    const api = global.DashboardAPI;
    if (api) {
      try {
        const srv = await askServer(question, signal);
        if (srv) {
          const loc = matchMetrics(question, data);
          if (loc.length) srv.charts.push(
            chartSuggestion(loc[0], (ctx && ctx.periodLabels) || DEFAULT_LABELS));
          return srv;
        }
      } catch (e) {
        // Отмена — не ошибка: пробрасываем наверх, иначе вместо
        // «остановлено» человек увидит запасной ответ с эмодзи ошибки.
        if (e && e.name === "AbortError") throw e;
        return {
          text: "⚠️ Локальный сервер ответил с ошибкой (" + e.message + ")." +
            "\n\nНиже — встроенная аналитика по демо-данным.\n\n" +
            answerLocal(question, data, ctx).text,
          source: "запасной режим (встроенная аналитика)",
          charts: answerLocal(question, data, ctx).charts
        };
      }
    }
    // Приоритет 1: локальная Ollama (qwen)
    try {
      const ol = await answerOllama(question, data, ctx);
      if (ol) {
        const locOl = matchMetrics(question, data);
        if (locOl.length) ol.charts.push(chartSuggestion(locOl[0], (ctx && ctx.periodLabels) || DEFAULT_LABELS));
        return ol;
      }
    } catch (e) {
      return { text: "⚠️ Не удалось получить ответ от локальной LLM (" + e.message + "). Убедитесь, что Ollama запущена с OLLAMA_ORIGINS=* (запустите start.bat). Ниже — встроенная аналитика.\n\n" + answerLocal(question, data, ctx).text, source: "запасной режим (встроенная аналитика)", charts: answerLocal(question, data, ctx).charts };
    }
    // Приоритет 2: DeepSeek API (если задан ключ)
    try {
      const ds = await answerDeepSeek(question, data, ctx);
      if (ds) {
        const loc = matchMetrics(question, data);
        if (loc.length) ds.charts.push(chartSuggestion(loc[0], (ctx && ctx.periodLabels) || DEFAULT_LABELS));
        return ds;
      }
    } catch (e) {
      return { text: "⚠️ Не удалось получить ответ от DeepSeek (" + e.message + "). Использую встроенную аналитику.", source: "запасной режим", charts: [] };
    }
    // Приоритет 3: встроенная аналитика
    return answerLocal(question, data, ctx);
  }

  global.AIAssistant = {
    SYNONYM_MAP, matchMetrics, blockName, chartSuggestion, fmtVal,
    answerLocal, answerOllama, answerDeepSeek, askServer, ask
  };
})(window);