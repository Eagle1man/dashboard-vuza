#!/usr/bin/env bash
# Запуск дашборда ректора на Linux (без службы).
#
# Для боевого режима используется systemd — см. deploy/systemd/README.md.
# Этот скрипт нужен для ручной проверки и для запуска в консоли.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Окружение не установлено. Запустите ./install.sh"
  exit 1
fi

OLLAMA_URL="${RECTOR_OLLAMA_URL:-http://127.0.0.1:11434}"
if ! curl -fsS --max-time 3 "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  echo "Ollama не отвечает на $OLLAMA_URL."
  echo "  sudo systemctl enable --now ollama"
  echo "Дашборд всё равно запустится: ассистент будет показывать найденные"
  echo "документы без сводного ответа."
fi

.venv/bin/python -m backend.modelcheck || true

echo
echo "Сервер: http://${RECTOR_API_HOST:-127.0.0.1}:${RECTOR_API_PORT:-8765}/"
echo "Остановка — Ctrl+C."
echo
exec .venv/bin/python -m backend.app
