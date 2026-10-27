/* ============================================================
   api.js — обмен с локальным сервером дашборда

   Зачем он нужен при том, что интерфейс и раньше работал сам:
   браузер не умеет ни искать по векторам, ни читать SQLite, ни
   обращаться к языковой модели с серверной стороны. Сервер
   делает это локально, на компьютере ректора, без интернета.

   Если сервер недоступен (например, index.html открыли двойным
   кликом), модуль молча отдаёт признак "нет связи" — и дашборд
   продолжает работать на демо-данных. Это осознанно: лучше
   показать цифры без ИИ, чем белый экран.
   ============================================================ */
(function (global) {
  "use strict";

  var BASE = "";   // пустая строка = тот же сервер, что отдал страницу

  function withTimeout(promise, ms, label) {
    return new Promise(function (resolve, reject) {
      var done = false;
      var t = setTimeout(function () {
        if (!done) { done = true; reject(new Error(label + ": превышено время ожидания")); }
      }, ms);
      promise.then(function (v) {
        if (!done) { done = true; clearTimeout(t); resolve(v); }
      }, function (e) {
        if (!done) { done = true; clearTimeout(t); reject(e); }
      });
    });
  }

  function get(path, ms) {
    return withTimeout(
      fetch(BASE + path, { headers: { "Accept": "application/json" } })
        .then(function (r) {
          if (!r.ok) throw new Error(path + ": HTTP " + r.status);
          return r.json();
        }),
      ms || 8000, path);
  }

  function post(path, body, ms, signal) {
    return withTimeout(
      fetch(BASE + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: signal
      }).then(function (r) {
        if (!r.ok) throw new Error(path + ": HTTP " + r.status);
        return r.json();
      }),
      ms || 120000, path);
  }

  var API = {
    // Витрина метрик из базы вместо генератора в браузере
    metrics: function () { return get("/api/metrics", 10000); },

    // Состояние системы: сервер, модель, размер индекса, демо-либо нет
    health: function () { return get("/api/health", 6000); },

    // Список проиндексированных документов
    docs: function () { return get("/api/docs", 8000); },

    // Переиндексация папки knowledge (долгая — свой таймаут)
    reindex: function () { return post("/api/reindex?prune=true", {}, 600000); },

    // Вопрос ИИ-ассистенту (одиночный путь, без агентов)
    ask: function (question, topK) {
      return post("/api/ask", { question: question, top_k: topK || 0 }, 180000);
    },

    // Вопрос экипажу агентов. Режим не передаётся: число агентов
    // сервер выбирает сам по типу вопроса (crew.plan_agents). Таймаут
    // заведомо большой: цикл — роутер, специалист и, когда он нужен,
    // критик, то есть до четырёх обращений к локальной модели подряд.
    askCrew: function (question, signal) {
      return post("/api/ask/crew",
        { question: question, mode: "auto" }, 600000, signal);
    },

    // Состав экипажа: кто есть, за что отвечает, сколько документов видит
    agents: function () {
      return get("/api/agents", 10000);
    },

    // Однократная проверка связи. Дёшево: если сервера нет, страницу
    // не надо ждать по таймауту на каждом действии.
    online: function () {
      return get("/api/health", 2500).then(function () { return true; },
        function () { return false; });
    }
  };

  global.DashboardAPI = API;
})(window);