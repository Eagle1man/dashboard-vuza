/* static-api.js — ответы на /api/... из вшитого снимка.
 *
 * Зачем: страницы дашборда написаны под сервер и зовут /api/metrics,
 * /api/monitoring и другие адреса. На GitHub Pages сервера нет, и без
 * перехвата каждый такой вызов давал бы 404, а экран — пустоту.
 *
 * Шим подменяет window.fetch только для путей /api/ и отдаёт данные из
 * static-data.js. Всё остальное уходит в настоящий fetch.
 */
(function () {
  "use strict";

  var DATA = window.__STATIC_API__ || {};
  var realFetch = window.fetch ? window.fetch.bind(window) : null;

  function jsonResponse(body, status) {
    return new Response(JSON.stringify(body), {
      status: status || 200,
      headers: { "Content-Type": "application/json; charset=utf-8" }
    });
  }

  function pathOf(url) {
    var q = url.indexOf("?");
    var path = q >= 0 ? url.slice(0, q) : url;
    var m = path.match(/^https?:\/\/[^/]+(\/.*)?$/);
    if (m) path = m[1] || "/";
    return path;
  }

  window.fetch = function (input, init) {
    var url = typeof input === "string" ? input : (input && input.url) || "";
    var path = pathOf(url);

    if (path.indexOf("/api/") !== 0) {
      if (realFetch) return realFetch(input, init);
      return Promise.reject(new Error("fetch недоступен"));
    }

    var method = String(
      (init && init.method) || (input && input.method) || "GET"
    ).toUpperCase();

    var body;
    if (DATA[method + " " + path] !== undefined) {
      body = DATA[method + " " + path];
    } else if (method === "GET" && DATA[path] !== undefined) {
      body = DATA[path];
    } else {
      var src = path.match(/^\/api\/metrics\/([^/]+)\/sources$/);
      var id = src ? decodeURIComponent(src[1]) : null;
      if (id && DATA.sources && DATA.sources[id]) {
        body = DATA.sources[id];
      }
    }

    if (body === undefined) {
      return Promise.resolve(jsonResponse({
        detail: "Статическая витрина: этот раздел доступен только в полной версии"
      }, 404));
    }
    return Promise.resolve(jsonResponse(body, 200));
  };
})();
