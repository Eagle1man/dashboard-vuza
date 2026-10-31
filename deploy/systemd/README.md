# Служба systemd: автозапуск и перезапуск

Файл `rector-dashboard.service` — шаблон. В нём три места, которые
надо подставить под свою машину: `User`, `Group` и `WorkingDirectory`
(а также путь в `ExecStart`). Если ставили в `/opt/rector-dashboard`
под пользователя `rector` — менять ничего не нужно.

## Установка

```bash
sudo useradd -r -m -d /opt/rector-dashboard rector   # если пользователя ещё нет
sudo chown -R rector:rector /opt/rector-dashboard

sudo cp deploy/systemd/rector-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rector-dashboard
```

Проверка:

```bash
systemctl status rector-dashboard
journalctl -u rector-dashboard -n 50 --no-pager
curl -s http://127.0.0.1:8765/api/health
```

## Что задано в шаблоне и почему

| Переменная | Значение | Зачем |
|---|---|---|
| `RECTOR_API_HOST` | `0.0.0.0` | доступ с других машин вуза; `127.0.0.1` — только локально |
| `RECTOR_REQUIRE_LOGIN` | `1` | порт в сети: витрина, документы и чат только после входа |
| `RECTOR_OLLAMA_TIMEOUT` | `600` | 7B на процессоре отвечает минутами, 300 с не хватает |
| `Restart=always` | — | сервер поднимается после падения, не после перезагрузки только |

## Обновление кода

```bash
sudo systemctl stop rector-dashboard
git -C /opt/rector-dashboard pull
sudo -u rector /opt/rector-dashboard/.venv/bin/python -m backend.ingest --prune
sudo systemctl start rector-dashboard
```

Индекс пересчитывать при обычном обновлении кода не нужно — только если
менялись документы в `knowledge/` или модель эмбеддингов.
