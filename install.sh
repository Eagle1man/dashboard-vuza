#!/usr/bin/env bash
# Установка дашборда ректора на Linux.
#
# Проверено на Ubuntu 22.04/24.04. Нужен Python 3.10+ и Ollama
# (https://ollama.com/download/linux). Скрипт идемпотентен: повторный
# запуск не ломает уже установленное окружение и не перезаписывает
# пароли подразделений.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python не найден. Установите:"
  echo "  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

"$PY" - <<'PYEOF'
import sys
if sys.version_info < (3, 10):
    print("Нужен Python 3.10 или новее, а стоит", sys.version.split()[0])
    sys.exit(1)
PYEOF

if [ ! -x .venv/bin/python ]; then
  echo "Создаю виртуальное окружение .venv ..."
  "$PY" -m venv .venv
fi

# Установщик в конце ждёт Enter — в скрипте это лишнее ожидание.
export RECTOR_NO_PAUSE=1
.venv/bin/python -m backend.setup

echo
echo "Проверка готовности (сервер ещё не запущен, поэтому без HTTP):"
.venv/bin/python -m backend.smoke --no-server || true

echo
echo "Дальше:"
echo "  1) запустить Ollama:      sudo systemctl enable --now ollama"
echo "  2) скачать модели:        ollama pull qwen2.5:1.5b && ollama pull nomic-embed-text"
echo "  3) запустить дашборд:     ./start.sh"
echo "  4) проверить целиком:     .venv/bin/python -m backend.smoke --ask \"<вопрос по вашим документам>\""
echo
echo "Для сети и автозапуска см. docs/DEPLOY.md"
