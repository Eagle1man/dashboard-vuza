# REAL01_REPAIR_REPORT — Дашборд ректора ИУБиП

Дата: 2026-10-29  
Рабочий каталог: `C:\Users\diorl\.cline\data\workspaces\chat\rector-dashboard`  
Ветка: `master`

---

## 1. Обнаруженные и устранённые дефекты (P0 / P1)

| # | Узел / Файл | Первопричина дефекта | Внесённое исправление |
|---|---|---|---|
| **P0-1** | `backend/ingest.py` (`index_file`, `_delete_doc`) | При `PRAGMA foreign_keys = ON` (включён в `db.connect()`) переиндексация существующего документа (`force=True` или обновление файла) вызывала `DELETE FROM doc WHERE doc_id = ?` и вставляла новую строку с новым `doc_id`. Каскадные ключи уничтожали анкету `doc_meta` (`ON DELETE CASCADE`), связи `metric_source` (`ON DELETE CASCADE`) и обнуляли `upload.doc_id` и `fact_provenance.source_doc_id` (`ON DELETE SET NULL`). | Выделена функция `_clear_doc_index(conn, doc_id)`, очищающая только индексные таблицы (`doc_block`, `doc_page`, `doc_chunk`, `chunk_fts`, `chunk_vec`). Строка `doc` обновляется через `UPDATE` с сохранением `doc_id`. Добавлен регрессионный тест `test_reindex_preserves_doc_meta_and_upload_link`. |
| **P0-2** | `backend/intake.py` (`_extract_json`) | 1) Модели 7B повторяют JSON-ответ несколько раз подряд (`{...} {...}`), а старый срез `find("{")..rfind("}")` склеивал их в невалидный JSON. 2) На многострочных отчётах модель 1.5B при превышении `num_predict` обрывала массив `values` до закрывающей скобки `]}`, из-за чего терялись все уже распознанные показатели. | Реализован парсер первого сбалансированного объекта `{...}` с учётом строковых литералов и экранирования + сохранение всех полностью закрытых элементов `{...}` внутри массива `values` при обрыве хвоста (`last_item_end` на переходе глубины `2 -> 1`). Добавлен тест `test_extract_json_tolerates_repeats`. |
| **P0-3** | `backend/intake.py` (`_sample_rows`, `_prompt_user`) | Для таблиц XLSX в промпт модели отправлялись и строки `kind="row"`, и дублирующие их голые ячейки `kind="cell"`, а также весь каталог из 47 метрик без приоритизации выбранных в анкете `metric_ids`. Модель 1.5B пыталась вывести объекты для всех 47 показателей и упиралась в лимит токенов (2437 симв., `parseError`). | При наличии табличных строк `kind="row"` голые ячейки `kind="cell"` не дублируются в промпт (оставаясь в `upload_raw` для провенанса); метрики из анкеты ставятся в начало списка; добавлено явное правило выводить только числа, реально присутствующие в строках сырья. Время разбора 10-строчного отчёта сократилось со 135 с (ошибка) до **58,5 с (успех с 1-й попытки)**. |
| **P0-4** | Конфигурация Ollama (`vikhr-qd-a:latest` / `vikhr-7b:latest`) и `backend/consistency.py` | В Modelfile `vikhr-qd-a:latest` (`Vikhr-7B-instruct_0.2.Q4_K_M.gguf`) стоял `PARAMETER stop "</s>"` и шаблон без русского зачина ответа, из-за чего `/api/chat` завершался на 1-м токене (`eval_count: 1`, `response_chars: 0`, «модель вернула пустой ответ»). | Через `/api/create` без перекачки весов (переиспользован блоб `sha256:a37244f1...`, 4,24 ГБ) исправлены манифесты `vikhr-qd-a:latest` и `vikhr-7b:latest`; в `consistency.py` лимит `num_predict` установлен в `280` токенов. Генерация объяснения стабильно возвращает осмысленный русский текст (`ok=1`). |

---

## 2. Регрессионные тесты и результаты полного прогона `pytest`

В `backend/tests/test_pipeline.py` добавлены:
- `test_extract_json_tolerates_repeats` — проверка одиночного JSON, дублированного ответа `{...} {...}`, markdown-блока, фигурных скобок внутри строк, восстановления массива `values` при обрыве и отклонения текста без JSON;
- `test_reindex_preserves_doc_meta_and_upload_link` — проверка сохранности `doc_id`, `doc_meta`, `upload.doc_id` и `metric_source` после повторной индексации `index_file(..., force=True)`.

Фактический вывод полного набора тестов:
```text
$env:PYTHONIOENCODING="utf-8"
.\.venv\Scripts\python.exe -m pytest backend/tests/ -q --tb=line
..................................................                       [100%]
50 passed in 66.31s (0:01:06)
EXIT_CODE=0
```

---

## 3. Конфигурация реальной LLM и состояние `/api/health`

Локальный сервер Ollama (`http://127.0.0.1:11434`) и бэкенд (`http://127.0.0.1:8765`) работают без mock-заглушек:
- **Роль `small` (разбор документов и RAG-ассистент):** `qwen2.5:1.5b` (`installed: true`, `resolved: "meta"`, `fallback: false`)
- **Роль `large` (объяснение проверок на экране сверки):** `vikhr-qd-a:latest` (`Vikhr-7B-instruct_0.2.Q4_K_M.gguf`, 7.0B Q4_K_M, `installed: true`, `resolved: "meta"`, `fallback: false`)
- **Модель эмбеддингов (`embed_model`):** `nomic-embed-text:latest` (768 dim; в `chunk_vec` 578 векторов, единый бэкенд `ollama:nomic-embed-text`)

---

## 4. Слепой сквозной прогон REAL-01 (`real01_blind_run.py`)

Прогон выполнен на изолированной копии боевой БД (`127.0.0.1:8767`) скриптом `C:\Users\diorl\AppData\Local\Temp\opencode\real01_blind_run.py`.  
Артефакт сохранён в: `C:\Users\diorl\AppData\Local\Temp\opencode\real01_rector_result.json`.

- **Входной файл:** `real01_svodka_iubip_2026_03.xlsx` (10 показателей ИУБиП за период `2026-03`, `raw_rows = 20`).
- **Слепой режим:** поле `metric_values` в `POST /api/upload` передано пустым (`"{}"`) — все числа извлекались моделью непосредственно из файла.
- **Тайминги и идентификаторы:**
  - `upload_id`: `5`, статус после приёма: `needs_review` (витрина не изменена до решения человека);
  - Время приёма и LLM-разбора (`POST /api/upload`): **60,68 с** (`parseMs = 58458`, `attempts = 1`, `parseError = null`);
  - Время генерации объяснения 7B-моделью (`POST /api/upload/5/explain`, `vikhr-qd-a:latest`): **109,81 с** (`available = true`);
  - Время повторного чтения из кэша (`GET /api/upload/5/review`): **283,2 мс** (`cached_available = true`);
  - Время транзакции публикации (`POST /api/upload/5/decide`, `action="accept"`): **0,06 с** (`status = "accepted"`, `published = 8`, `periodId = "2026-03"`).

### Выдержка из `llm_trace`:
```json
[
  {
    "id": 1,
    "ts": "2026-10-29 17:12:20",
    "purpose": "parse_card",
    "role": "small",
    "model": "qwen2.5:1.5b",
    "resolved": "meta",
    "duration_ms": 58459,
    "ok": 1,
    "prompt_chars": 2972,
    "response_chars": 759
  },
  {
    "id": 2,
    "ts": "2026-10-29 17:14:10",
    "purpose": "consistency",
    "role": "large",
    "model": "vikhr-qd-a:latest",
    "resolved": "meta",
    "duration_ms": 109680,
    "ok": 1,
    "prompt_chars": 2147,
    "response_chars": 279
  }
]
```

### Текст объяснения от 7B-модели (`vikhr-qd-a:latest`):
> «в файле .xlsx отсутствует информация о действующих образовательных программах и трудоустройстве выпускников. Это может привести к тому, что данные будут считаться недостоверными при проверке соответствия показателей вуза показателям мониторинга эффективности деятельности вузов.»

---

## 5. Поэлементное сравнение с эталонным документом REAL-01

| Показатель (эталон) | Код `metric_id` | В документе | Извлечено моделью (`modelValue`) | В `fact_metric` (`value_exact`) | Провенанс (`rawRef`, метод, модель) | Оценка |
|---|---|---:|---:|---:|---|---|
| Контингент обучающихся | `continuer-total` | `2946` | `2946` | `2946` | `rawRef=1`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Контингент СПО | `continuer-spo` | `780` | `780` | `780` | `rawRef=3`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Контингент бакалавриата | `continuer-ba` | `1988` | `1988` | `1988` | `rawRef=5`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Контингент магистратуры | `continuer-ma` | `146` | `146` | `146` | `rawRef=7`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Аспиранты | `continuer-aspir` | `32` | `32` | `32` | `rawRef=9`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Принято на 1 курс (2025) | `applicant-2025` | `412` | `412` | `412` | `rawRef=11`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Выпуск 2025 | `graduate-2025` | `505` | `505` | `505` | `rawRef=13`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Иностранцы | `foreign` | `64` | `64` | `64` | `rawRef=15`, `xlsx_row`, `small:qwen2.5:1.5b` | **СОВПАЛО** |
| Действующие образовательные программы | `programs` | `38` | `""` (пропуск 1.5B) | `NULL` (не опубликовано) | Поймано проверкой `value_missing` (`warning`) | **ЧЕСТНЫЙ ПРОПУСК (отмечен проверкой)** |
| Трудоустройство выпускников | `trudoustroystvo` | `91.2` | `""` (пропуск 1.5B) | `NULL` (не опубликовано) | Поймано проверкой `value_missing` (`warning`) | **ЧЕСТНЫЙ ПРОПУСК (отмечен проверкой)** |

**Анализ точности и безопасности:**
- **0 выдуманных чисел (галлюцинаций):** все 8 извлечённых значений совпадают с первоисточником до последнего знака и привязаны к точным строкам сырья (`rawRef = 1..15`).
- **Честная обработка пропусков малой модели:** 2 показателя, которые модель 1.5B не включила в JSON, детерминированный движок `checks.py` пометил предупреждением `value_missing`, а большая модель `vikhr-qd-a:latest` (7B) прямо указала проверяющему на их отсутствие. В витрину пустые/неподтверждённые числа не попали.

---

## 6. Проверка пользовательских сценариев (UI / API) и безопасности на `127.0.0.1:8765`

| Проверка | Фактический результат |
|---|---|
| Страницы UI (`/`, `/dashboard.html`, `/upload.html`, `/uploads.html`, `/review.html?id=3`, `/journal.html`) | Все отвечают `200 OK` |
| Статус ролей `GET /api/health` | `200 OK`, `small: qwen2.5:1.5b (installed: true)`, `large: vikhr-qd-a:latest (installed: true)`, локальные пути диска скрыты |
| Защита сессии (`GET /api/upload/3/review` без cookie) | `401 Unauthorized` |
| Защита CSRF (`POST /api/upload/3/decide` с `Origin: http://evil.example.com`) | `403 Forbidden` |
| RAG-ассистент (`POST /api/ask`) | `200 OK`, 99,9 с, 6 источников из корпуса ВНОКО, ответ на русском языке |

---

## 7. Изменённые файлы и команды для воспроизведения

### Изменённые файлы (`git diff --stat`):
- `backend/ingest.py` — сохранение `doc_id` и внешних связей (`doc_meta`, `upload.doc_id`, `metric_source`) при переиндексации;
- `backend/intake.py` — фильтрация дублирующих ячеек в промпте, приоритизация `metric_ids`, устойчивый разбор JSON `_extract_json` (повторы 7B + восстановление массива `values` при обрыве);
- `backend/consistency.py` — оптимизация `num_predict=280` для большой модели на CPU;
- `backend/tests/test_pipeline.py` — регрессионные тесты `test_extract_json_tolerates_repeats` и `test_reindex_preserves_doc_meta_and_upload_link`.

### Команды для повторения проверки:
```powershell
cd C:\Users\diorl\.cline\data\workspaces\chat\rector-dashboard
$env:PYTHONIOENCODING="utf-8"

# 1. Полный набор unit и контрактных тестов
.\.venv\Scripts\python.exe -m pytest backend/tests/ -q --tb=line

# 2. Слепой сквозной прогон REAL-01 на копии БД с реальной Ollama
.\.venv\Scripts\python.exe C:\Users\diorl\AppData\Local\Temp\opencode\real01_blind_run.py
```
