# Удаление автозапуска дашборда на Windows.
#
# Запускать от имени администратора:
#   powershell -ExecutionPolicy Bypass -File deploy\windows\service-remove.ps1
#
# Сервер, запущенный вручную из start.bat, этим не останавливается:
# скрипт снимает только задачу Планировщика.

$ErrorActionPreference = "Stop"
$task = "RectorDashboard"

schtasks /End /TN $task 2>$null | Out-Null
schtasks /Delete /TN $task /F 2>$null | Out-Null

$ruleName = "RectorDashboard 8765"
if ($env:RECTOR_API_PORT) { $ruleName = "RectorDashboard $($env:RECTOR_API_PORT)" }
netsh advfirewall firewall delete rule name="$ruleName" 2>$null | Out-Null

Write-Host "Задача '$task' удалена, правило брандмауэра снято." -ForegroundColor Green
Write-Host "Файлы, база и документы не тронуты."
