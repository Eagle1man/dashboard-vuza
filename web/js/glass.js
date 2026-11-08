/* glass.js — переключение оформления «матовое стекло / классика».
 *
 * Класс glass на <html> включает glass.css. Выбор запоминается в
 * localStorage; кнопки с атрибутом data-glass-toggle переключают вид.
 * Удаление класса glass (или кнопка «Классика») возвращает исходное
 * оформление — это и есть безопасный откат.
 */
(function () {
  "use strict";

  var KEY = "dashboard_glass";
  var root = document.documentElement;
  var enabled = true;
  try {
    enabled = localStorage.getItem(KEY) !== "0";
  } catch (e) {}

  root.classList.toggle("glass", enabled);

  function paint() {
    var on = root.classList.contains("glass");
    var buttons = document.querySelectorAll("[data-glass-toggle]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].textContent = on ? "Классика" : "Стекло";
      buttons[i].setAttribute("aria-pressed", on ? "true" : "false");
      buttons[i].title = on
        ? "Вернуть классическое оформление"
        : "Включить оформление «матовое стекло»";
    }
  }

  function toggle() {
    var next = !root.classList.contains("glass");
    root.classList.toggle("glass", next);
    try {
      localStorage.setItem(KEY, next ? "1" : "0");
    } catch (e) {}
    paint();
  }

  function ready() {
    paint();
    var buttons = document.querySelectorAll("[data-glass-toggle]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", toggle);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ready);
  } else {
    ready();
  }
})();
