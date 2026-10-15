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
    if (v >= 1000000) s = (v / 1000000).toFixed(2) + " М";
    else if (v >= 10000) s = (v / 1000).toFixed(1) + " К";
    else s = parseFloat(v.toFixed(2)).toString();
    return s + (unit ? " " + unit : "");
  }

  function blockDesc(data, blockId) {
    const b = data.blocks.find((x) => x.id === blockId);
    return b ? b.desc : "";
  }

  function sourceSystem(blockId) {
    const map = {
      academic: "Moodle, 1С:Университет",
      results: "Moodle, 1С:Университет",
      finance: "1С:Бухгалтерия / ERP",
      employ: "База карьерного центра",
      science: "Научная часть, Scopus/WoS",
      ops: "Учётная система деканатов",
      reputation: "LimeSurvey, Медиалогия"
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
      lines.push("• " + m.title + " — " + fmtVal(cur, m.unit) + " (" + (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%)");
      if (m.status === "green") green++;
      else if (m.status === "yellow") yellow++;
      else red++;
    });
    lines.push("\nСтатус блока: " + green + " зелёных, " + yellow + " жёлтых, " + red + " красных показателей.");
    if (red > 0) lines.push("⚠️ Требуют внимания (красный): " + mets.filter((m) => m.status === "red").map((m) => m.title).join(", ") + ".");
    charts.push(chartSuggestion(mets[0], DEFAULT_LABELS));
    let drill = [];
    const dd = dept ? (mets[0].drill && mets[0].drill[dept.id]) : (mets[0].drill && mets[0].drill["ff"]);
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
      const blockAlias = { "финанс": "finance", "приём": "academic", "контингент": "academic", "академич": "results", "трудоустро": "employ", "исследова": "science", "операцион": "ops", "репута": "reputation", "студенческий опыт": "reputation" };
      Object.keys(blockAlias).forEach((k) => { if (q.indexOf(k) !== -1) blockId = blockAlias[k]; });
      if (!blockId) blockId = ctx.currentBlockId || "academic";
      return reportByBlock(question, data, blockId, dept, charts);
    }

    if (matched.length === 0) {
      return {
        text: "Не удалось однозначно сопоставить вопрос с метриками дашборда. Уточните тему: приём и контингент, успеваемость, финансы, трудоустройство, наука, операционная эффективность, репутация. Например: «Почему выросло отчисление на 1 курсе?»",
        source: "Семантический слой метрик",
        charts: []
      };
    }

    const m = matched[0];
    let lines = [];
    const label = dept ? dept.name : "в целом по университету";
    const cur = dept && m.dept ? (m.dept.find((d) => d.deptId === dept.id) ? m.dept.find((d) => d.deptId === dept.id).value : m.current) : m.current;

    lines.push("• **" + m.title + "** (" + label + "): текущее значение **" + fmtVal(cur, m.unit) + "**, изменение к прошлому периоду **" + (m.delta >= 0 ? "+" : "") + m.delta.toFixed(1) + "%**.");

    if (dept && m.current) {
      const diff = ((cur - m.current) / m.current) * 100;
      lines.push("Это " + (diff >= 0 ? "выше" : "ниже") + " среднего по университету (" + fmtVal(m.current, m.unit) + ") на " + Math.abs(diff).toFixed(1) + "%.");
    }

    const statusTxt = m.status === "green" ? "показатель в пределах целевого диапазона" : m.status === "yellow" ? "показатель приближается к нижней границе" : "показатель ниже критичного порога";
    lines.push("Оценка: " + statusTxt + " (целевой порог " + fmtVal(m.target, m.unit) + ").");
    lines.push("Контекст блока «" + blockName(data, m.block) + "»: " + blockDesc(data, m.block));
    if (m.status === "red" || m.status === "yellow") lines.push("⚠️ Рекомендую детализировать разрез по подразделениям — распределение может быть неравномерным.");

    let source = "Витрина: " + blockName(data, m.block) + " · источник: " + sourceSystem(m.block);
    let drill = [];
    if (dept && m.drill) drill = m.drill[dept.id] || [];
    else if (m.drill) drill = data.dept.filter((d) => d.id === "ff").map((d) => m.drill[d.id] || []).reduce((a, b) => a.concat(b), []);

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
  // Режим работы ассистента: "crew" — экипаж агентов, "single" —
  // прежний одиночный путь. Переключается пользователем и хранится.
  function getPipeline() {
    var v = null;
    try { v = global.localStorage.getItem("dash_pipeline"); } catch (e) { v = null; }
    return v === "single" ? "single" : "crew";
  }

  function setPipeline(v) {
    try { global.localStorage.setItem("dash_pipeline", v); } catch (e) { /* режим приватного просмотра */ }
    if (global.DashboardUI && global.DashboardUI.renderPipeline) {
      global.DashboardUI.renderPipeline();
    }
  }

  async function askServer(question) {
    const api = global.DashboardAPI;
    if (!api) return null;
    var crewMode = getPipeline() === "crew";
    var out = crewMode
      ? await api.askCrew(question, "crew")
      : await api.ask(question);
    if (!out || typeof out.answer !== "string") return null;

    // В ответе без модели сервер сам объясняет, что не смог ответить,
    // и показывает найденные документы. Причину показываем честно —
    // иначе "аналитик молчит" выглядит как зависание.
    if (crewMode) {
      // Экипаж возвращает больше полей, чем одиночный путь, и они
      // показываются пользователю: без этого «проверено критиком»
      // ничем не отличается от «никто не проверял».
      var parts = [];
      parts.push("Экипаж · " + (out.agent_name || out.agent_id));
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

    const meta = out.meta || {};
    let source;
    if (meta.llm) {
      source = "Документы университета · " + (meta.model || "локальная модель") +
        " · найдено фрагментов: " + (meta.retrieved || 0);
    } else {
      source = "Документы университета · поиск только по словам" +
        (meta.reason ? " (" + meta.reason + ")" : "");
    }

    return {
      text: out.answer,
      source: source,
      sources: out.sources || [],
      charts: [],
      demo: !!out.demo_notice,
      server: true,
      meta: meta
    };
  }

  // Публичный API
  async function ask(question, data, ctx) {
    // Приоритет 0: локальный сервер с корпусом документов (RAG).
    // Если сервер поднят, он и источник истины, и генератор ответа:
    // документы вуза полезнее демо-цифр, которые всё равно выдуманы.
    const api = global.DashboardAPI;
    if (api) {
      try {
        const srv = await askServer(question);
        if (srv) {
          const loc = matchMetrics(question, data);
          if (loc.length) srv.charts.push(
            chartSuggestion(loc[0], (ctx && ctx.periodLabels) || DEFAULT_LABELS));
          return srv;
        }
      } catch (e) {
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
    answerLocal, answerOllama, answerDeepSeek, askServer, ask,
    getPipeline, setPipeline
  };
})(window);