# Точка сохранения: QLoRA v2 (ожидание Kaggle)

**Дата:** 2026-11-08 (создано при запуске v2)  
**Ветка:** `work/kaggle-qlora` (локально, без push)  
**Последний коммит v2:** `b379850` `QLoRA v2: 30% отказов в train, r=8, lr=1e-4`

## Что сделано

- Датасет v2: **1551** пара (train **1130**, dev **186**, test **235**), 0 отброшенных.
  - Отказы в train: **343 (30,4%)** (v1 было 42 / 5,1%). Типы: год не тот при реальном фрагменте (2024↔2025↔2026↔2027) по карточке/направлениям/E/СПО; чужой вуз в контексте → вопрос про ИУБиП; показатель не в фрагменте; out-of-scope.
  - Отказы в test: **35**.
- Ноутбук v2: `r=8`, `alpha=16`, `lr=1e-4`, `warmup_steps=5`, 1 эпоха, `MAX_SEQ=1280`, замер семплирует 16 отказов.
- Kaggle датасет: `orel1986/monitoring-qa` обновлён (v2, `ready`).
- Kaggle ядро: <https://www.kaggle.com/code/orel1986/rector-qlora-qwen2-5-14b>, **version 5, RUNNING** (T4, internet on). Ориентир ~90–110 мин.
- Тесты: `backend/tests` — **121 passed** на HEAD v2.
- Фоновый монитор в сессии: shell `sh_1b87ab06c002mIbPETKV8ySEFg` опрашивает статус каждые 5 мин (до 150 мин). При завершении придёт уведомление с выводом.

## Что сделать при `COMPLETE`

1. Скачать артефакты:
   ```powershell
   $env:KAGGLE_API_TOKEN = Get-Content "$env:USERPROFILE\.kaggle\access_token" -Raw
   $env:PYTHONIOENCODING='utf-8'
   New-Item -ItemType Directory -Path D:\kaggle-runs\run5 -Force | Out-Null
   kaggle kernels output orel1986/rector-qlora-qwen2-5-14b -p D:\kaggle-runs\run5 --file-pattern "lora_adapter.zip|eval_report.json|.*\.log"
   ```
2. Оценить `D:\kaggle-runs\run5\eval_report.json`: сравнить `adapter` vs `base`.
3. **Приёмка (обязательно, без исключений):**
   - `refusal_correct`: вырос (или не хуже базы)
   - `citation_present`: вырос / не упал
   - `numeric_exact`: не упал > 2 п.п.
   - `hallucinated_number_rate` / `false_refusal_rate`: не выросли
4. Если **прошёл**: зафиксировать метрики в `docs/kaggle/RESULTS.md` (секция v2), обновить `docs/kaggle/README.md`, при необходимости подготовить GGUF/Modelfile (локально, диск ~40 ГБ; на C:/D: места мало — осторожно). **Не пушить/не публиковать без явного запроса.**
5. Если **не прошёл**: записать негативный результат в `RESULTS.md`, адаптер **не подключать**. Предложить v3 (ещё больше целевых отказов, возможно другой баланс/2 эпохи на сбалансированных данных) или остаться на базе+RAG+точном lookup.

## Ссылка на v1 (отклонён)

- `D:\kaggle-runs\run4\lora_adapter.zip` (SHA256 `DE284A0A...F39D0849`), `eval_report.json`, лог.
- Провал: `refusal_correct` 0,857 (база) → 0,000 (адаптер). Причина: 5% отказов в train. См. `docs/kaggle/RESULTS.md`.

## Если `ERROR` / таймаут монитора

- Проверить: `kaggle kernels status orel1986/rector-qlora-qwen2-5-14b`
- Логи: `kaggle kernels output ... --file-pattern ".*\.log"` в `D:\kaggle-runs\run5`
- Типичные проблемы v1/v3-фиксов уже закрыты: проверка отказов без `[1]`, `warmup_steps` (не `warmup_ratio`), `MAX_SEQ=1280`, GGUF `rglob`.

## Ограничения

- IT-панель была недоступна (`localhost:3000`) — заявка не оформлялась, по решению пользователя работали без тикета. Публикация/push/отзыв ключей — только человек.
- Локально 14B не ставим (C: 0,6 ГБ, D: 10,2 ГБ). Храним только адаптер/отчёты.
