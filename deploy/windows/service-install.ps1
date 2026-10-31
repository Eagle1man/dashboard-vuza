# Автозапуск дашборда на Windows: задача Планировщика.
#
# Почему Планировщик, а не служба Windows: службе нужен исполняемый файл,
# а здесь — Python из .venv. Планировщик запускает .bat без сторонних
# утилит (NSSM и подобных), переживает перезагрузку и работает под
# системной учётной записью, то есть без входа пользователя в систему.
#
# Запускать от имени администратора:
#   powershell -ExecutionPolicy Bypass -File deploy\windows\service-install.ps1

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$bat = Join-Path $PSScriptRoot "run-server.bat"
$task = "RectorDashboard"
$port = "8765"
if ($env:RECTOR_API_PORT) { $port = $env:RECTOR_API_PORT }

if (-not (Test-Path (Join-Path $root ".venv\Scripts\python.exe"))) {
    Write-Host "Окружение не установлено: нет .venv\Scripts\python.exe" -ForegroundColor Red
    Write-Host "Сначала запустите install.bat в корне проекта."
    exit 1
}

# Пересоздаём задачу: повторный запуск скрипта не должен падать.
schtasks /Delete /TN $task /F 2>$null | Out-Null
schtasks /Create /TN $task /TR "`"$bat`"" /SC ONSTART /RU SYSTEM /RL HIGHEST /F | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Не удалось создать задачу Планировщика." -ForegroundColor Red
    exit 1
}

# Порт наружу: без правила брандмауэра дашборд виден только с этой машины.
$ruleName = "RectorDashboard $port"
netsh advfirewall firewall delete rule name="$ruleName" | Out-Null
netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=$port | Out-Null

schtasks /Run /TN $task | Out-Null

Write-Host ""
Write-Host "Задача '$task' создана и запущена." -ForegroundColor Green
Write-Host "  Слушает:  0.0.0.0:$port (данные после входа: RECTOR_REQUIRE_LOGIN=1)"
Write-Host "  Журнал:   $root\logs\server.log"
Write-Host "  Проверка: .venv\Scripts\python.exe -m backend.smoke"
Write-Host "  Адрес:    http://<ip-этой-машины>:$port/"
Write-Host ""
Write-Host "Удалить автозапуск: deploy\windows\service-remove.ps1"
