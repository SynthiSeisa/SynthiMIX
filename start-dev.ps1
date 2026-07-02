# YT-Downloader Dev Launcher
# Run: .\start.bat  (or: powershell -ExecutionPolicy Bypass -File .\start-dev.ps1)

$Root = $PSScriptRoot
$env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH","User")

# CRITICAL: clear or Electron runs as plain Node (no BrowserWindow/app API)
[System.Environment]::SetEnvironmentVariable("ELECTRON_RUN_AS_NODE", $null, "Process")
$env:NODE_ENV  = "development"
$env:YTDL_DEV  = "1"   # tells Electron NOT to spawn its own backend

# Kill stale backend/Vite processes to free ports
Write-Host "Beende alte Prozesse..." -ForegroundColor Yellow
Stop-Process -Name "python" -ErrorAction SilentlyContinue -Force
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess |
    ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

Write-Host "[1/3] Starting Python backend..." -ForegroundColor Cyan
# PYTHONUTF8 erzwingt UTF-8 statt CP1252 — wird vom Kind-Prozess geerbt
$env:PYTHONUTF8 = "1"
# --data-dir hält Dev-Modus-Daten (queue/history/settings) in 4_Dev-Daten,
# statt sie ungewollt zurück in den Quellcode-Ordner zu schreiben
$devDataDir = (Resolve-Path "$Root\4_Dev-Daten").Path
$backend = Start-Process python -ArgumentList "-u main.py --data-dir `"$devDataDir`"" `
    -WorkingDirectory "$Root\backend" -NoNewWindow -PassThru
Start-Sleep 2

Write-Host "[2/3] Starting Vite on :5173..." -ForegroundColor Cyan
$vite = Start-Process node -ArgumentList "node_modules\vite\bin\vite.js" `
    -WorkingDirectory "$Root\app" -NoNewWindow -PassThru
Start-Sleep 4

Write-Host "[3/3] Launching Electron..." -ForegroundColor Cyan
$el = "$Root\node_modules\electron\dist\electron.exe"
$electron = Start-Process $el -ArgumentList "." -WorkingDirectory $Root -PassThru

Write-Host "App running. Close the Electron window to stop all processes." -ForegroundColor Green
$electron.WaitForExit()

Write-Host "Stopping backend and Vite..." -ForegroundColor Yellow
Stop-Process -Id $vite.Id    -Force -ErrorAction SilentlyContinue
Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
