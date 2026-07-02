# SynthiMIX — Build-Skript
# Erzeugt einen Windows-Installer (.exe) der kein Python oder Node.js benötigt.
# Voraussetzungen: Python 3.11+, Node.js 20+, pip install pyinstaller

param(
    [switch]$SkipBackend,   # -SkipBackend → PyInstaller-Schritt überspringen
    [switch]$SkipFrontend   # -SkipFrontend → Vite-Build überspringen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "    FEHLER: $msg" -ForegroundColor Red; exit 1 }

# ── 1. Python-Backend mit PyInstaller bündeln ────────────────────────────────
if (-not $SkipBackend) {
    Step "Backend mit PyInstaller bündeln..."
    $specFile = "$root\backend\backend.spec"
    if (-not (Test-Path $specFile)) { Fail "backend.spec nicht gefunden" }

    $pyCheck = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "Python nicht gefunden. Bitte Python 3.11+ installieren." }
    Ok $pyCheck

    $piCheck = pyinstaller --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        Step "PyInstaller nicht gefunden, wird installiert..."
        pip install pyinstaller
    }

    Remove-Item "$root\backend\dist" -Recurse -Force -ErrorAction SilentlyContinue
    Push-Location "$root\backend"
    pip install -r requirements.txt --quiet
    pyinstaller backend.spec --distpath dist --workpath build --noconfirm
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "PyInstaller fehlgeschlagen" }
    Pop-Location
    Ok "Backend gebündelt → backend\dist\backend\"
}

# ── 2. Svelte-Frontend bauen ─────────────────────────────────────────────────
if (-not $SkipFrontend) {
    Step "Svelte-Frontend bauen..."
    Push-Location "$root\app"
    if (-not (Test-Path "node_modules")) {
        npm install
        if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install fehlgeschlagen" }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Vite-Build fehlgeschlagen" }
    Pop-Location
    Ok "Frontend gebaut → app\dist\"
}

# ── 3. electron-builder — Windows Installer ──────────────────────────────────
Step "Electron-Installer bauen..."
Push-Location $root

if (-not (Test-Path "node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm install fehlgeschlagen" }
}

# Platzhalter-Icon falls keins vorhanden
$iconDir = "$root\build-resources"
if (-not (Test-Path "$iconDir\icon.ico")) {
    New-Item -ItemType Directory -Force $iconDir | Out-Null
    # Kopiere ein Standard-Icon oder überspringe (electron-builder nutzt dann Standard)
    Write-Host "    Hinweis: Kein Icon gefunden ($iconDir\icon.ico). Standard-Icon wird verwendet." -ForegroundColor Yellow
    # Entferne Icon-Referenz temporär aus package.json wenn nicht vorhanden
}

npx electron-builder --win --x64
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "electron-builder fehlgeschlagen" }
Pop-Location

# ── 4. Installer + portable Build in die Projektordner veröffentlichen ──────
$projectRoot   = Split-Path $root -Parent
$installerDest = "$projectRoot\1_Installer"
$portableDest  = "$projectRoot\2_Portable-App"

$installer = Get-ChildItem "$root\dist-installer\*.exe" | Select-Object -First 1
if ($installer -and (Test-Path $installerDest)) {
    Copy-Item "$root\dist-installer\*.exe" $installerDest -Force
    Copy-Item "$root\dist-installer\*.exe.blockmap" $installerDest -Force -ErrorAction SilentlyContinue
    Ok "Installer veröffentlicht → $installerDest\$($installer.Name)"
}
if ((Test-Path "$root\dist-installer\win-unpacked") -and (Test-Path $portableDest)) {
    Remove-Item "$portableDest\*" -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "$root\dist-installer\win-unpacked\*" $portableDest -Recurse -Force
    Ok "Portable App aktualisiert → $portableDest"
}

Step "Fertig!"
if ($installer) {
    Write-Host "`n  Dieser Installer kann auf jedem Windows-PC ohne Python oder Node.js installiert werden.`n" -ForegroundColor White
} else {
    Write-Host "    Installer wurde erstellt in: $root\dist-installer\" -ForegroundColor Green
}
