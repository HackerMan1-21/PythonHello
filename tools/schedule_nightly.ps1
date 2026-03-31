<#
Create or run nightly scheduled task for full rebuild.
Run as Administrator to register task.

Usage (create):
.
  .\tools\schedule_nightly.ps1 -CreateTask

Usage (run now):
  .\tools\schedule_nightly.ps1 -RunNow
#>
param(
  [switch]$CreateTask,
  [switch]$RunNow,
  [string]$StartTime = '22:00'
)

$python = "${PWD}\.venv\Scripts\python.exe"
$script = "${PWD}\tools\full_rebuild_manager.py"

if ($CreateTask) {
  $taskName = "PythonHello_FullRebuild"
  $action = "`"$python`" `"$script`""
  Write-Host "Creating scheduled task $taskName to run at $StartTime"
  schtasks /Create /SC DAILY /TN $taskName /TR $action /ST $StartTime /F | Out-Null
  Write-Host "Task created (may require admin)."
  exit 0
}

if ($RunNow) {
  Write-Host "Running full rebuild manager now..."
  & $python $script
  exit $LASTEXITCODE
}

Write-Host "No action specified. Use -CreateTask or -RunNow."
