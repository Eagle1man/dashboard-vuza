#!/usr/bin/env bash
# Загрузка модели Ollama в обход зависания на мелких слоях.
#
# Симптом: `ollama pull` показывает 100% большого блоба и висит.
# В журнале ollama повторяется «part 0 stalled; retrying», `ollama list`
# модель не показывает, манифест не пишется. Причина — не размер, а
# конкретный слой (шаблон в 13 байт, лицензия, параметры): этот слой
# качается через редирект и на некоторых сетях не доходит.
#
# Что делает скрипт: берёт манифест из реестра, докачивает недостающие
# слои обычным curl (он по тому же адресу качает без проблем) прямо в
# каталог блобов с правильными именами, затем просит Ollama записать
# манифест. Повторный запуск безопасен: готовые слои пропускаются.
#
#   sudo ./deploy/linux/fetch-model.sh qwen2.5:14b
set -uo pipefail

MODEL="${1:-}"
if [ -z "$MODEL" ]; then
  echo "Укажите модель: sudo $0 qwen2.5:14b"
  exit 2
fi
if [ "$(id -u)" != "0" ]; then
  echo "Нужны права root: скрипт пишет в каталог блобов Ollama."
  exit 2
fi

NAME="${MODEL%%:*}"
TAG="${MODEL#*:}"
if [ "$TAG" = "$NAME" ]; then TAG="latest"; fi
BLOBS=/usr/share/ollama/.ollama/models/blobs
REG="https://registry.ollama.ai/v2/library"

echo "Модель $NAME:$TAG, каталог блобов $BLOBS"

# Зависший клиент держит загрузку и мешает записать манифест.
pkill -f "ollama pull" 2>/dev/null
sleep 2
rm -f "$BLOBS"/*-partial

python3 - "$NAME" "$TAG" <<'PY'
import json
import os
import subprocess
import sys
import urllib.request

name, tag = sys.argv[1], sys.argv[2]
blobs = "/usr/share/ollama/.ollama/models/blobs"
reg = "https://registry.ollama.ai/v2/library"

manifest = json.load(urllib.request.urlopen(
    f"{reg}/{name}/manifests/{tag}", timeout=60))
items = [manifest["config"]] + list(manifest["layers"])
print(f"слоёв в манифесте: {len(items)}")

for item in items:
    digest = item["digest"].split(":", 1)[1]
    size = item.get("size", 0)
    path = os.path.join(blobs, f"sha256-{digest}")
    have = os.path.getsize(path) if os.path.exists(path) else -1
    if have == size:
        print(f"  {digest[:12]} уже есть ({size} б)")
        continue
    print(f"  {digest[:12]} качаем ({size} б), было {have}")
    subprocess.run([
        "curl", "-sSL", "--max-time", "1800", "-o", path,
        f"{reg}/{name}/blobs/sha256:{digest}"], check=True)
    got = os.path.getsize(path)
    if got != size:
        print(f"    размер не совпал: {got} вместо {size}")
        sys.exit(2)

subprocess.run(["chown", "-R", "ollama:ollama", blobs], check=False)
print("слои на месте")
PY

echo
echo "=== регистрация манифеста"
ollama pull "$MODEL" 2>&1 | tr '\r' '\n' | grep -v '^\s*$' | tail -3
echo
ollama list
